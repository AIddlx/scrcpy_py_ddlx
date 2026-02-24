"""
Separated GUI Preview Process for scrcpy-py-ddlx.

This module provides a preview window that runs in a separate process,
avoiding conflicts with the HTTP MCP server's async event loop.

Uses OpenGL for GPU-accelerated rendering.

Usage:
    from scrcpy_py_ddlx.preview_process import PreviewManager

    # Start preview
    manager = PreviewManager()
    manager.start(frame_queue, device_name, width, height)

    # Stop preview
    manager.stop()
"""

import multiprocessing as mp
import numpy as np
import logging
import time
import sys
import ctypes
import socket
import platform
from typing import Optional, Tuple, Callable
from pathlib import Path
from collections import deque

logger = logging.getLogger(__name__)


# =============================================================================
# 跨进程事件通知器 (Cross-Process Event Notifiers)
# =============================================================================

class FrameNotifierBase:
    """跨进程帧通知器基类。"""

    def notify(self) -> None:
        """发送帧就绪通知。"""
        raise NotImplementedError

    def get_child_handle(self):
        """获取传递给子进程的句柄。"""
        raise NotImplementedError

    def close(self) -> None:
        """清理资源。"""
        pass


class LocalSocketNotifier(FrameNotifierBase):
    """
    使用 QLocalServer/QLocalSocket 的跨进程通知器（跨平台）。

    原理：
    - 父进程创建 QLocalServer 监听
    - 子进程创建 QLocalSocket 连接
    - 父进程写入 SHM 后，通过 socket 发送通知
    - 子进程收到 readyRead 信号，读取 SHM

    性能：Windows 命名管道 / Linux Unix socket
    """

    def __init__(self):
        from PySide6.QtNetwork import QLocalServer
        from PySide6.QtCore import QCoreApplication

        # 确保 QCoreApplication 存在（用于 QLocalServer）
        app = QCoreApplication.instance()
        self._owns_app = app is None
        if app is None:
            app = QCoreApplication([])

        # 创建唯一的服务器名称
        import os
        self._server_name = f"scrcpy_preview_{os.getpid()}_{int(time.time()*1000)}"

        # 移除旧的服务器（如果存在）
        QLocalServer.removeServer(self._server_name)

        # 创建服务器
        self._server = QLocalServer()
        self._server.setSocketOptions(QLocalServer.UserAccessOption)

        if not self._server.listen(self._server_name):
            raise RuntimeError(f"QLocalServer listen failed: {self._server.errorString()}")

        self._client_socket = None

        logger.info(f"[NOTIFIER] LocalSocket notifier created: {self._server_name}")

    def accept_connection(self, timeout_ms: int = 5000) -> bool:
        """等待子进程连接（阻塞调用）。"""
        if self._server.waitForNewConnection(timeout_ms):
            self._client_socket = self._server.nextPendingConnection()
            logger.info(f"[NOTIFIER] Client connected: {self._client_socket is not None}")
            return self._client_socket is not None
        else:
            logger.warning(f"[NOTIFIER] waitForNewConnection timeout")
            return False

    _notify_count = 0

    def notify(self) -> None:
        """发送帧就绪通知。"""
        LocalSocketNotifier._notify_count += 1
        if self._client_socket is None:
            logger.warning(f"[NOTIFIER] #{LocalSocketNotifier._notify_count} No client socket")
            return
        try:
            written = self._client_socket.write(b'1')
            self._client_socket.flush()
            # 等待数据写入完成（关键：因为没有事件循环）
            if written > 0:
                self._client_socket.waitForBytesWritten(100)
            if LocalSocketNotifier._notify_count <= 5:
                logger.info(f"[NOTIFIER] #{LocalSocketNotifier._notify_count} sent, written={written}")
        except Exception as e:
            logger.warning(f"[NOTIFIER] #{LocalSocketNotifier._notify_count} failed: {e}")

    def get_child_handle(self) -> str:
        """返回服务器名称（子进程用名称连接）。"""
        return self._server_name

    def close(self) -> None:
        """关闭服务器和 socket。"""
        try:
            if self._client_socket:
                self._client_socket.disconnectFromServer()
        except:
            pass
        try:
            self._server.close()
            from PySide6.QtNetwork import QLocalServer
            QLocalServer.removeServer(self._server_name)
        except:
            pass


class SocketPairNotifier(FrameNotifierBase):
    """
    使用 socketpair 的跨进程通知器 (Linux/macOS)。

    原理：
    - 父进程写入 SHM 后，发送 1 字节通知
    - 子进程使用 QSocketNotifier 监听，收到通知后读取 SHM

    注意：在 Windows 上 QSocketNotifier 可能不工作，建议使用 LocalSocketNotifier
    """

    def __init__(self):
        # 创建 socket pair
        # Windows 使用 AF_INET (TCP loopback)，Unix 使用 AF_UNIX
        if platform.system() == "Windows":
            self._parent_sock, self._child_sock = socket.socketpair(
                socket.AF_INET, socket.SOCK_STREAM
            )
        else:
            self._parent_sock, self._child_sock = socket.socketpair(
                socket.AF_UNIX, socket.SOCK_STREAM
            )

        # 设置非阻塞（避免 send 阻塞）
        self._parent_sock.setblocking(False)
        self._child_sock.setblocking(False)

        logger.info("[NOTIFIER] SocketPair notifier created")

    def notify(self) -> None:
        """发送帧就绪通知（非阻塞）。"""
        try:
            self._parent_sock.send(b'1')
        except BlockingIOError:
            # 缓冲区满，子进程还在处理上一帧
            pass
        except Exception as e:
            logger.warning(f"[NOTIFIER] Notify failed: {e}")

    def get_child_handle(self):
        """返回子进程 socket（multiprocessing 会自动处理传递）。"""
        return self._child_sock

    def get_child_fileno(self) -> int:
        """返回子进程 socket 的文件描述符。"""
        return self._child_sock.fileno()

    def close(self) -> None:
        """关闭 socket。"""
        try:
            self._parent_sock.close()
            self._child_sock.close()
        except:
            pass


class Win32EventNotifier(FrameNotifierBase):
    """
    使用 Win32 Event 的跨进程通知器 (Windows only)。

    原理：
    - 创建命名 Event
    - 父进程写入 SHM 后，SetEvent()
    - 子进程使用 QWinEventNotifier 监听，收到通知后读取 SHM

    性能：延迟 ~0.3μs（比 socketpair 快约 30 倍）
    """

    def __init__(self):
        if platform.system() != "Windows":
            raise RuntimeError("Win32EventNotifier only works on Windows")

        try:
            import win32event
            import win32api

            # 创建唯一命名的 Event
            # 使用进程 ID 确保唯一性
            self._event_name = f"Global\\ScrcpyFrameReady_{win32api.GetCurrentProcessId()}_{int(time.time()*1000)}"

            # 创建手动重置 Event
            self._event = win32event.CreateEvent(None, False, False, self._event_name)
            self._event_handle = int(self._event)

            logger.info(f"[NOTIFIER] Win32 Event notifier created: {self._event_name}")

        except ImportError:
            raise RuntimeError("pywin32 required for Win32EventNotifier. Install with: pip install pywin32")

    def notify(self) -> None:
        """发送帧就绪通知。"""
        try:
            import win32event
            win32event.SetEvent(self._event)
        except Exception as e:
            logger.warning(f"[NOTIFIER] SetEvent failed: {e}")

    def get_child_handle(self) -> str:
        """返回 Event 名称（子进程用名称打开）。"""
        return self._event_name

    def get_event_handle(self) -> int:
        """返回 Event 句柄。"""
        return self._event_handle

    def close(self) -> None:
        """关闭 Event 句柄。"""
        try:
            import win32api
            win32api.CloseHandle(self._event)
        except:
            pass


def create_frame_notifier() -> FrameNotifierBase:
    """
    创建平台最优的帧通知器。

    - Windows: LocalSocketNotifier (Named Pipes, Qt 事件驱动)
    - Linux/macOS: SocketPairNotifier (Unix socket, QSocketNotifier)
    """
    if platform.system() == "Windows":
        try:
            return LocalSocketNotifier()
        except Exception as e:
            logger.warning(f"[NOTIFIER] LocalSocketNotifier unavailable, falling back to socketpair: {e}")
            return SocketPairNotifier()
    else:
        return SocketPairNotifier()


class PreviewLatencyTracker:
    """
    Lightweight latency tracker for preview process.

    Tracks:
    - SHM_READ time (from main process write to preview read)
    - RENDER time (from read to paintGL complete)
    - Total preview latency
    """

    def __init__(self, history_size: int = 100, log_interval: int = 60):
        self._shm_read_times: deque = deque(maxlen=history_size)
        self._render_times: deque = deque(maxlen=history_size)
        self._total_preview_times: deque = deque(maxlen=history_size)
        self._frame_count = 0
        self._log_interval = log_interval

        # Per-frame timing
        self._frame_start_times: dict = {}  # packet_id -> read_start_time

    def record_shm_read(self, packet_id: int, read_time_ms: float):
        """Record SHM read time and start tracking for this frame."""
        self._shm_read_times.append(read_time_ms)
        self._frame_start_times[packet_id] = time.time()

    def record_render(self, packet_id: int, render_time_ms: float):
        """Record render time and calculate total preview latency."""
        self._render_times.append(render_time_ms)

        # Calculate total preview latency (read + render)
        if packet_id in self._frame_start_times:
            total_time = (time.time() - self._frame_start_times[packet_id]) * 1000
            self._total_preview_times.append(total_time)
            del self._frame_start_times[packet_id]

        self._frame_count += 1

        if self._frame_count % self._log_interval == 0:
            self._log_stats()

    def _log_stats(self):
        """Log latency statistics."""
        lines = ["[PREVIEW_LATENCY] Preview process latency analysis:"]

        if self._shm_read_times:
            avg = sum(self._shm_read_times) / len(self._shm_read_times)
            max_v = max(self._shm_read_times)
            lines.append(f"  SHM_READ:   avg={avg:.2f}ms, max={max_v:.2f}ms")

        if self._render_times:
            avg = sum(self._render_times) / len(self._render_times)
            max_v = max(self._render_times)
            lines.append(f"  RENDER:     avg={avg:.2f}ms, max={max_v:.2f}ms")

        if self._total_preview_times:
            avg = sum(self._total_preview_times) / len(self._total_preview_times)
            max_v = max(self._total_preview_times)
            min_v = min(self._total_preview_times)
            lines.append(f"  TOTAL_PREV: avg={avg:.2f}ms, min={min_v:.2f}ms, max={max_v:.2f}ms")

        lines.append(f"  Frames: {self._frame_count}")

        logger.info("\n".join(lines))


# Global preview latency tracker
_preview_tracker: Optional[PreviewLatencyTracker] = None


def get_preview_tracker() -> PreviewLatencyTracker:
    """Get or create the preview latency tracker."""
    global _preview_tracker
    if _preview_tracker is None:
        _preview_tracker = PreviewLatencyTracker()
    return _preview_tracker


def preview_window_process(frame_queue: mp.Queue,
                            control_queue: mp.Queue,
                            device_name: str,
                            width: int,
                            height: int,
                            stop_event: mp.Event,
                            shared_mem_info: Optional[dict] = None,
                            ready_event: Optional[mp.Event] = None,
                            notifier_handle = None):
    """
    Preview window process main function.

    This runs in a separate process with its own Qt event loop.
    Uses OpenGL for GPU-accelerated rendering.

    Writes logs to separate file: logs/preview_YYYYMMDD_HHMMSS.log

    Args:
        frame_queue: Queue to receive video frames (numpy arrays, fallback)
        control_queue: Queue to send control events (touch, key, etc.)
        device_name: Device name for window title
        width: Video width
        height: Video height
        stop_event: Event to signal process to stop
        shared_mem_info: Shared memory info dict for low-latency frame transfer
        ready_event: Event to signal when preview window is ready
        notifier_handle: Handle for event-driven notifications (socket or event name)
    """
    # Configure logging for this process - write to separate file
    from datetime import datetime
    # Log directory is at project_root/logs (same level as scrcpy_py_ddlx package)
    # __file__ = scrcpy_py_ddlx/preview_process.py
    # parent = scrcpy_py_ddlx, parent.parent = project root
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"preview_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    # Reset logging configuration for this process
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)  # Also print to console
        ]
    )
    logger = logging.getLogger(__name__)
    logger.info(f"Preview process logging to: {log_file}")

    # Connect to shared memory if available
    shared_mem_reader = None
    if shared_mem_info is not None:
        try:
            from scrcpy_py_ddlx.simple_shm import SimpleSHMReader
            shared_mem_reader = SimpleSHMReader(
                name=shared_mem_info['name'],
                size=shared_mem_info['size'],
                max_width=shared_mem_info.get('max_width', 1920),
                max_height=shared_mem_info.get('max_height', 4096),
                channels=shared_mem_info.get('channels', 3)
            )
            logger.info(f"[Preview] Connected to simple shared memory: {shared_mem_info['name']}")
        except Exception as e:
            logger.warning(f"[Preview] Failed to connect to shared memory: {e}")
            import traceback
            traceback.print_exc()
            shared_mem_reader = None

    try:
        from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtGui import QKeyEvent, QMouseEvent, QSurfaceFormat
        from PySide6.QtOpenGL import QOpenGLWindow
    except ImportError as e:
        logger.error(f"PySide6 not available: {e}")
        return

    # Try to import OpenGL
    try:
        from OpenGL.GL import (
            glGenTextures, glBindTexture, glTexParameteri,
            glTexImage2D, glTexSubImage2D, glClearColor, glClear,
            glViewport, glMatrixMode, glLoadIdentity,
            glOrtho, glEnable, glDisable,
            glColor3f, glBegin, glEnd,
            glTexCoord2f, glVertex2f,
            glPixelStorei,
            GL_UNSIGNED_BYTE, GL_RGB, GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER,
            GL_TEXTURE_MAG_FILTER, GL_LINEAR, GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T,
            GL_CLAMP_TO_EDGE, GL_COLOR_BUFFER_BIT, GL_QUADS,
            GL_PROJECTION, GL_MODELVIEW, GL_UNPACK_ALIGNMENT
        )
        OPENGL_AVAILABLE = True
        logger.info("OpenGL available for GPU NV12 rendering")
    except ImportError as e:
        logger.warning(f"OpenGL not available: {e}, falling back to CPU rendering")
        logger.warning("CPU mode: NOT recommended for >2Mbps or >30fps due to GIL contention")
        OPENGL_AVAILABLE = False

    if OPENGL_AVAILABLE:
        # OpenGL-based preview widget with NV12 GPU rendering support
        # Using QOpenGLWindow for lower CPU usage (~0.5% vs ~6.6% for QOpenGLWidget on Windows)
        class OpenGLPreviewWidget(QOpenGLWindow):
            """OpenGL window to display video frames with GPU acceleration.

            Note: QOpenGLWindow is a QWindow, not a QWidget. Use
            QWidget.createWindowContainer() to embed it in a widget hierarchy.
            """

            # NV12 YUV shader sources - using 2 textures (Y + UV) for better performance
            # This avoids CPU-based U/V separation
            NV12_VERTEX_SHADER = """
            varying highp vec2 v_texCoord;
            void main() {
                gl_Position = gl_ModelViewProjectionMatrix * gl_Vertex;
                v_texCoord = gl_MultiTexCoord0.xy;
            }
            """

            NV12_FRAGMENT_SHADER = """
            varying highp vec2 v_texCoord;
            uniform sampler2D y_texture;
            uniform sampler2D uv_texture;
            void main() {
                // Y texture: full resolution
                mediump float y = texture2D(y_texture, v_texCoord).r;

                // UV texture: interleaved U/V at half resolution
                // GL_LUMINANCE_ALPHA: .r = U, .a = V
                mediump vec2 uv = texture2D(uv_texture, v_texCoord).ra;
                mediump float u = uv.x - 0.5;
                mediump float v = uv.y - 0.5;

                // BT.601 YUV to RGB conversion
                highp float r = y + 1.402 * v;
                highp float g = y - 0.344136 * u - 0.714136 * v;
                highp float b = y + 1.772 * u;
                gl_FragColor = vec4(r, g, b, 1.0);
            }
            """

            def __init__(self, parent=None):
                super().__init__(parent)
                self._frame = None
                self._frame_format = 0  # 0=RGB24, 1=NV12
                self._device_size = (width, height)
                self._texture_id = None
                self._y_texture_id = None  # For NV12 Y plane
                self._uv_texture_id = None  # For NV12 UV plane
                self._texture_width = 0
                self._texture_height = 0
                # QOpenGLWindow uses QSize for setMinimumSize
                from PySide6.QtCore import QSize
                self.setMinimumSize(QSize(200, 200))

                # Shader program for NV12 (using Qt's QOpenGLShaderProgram)
                self._nv12_shader = None
                self._nv12_vbo = None
                self._nv12_initialized = False

                # PBO (Pixel Buffer Object) for async texture upload
                self._pbo_y_ids = None  # Double buffer for Y plane
                self._pbo_uv_ids = None  # Double buffer for UV plane
                self._pbo_index = 0  # Current PBO index (0 or 1)
                self._pbo_size = (0, 0)  # Track PBO allocated size

                # Touch tracking for swipe
                self._touch_start = None
                self._touch_current = None
                self._is_swiping = False

                # Configure OpenGL format for low latency
                fmt = QSurfaceFormat()
                fmt.setSwapInterval(0)  # Disable V-Sync for lower latency
                fmt.setDepthBufferSize(0)  # No depth buffer needed for 2D
                fmt.setStencilBufferSize(0)  # No stencil buffer needed
                fmt.setAlphaBufferSize(0)  # No alpha needed
                fmt.setSamples(0)  # No multisampling for performance
                self.setFormat(fmt)

                # Note: QOpenGLWindow (QWindow) doesn't have setFocusPolicy or setMouseTracking
                # Focus and mouse tracking are handled by the container widget

                # Performance tracking
                self._paint_count = 0
                self._last_paint_time = 0
                self._last_update_call_time = 0

            def set_device_size(self, w: int, h: int):
                self._device_size = (w, h)

            def update_frame(self, frame: np.ndarray, frame_count: int = 0, frame_format: int = 0):
                """Update displayed frame from numpy array."""
                update_start = time.time()
                self._frame = frame
                self._frame_format = frame_format
                self._last_update_time = update_start
                if frame_count <= 5 or frame_count % 60 == 0:
                    format_str = "NV12" if frame_format == 1 else "RGB24"
                    logger.info(f"[WIDGET_UPDATE] Frame #{frame_count} ({format_str}): update_frame() took {(time.time() - update_start)*1000:.2f}ms")

            def _get_device_coords(self, x, y):
                """Convert widget coordinates to device coordinates."""
                # Use _device_size instead of _frame.shape for coordinate conversion
                # This ensures correct coordinates even during rotation transition
                w, h = self._device_size
                if w == 0 or h == 0:
                    logger.warning(f"Invalid device size: {w}x{h}")
                    return None, None
                widget_w = self.width()
                widget_h = self.height()
                scale = min(widget_w / w, widget_h / h)
                img_w = int(w * scale)
                img_h = int(h * scale)
                offset_x = (widget_w - img_w) // 2
                offset_y = (widget_h - img_h) // 2

                # Debug: log coordinate conversion for troubleshooting
                logger.debug(f"Coord conv: widget=({widget_w}x{widget_h}), device=({w}x{h}), scale={scale:.3f}, click=({x},{y})")

                if offset_x <= x <= offset_x + img_w and offset_y <= y <= offset_y + img_h:
                    device_x = int((x - offset_x) / scale)
                    device_y = int((y - offset_y) / scale)
                    logger.debug(f"Device coords: ({device_x}, {device_y})")
                    return device_x, device_y
                logger.debug(f"Click outside image area: offset=({offset_x},{offset_y}), img=({img_w}x{img_h})")
                return None, None

            def _init_nv12_shader(self):
                """Initialize shader program for NV12 rendering using Qt's QOpenGLShaderProgram."""
                try:
                    from PySide6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram
                except ImportError:
                    # Fallback for older PySide6 versions
                    try:
                        from PySide6.QtGui import QOpenGLShader, QOpenGLShaderProgram
                    except ImportError:
                        logger.warning("QOpenGLShader not available, GPU NV12 rendering disabled")
                        self._nv12_shader = None
                        return False

                self._nv12_shader = QOpenGLShaderProgram(self)

                # Compile vertex shader
                if not self._nv12_shader.addShaderFromSourceCode(QOpenGLShader.Vertex, self.NV12_VERTEX_SHADER):
                    logger.error(f"NV12 vertex shader compile error: {self._nv12_shader.log()}")
                    self._nv12_shader = None
                    return False

                # Compile fragment shader
                if not self._nv12_shader.addShaderFromSourceCode(QOpenGLShader.Fragment, self.NV12_FRAGMENT_SHADER):
                    logger.error(f"NV12 fragment shader compile error: {self._nv12_shader.log()}")
                    self._nv12_shader = None
                    return False

                # Link shader program
                if not self._nv12_shader.link():
                    logger.error(f"NV12 shader link error: {self._nv12_shader.log()}")
                    self._nv12_shader = None
                    return False

                # Create VBO for fullscreen quad
                from OpenGL.GL import glGenBuffers, glBindBuffer, glBufferData, GL_ARRAY_BUFFER, GL_STATIC_DRAW
                # Vertex data: position (x, y) and texcoord (u, v)
                # Normalized coordinates (-1 to 1) for position, (0 to 1) for texcoord
                vertices = np.array([
                    # Position    TexCoord
                    -1.0, -1.0,   0.0, 1.0,  # Bottom-left (OpenGL Y is up, texture Y is down)
                     1.0, -1.0,   1.0, 1.0,  # Bottom-right
                     1.0,  1.0,   1.0, 0.0,  # Top-right
                    -1.0,  1.0,   0.0, 0.0,  # Top-left
                ], dtype=np.float32)

                self._nv12_vbo = glGenBuffers(1)
                glBindBuffer(GL_ARRAY_BUFFER, self._nv12_vbo)
                glBufferData(GL_ARRAY_BUFFER, vertices.nbytes, vertices.tobytes(), GL_STATIC_DRAW)
                glBindBuffer(GL_ARRAY_BUFFER, 0)

                self._nv12_initialized = True
                logger.info("NV12 GPU shader initialized successfully")
                return True

            def initializeGL(self):
                """Initialize OpenGL resources."""
                from OpenGL.GL import glGenBuffers, glBindBuffer, glBufferData, GL_ARRAY_BUFFER, GL_STATIC_DRAW

                # CRITICAL: Set pixel unpack alignment to 1 byte
                # This fixes texture corruption when width is not multiple of 4
                glPixelStorei(GL_UNPACK_ALIGNMENT, 1)

                # Initialize RGB texture
                self._texture_id = glGenTextures(1)
                glBindTexture(GL_TEXTURE_2D, self._texture_id)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

                # Initialize NV12 textures (Y and UV planes)
                self._y_texture_id = glGenTextures(1)
                glBindTexture(GL_TEXTURE_2D, self._y_texture_id)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

                self._uv_texture_id = glGenTextures(1)
                glBindTexture(GL_TEXTURE_2D, self._uv_texture_id)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

                # Initialize PBOs for async texture upload (double buffering)
                try:
                    self._pbo_y_ids = glGenBuffers(2)
                    self._pbo_uv_ids = glGenBuffers(2)
                    logger.info("PBO (Pixel Buffer Object) initialized for async texture upload")
                except Exception as e:
                    logger.warning(f"PBO not available: {e}, using sync texture upload")
                    self._pbo_y_ids = None
                    self._pbo_uv_ids = None

                glClearColor(0.0, 0.0, 0.0, 1.0)

                # Initialize NV12 shader
                try:
                    self._init_nv12_shader()
                except Exception as e:
                    logger.warning(f"Failed to initialize NV12 shader: {e}")
                    self._nv12_shader = None

                logger.info("OpenGL preview initialized (RGB + NV12 GPU support)")

            def resizeGL(self, w: int, h: int):
                """Handle window resize."""
                try:
                    glViewport(0, 0, w, h)
                except Exception as e:
                    # Ignore OpenGL errors during resize (context may not be current)
                    pass

            def _paint_nv12_gpu(self, nv12_data: np.ndarray, w: int, h: int):
                """Render NV12 frame using GPU shader with PBO async texture upload."""
                from OpenGL.GL import (
                    glActiveTexture, GL_TEXTURE0, GL_TEXTURE1,
                    glBindTexture, glTexImage2D, glTexSubImage2D, GL_TEXTURE_2D, GL_UNSIGNED_BYTE,
                    glGetError, GL_NO_ERROR, GL_LUMINANCE, GL_LUMINANCE_ALPHA,
                    glPixelStorei, GL_UNPACK_ALIGNMENT,
                    glBindBuffer, glBufferData, glMapBufferRange, glUnmapBuffer,
                    GL_PIXEL_UNPACK_BUFFER, GL_STREAM_DRAW,
                    GL_MAP_WRITE_BIT, GL_MAP_INVALIDATE_BUFFER_BIT, GL_MAP_UNSYNCHRONIZED_BIT
                )
                from ctypes import c_void_p, c_char, cast, POINTER

                # CRITICAL: Set pixel unpack alignment to 1 byte for NV12 textures
                glPixelStorei(GL_UNPACK_ALIGNMENT, 1)

                # Set up orthographic projection for pixel coordinates
                widget_w = self.width()
                widget_h = self.height()
                glMatrixMode(GL_PROJECTION)
                glLoadIdentity()
                glOrtho(0, widget_w, widget_h, 0, -1, 1)
                glMatrixMode(GL_MODELVIEW)
                glLoadIdentity()

                y_size = w * h
                uv_size = (w // 2) * (h // 2) * 2
                expected_size = y_size + uv_size

                if len(nv12_data) < expected_size:
                    logger.error(f"NV12 data too small: {len(nv12_data)} < {expected_size}")
                    return False

                # Extract Y and UV planes (direct view, no copy)
                y_plane = nv12_data[:y_size]
                uv_plane = nv12_data[y_size:expected_size]

                # Check if PBO is available and size matches
                use_pbo = self._pbo_y_ids is not None
                size_changed = not hasattr(self, '_nv12_tex_size') or self._nv12_tex_size != (w, h)
                first_frame = not hasattr(self, '_pbo_has_data') or not self._pbo_has_data

                if size_changed:
                    self._nv12_tex_size = (w, h)

                    # Allocate texture memory
                    glActiveTexture(GL_TEXTURE0)
                    glBindTexture(GL_TEXTURE_2D, self._y_texture_id)
                    glTexImage2D(GL_TEXTURE_2D, 0, GL_LUMINANCE, w, h, 0, GL_LUMINANCE, GL_UNSIGNED_BYTE, None)

                    glActiveTexture(GL_TEXTURE1)
                    glBindTexture(GL_TEXTURE_2D, self._uv_texture_id)
                    glTexImage2D(GL_TEXTURE_2D, 0, GL_LUMINANCE_ALPHA, w // 2, h // 2, 0, GL_LUMINANCE_ALPHA, GL_UNSIGNED_BYTE, None)

                    # Allocate PBO memory if available
                    if use_pbo:
                        for i in range(2):
                            glBindBuffer(GL_PIXEL_UNPACK_BUFFER, self._pbo_y_ids[i])
                            glBufferData(GL_PIXEL_UNPACK_BUFFER, y_size, None, GL_STREAM_DRAW)
                            glBindBuffer(GL_PIXEL_UNPACK_BUFFER, self._pbo_uv_ids[i])
                            glBufferData(GL_PIXEL_UNPACK_BUFFER, uv_size, None, GL_STREAM_DRAW)
                        glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0)
                        self._pbo_has_data = False  # Reset on size change
                        logger.info(f"PBO allocated: Y={y_size}B, UV={uv_size}B")

                if use_pbo:
                    import ctypes
                    idx = self._pbo_index
                    next_idx = 1 - idx

                    if first_frame:
                        # First frame: write data to PBO first, then upload
                        glBindBuffer(GL_PIXEL_UNPACK_BUFFER, self._pbo_y_ids[idx])
                        ptr = glMapBufferRange(GL_PIXEL_UNPACK_BUFFER, 0, y_size,
                                              GL_MAP_WRITE_BIT | GL_MAP_INVALIDATE_BUFFER_BIT)
                        if ptr:
                            ctypes.memmove(ptr, y_plane.ctypes.data_as(c_void_p), y_size)
                            glUnmapBuffer(GL_PIXEL_UNPACK_BUFFER)

                        glBindBuffer(GL_PIXEL_UNPACK_BUFFER, self._pbo_uv_ids[idx])
                        ptr = glMapBufferRange(GL_PIXEL_UNPACK_BUFFER, 0, uv_size,
                                              GL_MAP_WRITE_BIT | GL_MAP_INVALIDATE_BUFFER_BIT)
                        if ptr:
                            ctypes.memmove(ptr, uv_plane.ctypes.data_as(c_void_p), uv_size)
                            glUnmapBuffer(GL_PIXEL_UNPACK_BUFFER)

                        self._pbo_has_data = True

                    # --- Upload Y texture from PBO (GPU DMA, no CPU wait) ---
                    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, self._pbo_y_ids[idx])
                    glActiveTexture(GL_TEXTURE0)
                    glBindTexture(GL_TEXTURE_2D, self._y_texture_id)
                    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, w, h, GL_LUMINANCE, GL_UNSIGNED_BYTE, None)

                    # --- Upload UV texture from PBO (GPU DMA, no CPU wait) ---
                    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, self._pbo_uv_ids[idx])
                    glActiveTexture(GL_TEXTURE1)
                    glBindTexture(GL_TEXTURE_2D, self._uv_texture_id)
                    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, w // 2, h // 2, GL_LUMINANCE_ALPHA, GL_UNSIGNED_BYTE, None)

                    # --- Write next frame data to PBO (CPU) ---
                    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, self._pbo_y_ids[next_idx])
                    ptr = glMapBufferRange(GL_PIXEL_UNPACK_BUFFER, 0, y_size,
                                          GL_MAP_WRITE_BIT | GL_MAP_INVALIDATE_BUFFER_BIT | GL_MAP_UNSYNCHRONIZED_BIT)
                    if ptr:
                        ctypes.memmove(ptr, y_plane.ctypes.data_as(c_void_p), y_size)
                        glUnmapBuffer(GL_PIXEL_UNPACK_BUFFER)

                    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, self._pbo_uv_ids[next_idx])
                    ptr = glMapBufferRange(GL_PIXEL_UNPACK_BUFFER, 0, uv_size,
                                          GL_MAP_WRITE_BIT | GL_MAP_INVALIDATE_BUFFER_BIT | GL_MAP_UNSYNCHRONIZED_BIT)
                    if ptr:
                        ctypes.memmove(ptr, uv_plane.ctypes.data_as(c_void_p), uv_size)
                        glUnmapBuffer(GL_PIXEL_UNPACK_BUFFER)

                    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0)

                    # Swap PBO index for next frame
                    self._pbo_index = next_idx
                else:
                    # Fallback: Direct texture upload (no PBO)
                    y_ptr = y_plane.ctypes.data_as(c_void_p)
                    uv_ptr = uv_plane.ctypes.data_as(c_void_p)

                    glActiveTexture(GL_TEXTURE0)
                    glBindTexture(GL_TEXTURE_2D, self._y_texture_id)
                    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, w, h, GL_LUMINANCE, GL_UNSIGNED_BYTE, y_ptr)

                    glActiveTexture(GL_TEXTURE1)
                    glBindTexture(GL_TEXTURE_2D, self._uv_texture_id)
                    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, w // 2, h // 2, GL_LUMINANCE_ALPHA, GL_UNSIGNED_BYTE, uv_ptr)

                # Use shader program
                self._nv12_shader.bind()

                # Bind textures to uniforms (2 textures only)
                y_loc = self._nv12_shader.uniformLocation("y_texture")
                uv_loc = self._nv12_shader.uniformLocation("uv_texture")
                self._nv12_shader.setUniformValue(y_loc, 0)   # Texture unit 0
                self._nv12_shader.setUniformValue(uv_loc, 1)  # Texture unit 1

                # Calculate aspect ratio and draw position
                scale = min(widget_w / w, widget_h / h)
                img_w = int(w * scale)
                img_h = int(h * scale)
                x = (widget_w - img_w) // 2
                y = (widget_h - img_h) // 2

                # Bind textures before drawing
                glActiveTexture(GL_TEXTURE0)
                glBindTexture(GL_TEXTURE_2D, self._y_texture_id)
                glActiveTexture(GL_TEXTURE1)
                glBindTexture(GL_TEXTURE_2D, self._uv_texture_id)

                # Draw textured quad
                glEnable(GL_TEXTURE_2D)
                glColor3f(1.0, 1.0, 1.0)

                glBegin(GL_QUADS)
                glTexCoord2f(0.0, 0.0); glVertex2f(x, y)
                glTexCoord2f(1.0, 0.0); glVertex2f(x + img_w, y)
                glTexCoord2f(1.0, 1.0); glVertex2f(x + img_w, y + img_h)
                glTexCoord2f(0.0, 1.0); glVertex2f(x, y + img_h)
                glEnd()

                glDisable(GL_TEXTURE_2D)
                self._nv12_shader.release()

                err = glGetError()
                if err != GL_NO_ERROR:
                    logger.warning(f"GL error after NV12 render: {err}")
                    return False

                return True

            def _paint_nv12(self, nv12_data: np.ndarray, w: int, h: int):
                """Render NV12 frame using GPU shader ONLY."""
                if self._nv12_shader is None or not self._nv12_initialized:
                    logger.error("NV12 GPU shader not initialized! Cannot render NV12 frame.")
                    return

                try:
                    if not self._paint_nv12_gpu(nv12_data, w, h):
                        logger.error("NV12 GPU render returned False")
                except Exception as e:
                    logger.error(f"NV12 GPU render error: {e}")
                    import traceback
                    traceback.print_exc()

            def _nv12_to_rgb_cpu(self, nv12_data: np.ndarray, w: int, h: int) -> np.ndarray:
                """Convert NV12 to RGB using CPU (slow fallback)."""
                try:
                    y_size = w * h
                    expected_size = int(y_size * 1.5)

                    if len(nv12_data) < expected_size:
                        logger.warning(f"NV12 data too small: {len(nv12_data)} < {expected_size}")
                        return np.zeros((h, w, 3), dtype=np.uint8)

                    y_plane = nv12_data[:y_size].reshape((h, w)).astype(np.float32)
                    uv_plane = nv12_data[y_size:expected_size].reshape((h // 2, w))

                    # Extract U and V (interleaved in NV12 format)
                    u = uv_plane[:, 0::2].astype(np.float32)  # U at even columns
                    v = uv_plane[:, 1::2].astype(np.float32)  # V at odd columns

                    # Upsample U and V to full resolution (4:2:0 -> 4:4:4)
                    u_up = np.repeat(np.repeat(u, 2, axis=0), 2, axis=1)
                    v_up = np.repeat(np.repeat(v, 2, axis=0), 2, axis=1)

                    # Convert to RGB (BT.601 coefficients)
                    y = y_plane
                    r = y + 1.402 * (v_up - 128)
                    g = y - 0.344 * (u_up - 128) - 0.714 * (v_up - 128)
                    b = y + 1.772 * (u_up - 128)

                    # Clamp and stack
                    rgb = np.stack([
                        np.clip(r, 0, 255).astype(np.uint8),
                        np.clip(g, 0, 255).astype(np.uint8),
                        np.clip(b, 0, 255).astype(np.uint8)
                    ], axis=2)

                    return rgb
                except Exception as e:
                    logger.error(f"NV12 to RGB conversion error: {e}")
                    return np.zeros((h, w, 3), dtype=np.uint8)

            def _paint_rgb(self, rgb: np.ndarray, w: int, h: int):
                """Render RGB frame using standard OpenGL texture."""
                # Set up orthographic projection for pixel coordinates
                widget_w = self.width()
                widget_h = self.height()
                glMatrixMode(GL_PROJECTION)
                glLoadIdentity()
                glOrtho(0, widget_w, widget_h, 0, -1, 1)  # Y-axis inverted for screen coords
                glMatrixMode(GL_MODELVIEW)
                glLoadIdentity()

                # Update or create texture
                if w != self._texture_width or h != self._texture_height:
                    glBindTexture(GL_TEXTURE_2D, self._texture_id)
                    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0,
                                GL_RGB, GL_UNSIGNED_BYTE, rgb.tobytes())
                    self._texture_width = w
                    self._texture_height = h
                else:
                    glBindTexture(GL_TEXTURE_2D, self._texture_id)
                    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, w, h,
                                   GL_RGB, GL_UNSIGNED_BYTE, rgb.tobytes())

                # Calculate aspect ratio
                widget_w = self.width()
                widget_h = self.height()
                scale = min(widget_w / w, widget_h / h)
                img_w = int(w * scale)
                img_h = int(h * scale)
                x = (widget_w - img_w) // 2
                y = (widget_h - img_h) // 2

                # Draw textured quad
                glEnable(GL_TEXTURE_2D)
                glColor3f(1.0, 1.0, 1.0)
                glBegin(GL_QUADS)
                glTexCoord2f(0, 0); glVertex2f(x, y)
                glTexCoord2f(1, 0); glVertex2f(x + img_w, y)
                glTexCoord2f(1, 1); glVertex2f(x + img_w, y + img_h)
                glTexCoord2f(0, 1); glVertex2f(x, y + img_h)
                glEnd()
                glDisable(GL_TEXTURE_2D)

            def paintGL(self):
                """Render the video frame using OpenGL."""
                paint_start = time.time()
                self._paint_count += 1

                # Log paint start for NV12 debugging
                if self._paint_count <= 5:
                    logger.info(f"[PAINT_GL_START] Paint #{self._paint_count}, format={self._frame_format}")

                # Calculate time since last update() call
                update_to_paint_ms = 0
                if hasattr(self, '_last_update_call_time') and self._last_update_call_time > 0:
                    update_to_paint_ms = (paint_start - self._last_update_call_time) * 1000

                # Calculate time since last paintGL
                paint_interval_ms = 0
                if self._last_paint_time > 0:
                    paint_interval_ms = (paint_start - self._last_paint_time) * 1000

                glClear(GL_COLOR_BUFFER_BIT)

                if self._frame is not None:
                    frame_available_time = getattr(self, '_frame_available_time', paint_start)
                    frame_to_paint_ms = (paint_start - frame_available_time) * 1000

                    tex_start = time.time()

                    try:
                        if self._frame_format == 1:  # NV12 format
                            # NV12: frame is flat bytes, need dimensions from device_size
                            w, h = self._device_size
                            if self._paint_count <= 5:
                                logger.info(f"[PAINT_NV12] Painting NV12 frame, size={w}x{h}, data_len={len(self._frame)}")
                            self._paint_nv12(self._frame, w, h)
                            if self._paint_count <= 5:
                                logger.info(f"[PAINT_NV12] Done painting NV12 frame")
                        else:  # RGB24 format
                            h, w, c = self._frame.shape
                            rgb = self._frame  # Already RGB format
                            self._paint_rgb(rgb, w, h)
                    except Exception as e:
                        logger.error(f"paintGL render error: {e}")
                        import traceback
                        traceback.print_exc()

                    tex_time_ms = (time.time() - tex_start) * 1000

                    total_paint_ms = (time.time() - paint_start) * 1000

                    # Log every frame for first 10, then every 60
                    if self._paint_count <= 10 or self._paint_count % 60 == 0:
                        format_str = "NV12" if self._frame_format == 1 else "RGB24"
                        logger.info(
                            f"[PAINT_GL] Paint #{self._paint_count} ({format_str}): "
                            f"update_to_paint={update_to_paint_ms:.1f}ms, "
                            f"paint_interval={paint_interval_ms:.1f}ms, "
                            f"frame_to_paint={frame_to_paint_ms:.1f}ms, "
                            f"tex_upload={tex_time_ms:.2f}ms, "
                            f"total_paint={total_paint_ms:.2f}ms"
                        )

                self._last_paint_time = paint_start

            def mousePressEvent(self, event: QMouseEvent):
                """Handle mouse press - start touch down."""
                if event.button() == Qt.LeftButton:
                    x = event.position().x()
                    y = event.position().y()
                    device_x, device_y = self._get_device_coords(x, y)
                    if device_x is not None:
                        self._touch_start = (device_x, device_y)
                        self._touch_current = (device_x, device_y)
                        self._is_swiping = False
                        # Send touch DOWN event immediately
                        try:
                            control_queue.put(('touch_down', device_x, device_y), timeout=0.1)
                            logger.debug(f"[TOUCH] Down at ({device_x}, {device_y})")
                        except Exception as e:
                            logger.warning(f"[TOUCH] Failed to send touch_down: {e}")

            def mouseMoveEvent(self, event: QMouseEvent):
                """Handle mouse move - send touch move for real-time tracking."""
                if self._touch_start is not None:
                    x = event.position().x()
                    y = event.position().y()
                    device_x, device_y = self._get_device_coords(x, y)
                    if device_x is not None:
                        self._touch_current = (device_x, device_y)
                        # Mark as swiping if moved significantly
                        dx = abs(device_x - self._touch_start[0])
                        dy = abs(device_y - self._touch_start[1])
                        if dx > 10 or dy > 10:
                            self._is_swiping = True
                        # Send touch MOVE event for real-time tracking
                        try:
                            control_queue.put(('touch_move', device_x, device_y), timeout=0.05)
                        except Exception as e:
                            logger.warning(f"[TOUCH] Failed to send touch_move: {e}")

            def mouseReleaseEvent(self, event: QMouseEvent):
                """Handle mouse release - send touch up."""
                if event.button() == Qt.LeftButton and self._touch_start is not None:
                    try:
                        # Always send touch UP event
                        x, y = self._touch_current if self._touch_current else self._touch_start
                        control_queue.put(('touch_up', x, y), timeout=0.1)
                        logger.debug(f"[TOUCH] Up at ({x}, {y})")
                    except Exception as e:
                        logger.warning(f"[TOUCH] Failed to send touch_up: {e}")
                    finally:
                        self._touch_start = None
                        self._touch_current = None
                        self._is_swiping = False

            def keyPressEvent(self, event: QKeyEvent):
                """Handle key press for control events."""
                key = event.key()
                key_map = {
                    Qt.Key_Back: 'back',
                    Qt.Key_Home: 'home',
                    Qt.Key_Menu: 'menu',
                    Qt.Key_Enter: 'enter',
                    Qt.Key_Return: 'enter',
                    Qt.Key_Escape: 'back',
                }
                action = key_map.get(key)
                if action:
                    try:
                        control_queue.put(('key', action), timeout=0.1)
                        logger.debug(f"[KEY] {action}")
                    except Exception as e:
                        logger.warning(f"[KEY] Failed to send key: {e}")

            def wheelEvent(self, event):
                """Handle mouse wheel for scroll."""
                if self._frame is not None:
                    x = event.position().x()
                    y = event.position().y()
                    device_x, device_y = self._get_device_coords(x, y)
                    if device_x is not None:
                        delta = event.angleDelta().y() / 120.0
                        vscroll = -delta * 0.5
                        try:
                            control_queue.put(('scroll', device_x, device_y, 0.0, vscroll), timeout=0.1)
                        except Exception as e:
                            logger.warning(f"[TOUCH] Failed to send scroll: {e}")

        PreviewWidget = OpenGLPreviewWidget
    else:
        # Fallback to CPU rendering
        from PySide6.QtWidgets import QWidget
        from PySide6.QtGui import QImage, QPixmap, QPainter

        class CPUPreviewWidget(QWidget):
            """CPU-based widget to display video frames (fallback)."""

            def __init__(self, parent=None):
                super().__init__(parent)
                self._frame = None
                self._device_size = (width, height)
                self.setMinimumSize(200, 200)

                # Touch tracking for swipe
                self._touch_start = None
                self._touch_current = None
                self._is_swiping = False
                self._press_timer = None

                self.setMouseTracking(True)

            def set_device_size(self, w: int, h: int):
                self._device_size = (w, h)

            def update_frame(self, frame: np.ndarray):
                self._frame = frame
                # Sync device size from frame
                if hasattr(frame, 'shape') and len(frame.shape) >= 2:
                    h, w = frame.shape[:2]
                    self._device_size = (w, h)
                # Note: Don't call update() here - caller will call repaint() for immediate render

            def _get_device_coords(self, x, y):
                """Convert widget coordinates to device coordinates."""
                # Use _device_size instead of _frame.shape for coordinate conversion
                # This ensures correct coordinates even during rotation transition
                w, h = self._device_size
                if w == 0 or h == 0:
                    logger.warning(f"Invalid device size: {w}x{h}")
                    return None, None
                widget_size = self.size()
                scale = min(widget_size.width() / w, widget_size.height() / h)
                img_w = int(w * scale)
                img_h = int(h * scale)
                offset_x = (widget_size.width() - img_w) // 2
                offset_y = (widget_size.height() - img_h) // 2

                # Debug: log coordinate conversion for troubleshooting
                logger.debug(f"Coord conv: widget=({widget_size.width()}x{widget_size.height()}), device=({w}x{h}), scale={scale:.3f}, click=({x},{y})")

                if offset_x <= x <= offset_x + img_w and offset_y <= y <= offset_y + img_h:
                    device_x = int((x - offset_x) / scale)
                    device_y = int((y - offset_y) / scale)
                    logger.debug(f"Device coords: ({device_x}, {device_y})")
                    return device_x, device_y
                logger.debug(f"Click outside image area: offset=({offset_x},{offset_y}), img=({img_w}x{img_h})")
                return None, None

            def paintEvent(self, event):
                painter = QPainter(self)
                painter.setRenderHint(QPainter.SmoothPixmapTransform)

                if self._frame is not None:
                    h, w, c = self._frame.shape
                    rgb = self._frame
                    q_img = QImage(rgb.data, w, h, w * c, QImage.Format_RGB888)
                    pixmap = QPixmap.fromImage(q_img)
                    widget_size = self.size()
                    scaled = pixmap.scaled(widget_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    x = (widget_size.width() - scaled.width()) // 2
                    y = (widget_size.height() - scaled.height()) // 2
                    painter.drawPixmap(x, y, scaled)
                else:
                    painter.fillRect(self.rect(), Qt.black)

            def mousePressEvent(self, event: QMouseEvent):
                """Handle mouse press - start touch down."""
                if event.button() == Qt.LeftButton:
                    x = event.position().x()
                    y = event.position().y()
                    device_x, device_y = self._get_device_coords(x, y)
                    if device_x is not None:
                        self._touch_start = (device_x, device_y)
                        self._touch_current = (device_x, device_y)
                        self._is_swiping = False
                        # Send touch DOWN event immediately
                        try:
                            control_queue.put(('touch_down', device_x, device_y), timeout=0.1)
                        except:
                            pass

            def mouseMoveEvent(self, event: QMouseEvent):
                """Handle mouse move - send touch move for real-time tracking."""
                if self._touch_start is not None:
                    x = event.position().x()
                    y = event.position().y()
                    device_x, device_y = self._get_device_coords(x, y)
                    if device_x is not None:
                        self._touch_current = (device_x, device_y)
                        dx = abs(device_x - self._touch_start[0])
                        dy = abs(device_y - self._touch_start[1])
                        if dx > 10 or dy > 10:
                            self._is_swiping = True
                        # Send touch MOVE event for real-time tracking
                        try:
                            control_queue.put(('touch_move', device_x, device_y), timeout=0.05)
                        except:
                            pass

            def mouseReleaseEvent(self, event: QMouseEvent):
                """Handle mouse release - send touch up."""
                if event.button() == Qt.LeftButton and self._touch_start is not None:
                    try:
                        # Always send touch UP event
                        x, y = self._touch_current if self._touch_current else self._touch_start
                        control_queue.put(('touch_up', x, y), timeout=0.1)
                    except:
                        pass
                    finally:
                        self._touch_start = None
                        self._touch_current = None
                        self._is_swiping = False

            def wheelEvent(self, event):
                """Handle mouse wheel for scroll."""
                if self._frame is not None:
                    x = event.position().x()
                    y = event.position().y()
                    device_x, device_y = self._get_device_coords(x, y)
                    if device_x is not None:
                        # Get scroll delta (usually 120 per step)
                        delta = event.angleDelta().y() / 120.0
                        # Convert to scroll value (typically -1.0 to 1.0)
                        vscroll = -delta * 0.5  # Negative because wheel up = scroll down
                        try:
                            control_queue.put(('scroll', device_x, device_y, 0.0, vscroll), timeout=0.1)
                        except:
                            pass

            def keyPressEvent(self, event: QKeyEvent):
                key = event.key()
                key_map = {
                    Qt.Key_Back: 'back',
                    Qt.Key_Home: 'home',
                    Qt.Key_Menu: 'menu',
                    Qt.Key_Enter: 'enter',
                    Qt.Key_Return: 'enter',
                    Qt.Key_Escape: 'back',
                }
                action = key_map.get(key)
                if action:
                    try:
                        control_queue.put(('key', action), timeout=0.1)
                    except:
                        pass

        PreviewWidget = CPUPreviewWidget
        logger.warning("Using CPU rendering for preview")
        logger.warning("CPU mode: NOT recommended for >2Mbps or >30fps due to GIL contention causing delays")

    class GLWindowContainer(QWidget):
        """
        Custom container widget for QOpenGLWindow that handles mouse/keyboard events.

        When QOpenGLWindow is embedded via createWindowContainer(), mouse events
        are received by the container widget, not the QOpenGLWindow itself.
        This class handles events directly and sends control messages to the device.
        """

        def __init__(self, gl_window, main_window, parent=None):
            super().__init__(parent)
            self._gl_window = gl_window
            self._main_window = main_window
            self.setMouseTracking(True)  # Enable mouse move events

            # Touch tracking
            self._touch_start = None
            self._touch_current = None
            self._is_swiping = False

            # Create the actual container for the GL window
            self._container = QWidget.createWindowContainer(gl_window, self)
            # Don't let the internal container grab keyboard focus
            self._container.setFocusPolicy(Qt.NoFocus)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self._container)

        def _get_device_coords(self, x, y):
            """Convert widget coordinates to device coordinates."""
            w, h = self._gl_window._device_size
            if w == 0 or h == 0:
                logger.warning(f"[COORD] Invalid device size: {w}x{h}")
                return None, None
            widget_w = self.width()
            widget_h = self.height()
            scale = min(widget_w / w, widget_h / h)
            img_w = int(w * scale)
            img_h = int(h * scale)
            offset_x = (widget_w - img_w) // 2
            offset_y = (widget_h - img_h) // 2

            # Debug log for coordinate conversion
            logger.debug(f"[COORD] widget=({widget_w}x{widget_h}), device=({w}x{h}), scale={scale:.3f}")
            logger.debug(f"[COORD] img=({img_w}x{img_h}), offset=({offset_x},{offset_y}), click=({x},{y})")

            if offset_x <= x <= offset_x + img_w and offset_y <= y <= offset_y + img_h:
                device_x = int((x - offset_x) / scale)
                device_y = int((y - offset_y) / scale)
                logger.debug(f"[COORD] Device coords: ({device_x}, {device_y})")
                return device_x, device_y
            logger.warning(f"[COORD] Click outside image area!")
            return None, None

        def mousePressEvent(self, event):
            """Handle mouse press - start touch down."""
            if event.button() == Qt.LeftButton:
                x = event.position().x()
                y = event.position().y()
                device_x, device_y = self._get_device_coords(x, y)
                if device_x is not None:
                    self._touch_start = (device_x, device_y)
                    self._touch_current = (device_x, device_y)
                    self._is_swiping = False
                    # Send touch DOWN event immediately
                    try:
                        control_queue.put(('touch_down', device_x, device_y), timeout=0.1)
                        logger.debug(f"[TOUCH] Down at ({device_x}, {device_y})")
                    except Exception as e:
                        logger.warning(f"[TOUCH] Failed to send touch_down: {e}")

        def mouseMoveEvent(self, event):
            """Handle mouse move - send touch move for real-time tracking."""
            if self._touch_start is not None:
                x = event.position().x()
                y = event.position().y()
                device_x, device_y = self._get_device_coords(x, y)
                if device_x is not None:
                    self._touch_current = (device_x, device_y)
                    dx = abs(device_x - self._touch_start[0])
                    dy = abs(device_y - self._touch_start[1])
                    if dx > 10 or dy > 10:
                        self._is_swiping = True
                    # Send touch MOVE event for real-time tracking
                    try:
                        control_queue.put(('touch_move', device_x, device_y), timeout=0.05)
                    except:
                        pass

        def mouseReleaseEvent(self, event):
            """Handle mouse release - send touch up."""
            if event.button() == Qt.LeftButton and self._touch_start is not None:
                try:
                    # Always send touch UP event
                    x, y = self._touch_current if self._touch_current else self._touch_start
                    control_queue.put(('touch_up', x, y), timeout=0.1)
                    logger.debug(f"[TOUCH] Up at ({x}, {y})")
                except:
                    pass
                finally:
                    self._touch_start = None
                    self._touch_current = None
                    self._is_swiping = False

        def wheelEvent(self, event):
            """Handle mouse wheel for scroll."""
            x = event.position().x()
            y = event.position().y()
            device_x, device_y = self._get_device_coords(x, y)
            if device_x is not None:
                delta = event.angleDelta().y() / 120.0
                vscroll = -delta * 0.5
                try:
                    control_queue.put(('scroll', device_x, device_y, 0.0, vscroll), timeout=0.1)
                except:
                    pass

        def keyPressEvent(self, event):
            """Handle key press for control events."""
            key = event.key()
            key_map = {
                Qt.Key_Back: 'back',
                Qt.Key_Home: 'home',
                Qt.Key_Menu: 'menu',
                Qt.Key_Enter: 'enter',
                Qt.Key_Return: 'enter',
                Qt.Key_Escape: 'back',
            }
            action = key_map.get(key)
            if action:
                try:
                    control_queue.put(('key', action), timeout=0.1)
                    logger.debug(f"[KEY] {action}")
                except:
                    pass

        def keyReleaseEvent(self, event):
            """Handle key release."""
            pass

    class PreviewWindow(QMainWindow):
        """Main window for preview."""

        def __init__(self, notifier_handle=None):
            super().__init__()
            self._base_title = f"scrcpy-py-ddlx - {device_name}"
            self.setWindowTitle(self._base_title)
            self.setMinimumSize(400, 300)
            self._device_size = (width, height)

            # Enable keyboard focus for the main window
            self.setFocusPolicy(Qt.StrongFocus)

            # Enable input method support
            self.setAttribute(Qt.WA_InputMethodEnabled, True)
            self.setInputMethodHints(Qt.ImhPreferLatin)  # Prefer Latin input for stability

            # Calculate initial size
            screen = QApplication.primaryScreen()
            if screen:
                screen_size = screen.availableGeometry()
                max_w = screen_size.width() - 100
                max_h = screen_size.height() - 100
                scale = min(max_w / width, max_h / height, 1.0)
                self.resize(int(width * scale), int(height * scale))
            else:
                self.resize(min(width, 800), min(height, 600))

            # Create preview widget
            if OPENGL_AVAILABLE:
                # QOpenGLWindow is a QWindow, need to wrap it in a container widget
                self._gl_window = PreviewWidget()
                # Use custom container that forwards mouse events to GL window
                self._widget = GLWindowContainer(self._gl_window, self)
                self._widget.setFocusPolicy(Qt.StrongFocus)  # Enable keyboard focus
                logger.info("[PREVIEW] Using QOpenGLWindow (low CPU mode)")
            else:
                # CPU mode uses QWidget directly
                self._gl_window = None
                self._widget = PreviewWidget()
            self.setCentralWidget(self._widget)

            # Center window on screen
            self._center_on_screen()

            # Event-driven or polling mode
            self._notifier_handle = notifier_handle
            self._socket_notifier = None
            self._win_event_notifier = None
            self._notify_socket = None

            if notifier_handle is not None:
                # Event-driven mode
                self._setup_event_notifier(notifier_handle)
                logger.info("[PREVIEW] Event-driven mode enabled")

                # Fallback timer for safety (in case events are missed)
                self._timer = QTimer()
                self._timer.timeout.connect(self._update_frame)
                self._timer.start(100)  # 100ms fallback
            else:
                # Polling mode (original behavior)
                self._timer = QTimer()
                self._timer.timeout.connect(self._update_frame)
                self._timer.start(16)  # 16ms interval (~60fps)
                logger.info("[PREVIEW] Polling mode (16ms timer)")

            self._frame_count = 0
            self._last_true_e2e = 0  # Track last TRUE_E2E for title display

            # Install event filter to catch keyboard events at application level
            QApplication.instance().installEventFilter(self)
            logger.debug("[PREVIEW] Event filter installed")

        def eventFilter(self, obj, event):
            """Event filter to catch keyboard and input method events at application level."""
            from PySide6.QtCore import QEvent

            # Log all event types for debugging (only for our window)
            if self.isActiveWindow() and event.type() in [
                QEvent.Type.KeyPress,
                QEvent.Type.KeyRelease,
                QEvent.Type.InputMethod,
                QEvent.Type.InputMethodQuery,
            ]:
                logger.debug(f"[EVENT] type={event.type()}, obj={obj.__class__.__name__}")

            # Handle input method events (for IME like Chinese input)
            if event.type() == QEvent.Type.InputMethod:
                if self.isActiveWindow():
                    commit_string = event.commitString()
                    preedit_string = event.preeditString()
                    logger.debug(f"[IME] commit='{commit_string}', preedit='{preedit_string}'")
                    if commit_string:
                        try:
                            control_queue.put(('text', commit_string), timeout=0.1)
                            logger.debug(f"[TEXT] '{commit_string}' (from IME)")
                        except Exception as e:
                            logger.warning(f"[TEXT] Failed: {e}")
                        return True  # Event handled

            # Handle key press events
            if event.type() == QEvent.Type.KeyPress:
                # Only process if this window is active
                if self.isActiveWindow():
                    key = event.key()
                    text = event.text()
                    modifiers = event.modifiers()
                    logger.debug(f"[EVENT_FILTER] key={key}, text='{text}', modifiers={modifiers}")

                    # Extended key mappings (Qt.Key -> Android keycode name)
                    # Reference: https://developer.android.com/reference/android/view/KeyEvent
                    key_map = {
                        # Navigation keys
                        Qt.Key_Back: 'back',
                        Qt.Key_Home: 'home',
                        Qt.Key_Menu: 'menu',
                        Qt.Key_Enter: 'enter',
                        Qt.Key_Return: 'enter',
                        Qt.Key_Escape: 'back',
                        Qt.Key_Backtab: 'back',

                        # Arrow keys
                        Qt.Key_Left: 'dpad_left',
                        Qt.Key_Right: 'dpad_right',
                        Qt.Key_Up: 'dpad_up',
                        Qt.Key_Down: 'dpad_down',

                        # Media keys (only include keys that exist in PySide6)
                        Qt.Key_VolumeUp: 'volume_up',
                        Qt.Key_VolumeDown: 'volume_down',
                        Qt.Key_VolumeMute: 'volume_mute',

                        # Function keys
                        Qt.Key_F1: 'f1',
                        Qt.Key_F2: 'f2',
                        Qt.Key_F3: 'f3',
                        Qt.Key_F4: 'f4',
                        Qt.Key_F5: 'f5',
                        Qt.Key_F6: 'f6',
                        Qt.Key_F7: 'f7',
                        Qt.Key_F8: 'f8',
                        Qt.Key_F9: 'f9',
                        Qt.Key_F10: 'f10',
                        Qt.Key_F11: 'f11',
                        Qt.Key_F12: 'f12',

                        # Special keys
                        Qt.Key_Tab: 'tab',
                        Qt.Key_Delete: 'del',
                        Qt.Key_Backspace: 'del',
                        Qt.Key_Insert: 'insert',
                        Qt.Key_PageUp: 'page_up',
                        Qt.Key_PageDown: 'page_down',
                        Qt.Key_CapsLock: 'caps_lock',
                    }

                    action = key_map.get(key)
                    if action:
                        try:
                            control_queue.put(('key', action), timeout=0.1)
                            logger.debug(f"[KEY] {action}")
                        except Exception as e:
                            logger.warning(f"[KEY] Failed: {e}")
                        return True  # Event handled

                    # Text input
                    if text:
                        char = text[0] if len(text) > 0 else ''
                        logger.debug(f"[TEXT_CHECK] char='{char}', printable={char.isprintable() if char else 'N/A'}")
                        if char and (char.isprintable() or char in '\t\n'):
                            try:
                                control_queue.put(('text', char), timeout=0.1)
                                logger.debug(f"[TEXT] '{char}'")
                            except Exception as e:
                                logger.warning(f"[TEXT] Failed: {e}")
                            return True  # Event handled

            return super().eventFilter(obj, event)

        def inputMethodEvent(self, event):
            """Handle input method events directly."""
            commit_string = event.commitString()
            if commit_string:
                logger.debug(f"[IME_DIRECT] commitString='{commit_string}'")
                try:
                    control_queue.put(('text', commit_string), timeout=0.1)
                    logger.debug(f"[TEXT] '{commit_string}' (from IME direct)")
                except Exception as e:
                    logger.warning(f"[TEXT] Failed: {e}")
            event.accept()

        @property
        def _renderer(self):
            """Return the actual renderer object (QOpenGLWindow or QWidget)."""
            return self._gl_window if self._gl_window is not None else self._widget

        def _setup_event_notifier(self, handle):
            """Set up event notifier based on handle type."""
            if hasattr(handle, 'fileno'):
                # Socket-based notifier (Linux/macOS socketpair)
                self._setup_socket_notifier(handle)
            elif isinstance(handle, str):
                # String handle - could be QLocalServer name or Win32 Event name
                if handle.startswith("scrcpy_preview_"):
                    # QLocalServer name (LocalSocketNotifier)
                    self._setup_local_socket_notifier(handle)
                elif platform.system() == "Windows":
                    # Win32 Event name (fallback)
                    self._setup_win32_notifier(handle)
                else:
                    logger.warning(f"[PREVIEW] Unknown string handle: {handle}")
            elif isinstance(handle, int):
                # File descriptor (from socket.fileno())
                self._setup_socket_from_fd(handle)
            else:
                logger.warning(f"[PREVIEW] Unknown notifier handle type: {type(handle)}")

        def _setup_local_socket_notifier(self, server_name: str):
            """Set up QLocalSocket for LocalSocketNotifier."""
            from PySide6.QtNetwork import QLocalSocket

            self._local_socket = QLocalSocket()
            self._local_socket.connectToServer(server_name)

            if self._local_socket.waitForConnected(5000):
                self._local_socket.readyRead.connect(self._on_local_socket_ready_read)
                logger.info(f"[PREVIEW] QLocalSocket connected to: {server_name}")
            else:
                logger.warning(f"[PREVIEW] QLocalSocket connection failed: {self._local_socket.errorString()}")

        def _on_local_socket_ready_read(self):
            """Handle QLocalSocket readyRead signal."""
            # Read and discard notification data
            try:
                self._local_socket.readAll()
            except:
                pass
            # Process frame
            self._update_frame()

        def _setup_socket_notifier(self, sock):
            """Set up QSocketNotifier for socket-based notifications."""
            from PySide6.QtCore import QSocketNotifier

            self._notify_socket = sock
            self._socket_notifier = QSocketNotifier(
                sock.fileno(),
                QSocketNotifier.Type.Read
            )
            self._socket_notifier.activated.connect(self._on_socket_notify)
            self._socket_notifier.setEnabled(True)
            logger.info(f"[PREVIEW] QSocketNotifier enabled on fd={sock.fileno()}")

        def _setup_socket_from_fd(self, fd: int):
            """Set up QSocketNotifier from file descriptor."""
            from PySide6.QtCore import QSocketNotifier

            self._socket_notifier = QSocketNotifier(
                fd,
                QSocketNotifier.Type.Read
            )
            self._socket_notifier.activated.connect(self._on_fd_notify)
            self._socket_notifier.setEnabled(True)
            logger.info(f"[PREVIEW] QSocketNotifier enabled on fd={fd}")

        def _setup_win32_notifier(self, event_name: str):
            """Set up QWinEventNotifier for Win32 Event notifications."""
            try:
                import win32event
                from PySide6.QtCore import QWinEventNotifier

                # Open the named event
                self._win_event = win32event.OpenEvent(
                    win32event.EVENT_MODIFY_STATE | win32event.SYNCHRONIZE,
                    False,
                    event_name
                )

                # PyHANDLE.handle is already an int, use it directly
                self._win_event_notifier = QWinEventNotifier(self._win_event.handle)
                self._win_event_notifier.activated.connect(self._on_win32_notify)
                self._win_event_notifier.setEnabled(True)
                logger.info(f"[PREVIEW] QWinEventNotifier enabled: {event_name}")

            except ImportError:
                logger.warning("[PREVIEW] pywin32 not available, falling back to polling")
            except Exception as e:
                logger.warning(f"[PREVIEW] QWinEventNotifier setup failed: {e}")

        def _on_socket_notify(self, fd):
            """Handle socket notification (Linux/macOS)."""
            # Read and discard notification byte(s)
            try:
                self._notify_socket.recv(1024)
            except BlockingIOError:
                pass
            except Exception as e:
                logger.debug(f"[PREVIEW] Socket recv error: {e}")

            # Process frame
            self._update_frame()

        def _on_fd_notify(self, fd):
            """Handle fd notification (Windows socket)."""
            # Create a temporary socket to read notification
            try:
                temp_sock = socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM)
                temp_sock.recv(1024)
            except:
                pass

            # Process frame
            self._update_frame()

        def _on_win32_notify(self, handle):
            """Handle Win32 Event notification."""
            # The event is auto-reset, no need to call ResetEvent
            # Process frame
            self._update_frame()

        def _center_on_screen(self):
            """Center the window on the primary screen."""
            screen = QApplication.primaryScreen()
            if screen:
                screen_geometry = screen.availableGeometry()
                window_geometry = self.frameGeometry()
                x = (screen_geometry.width() - window_geometry.width()) // 2
                y = (screen_geometry.height() - window_geometry.height()) // 2
                self.move(x + screen_geometry.x(), y + screen_geometry.y())

        def resizeEvent(self, event):
            """
            Handle window resize - maintain video aspect ratio.
            When user drags any edge, adjust to match video aspect ratio.
            """
            super().resizeEvent(event)

            device_width, device_height = self._device_size
            if device_width == 0 or device_height == 0:
                return

            old_width = event.oldSize().width() if event.oldSize() else 0
            old_height = event.oldSize().height() if event.oldSize() else 0
            new_width = event.size().width()
            new_height = event.size().height()

            if old_width <= 0 or old_height <= 0:
                return

            device_aspect = device_width / device_height

            # Determine user's intent: which dimension changed more?
            width_change = abs(new_width - old_width)
            height_change = abs(new_height - old_height)

            # Calculate corrected size based on which dimension user is adjusting
            if width_change >= height_change:
                # User is adjusting width more (dragging vertical edges or corners)
                # Keep width, adjust height
                corrected_width = new_width
                corrected_height = int(new_width / device_aspect)
            else:
                # User is adjusting height more (dragging horizontal edges)
                # Keep height, adjust width
                corrected_width = int(new_height * device_aspect)
                corrected_height = new_height

            # Apply minimum size
            corrected_width = max(corrected_width, 200)
            corrected_height = max(corrected_height, 200)

            # If size doesn't match aspect ratio, correct it
            if corrected_width != new_width or corrected_height != new_height:
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self.resize(corrected_width, corrected_height))

        def set_device_size(self, w: int, h: int):
            """Update device size (called when device rotates)."""
            old_w, old_h = self._device_size
            if (w, h) == (old_w, old_h):
                return  # No change

            logger.info(f"Device size changed: {old_w}x{old_h} -> {w}x{h}")
            self._device_size = (w, h)

            # Notify MCP server about device size change for touch events
            try:
                control_queue.put(('device_size_changed', w, h), timeout=0.1)
                logger.info(f"[ROTATION] Sent device_size_changed to MCP server: {w}x{h}")
            except Exception as e:
                logger.warning(f"[ROTATION] Failed to send device_size_changed: {e}")

            # Adjust window to new aspect ratio while keeping similar area
            current_w, current_h = self.width(), self.height()
            device_aspect = w / h

            # Keep width, adjust height for new aspect ratio
            new_h = int(current_w / device_aspect)
            new_w = current_w

            # If new height is too small/large, adjust width instead
            if new_h < 200:
                new_h = 200
                new_w = int(new_h * device_aspect)
            elif new_h > 1200:
                new_h = 1200
                new_w = int(new_h * device_aspect)

            self.resize(new_w, new_h)
            self._renderer.set_device_size(w, h)

        def _update_frame(self):
            """Check for new frames and update display - OPTIMIZED for low latency."""
            timer_tick_time = time.time()

            # Track timer interval accuracy
            if not hasattr(self, '_last_timer_tick'):
                self._last_timer_tick = timer_tick_time
            timer_interval_ms = (timer_tick_time - self._last_timer_tick) * 1000
            self._last_timer_tick = timer_tick_time

            # Log timer ticks periodically (every 60 ticks = ~60ms at 1ms interval)
            if not hasattr(self, '_timer_tick_count'):
                self._timer_tick_count = 0
            self._timer_tick_count += 1

            if self._timer_tick_count <= 5 or self._timer_tick_count % 60 == 0:
                logger.debug(f"[TIMER_TICK] #{self._timer_tick_count}: interval={timer_interval_ms:.1f}ms")

            if stop_event.is_set():
                self.close()
                return

            try:
                # Priority 1: Try shared memory (low latency)
                if shared_mem_reader is not None:
                    shm_read_start = time.time()
                    result = shared_mem_reader.read_frame_ex()  # Use extended read for format detection
                    shm_read_end = time.time()
                    shm_read_ms = (shm_read_end - shm_read_start) * 1000

                    if result is not None:
                        frame, pts, capture_time, udp_recv_time, frame_format, frame_width, frame_height = result
                        frame_available_time = time.time()

                        # Check for frame size change (rotation)
                        # Use set_device_size() which also resizes the window
                        if (frame_width, frame_height) != self._device_size:
                            self.set_device_size(frame_width, frame_height)
                            logger.info(f"[ROTATION] Frame size changed: {frame_width}x{frame_height}")

                        # NOTE: Removed PTS-based duplicate detection because PTS unit is unreliable
                        # (device sends microseconds but code treated it as nanoseconds).
                        # SimpleSHM always returns the latest frame, so no duplicate detection needed.
                        self._frame_count += 1

                        # Current time for all calculations
                        current_time = time.time()

                        # Calculate all timing components
                        udp_to_shm_read_ms = (shm_read_start - udp_recv_time) * 1000 if udp_recv_time > 0 else 0
                        capture_to_now_ms = (current_time - capture_time) * 1000 if capture_time > 0 else 0

                        # PTS Clock Drift Diagnostic (same as main process)
                        # IMPORTANT: PTS from scrcpy device is in MICROSECONDS, not nanoseconds!
                        if not hasattr(self, '_preview_last_pts'):
                            self._preview_last_pts = 0
                            self._preview_last_pts_time = 0.0
                            self._preview_first_pts = 0
                            self._preview_first_pts_time = 0.0

                        if self._preview_last_pts != 0:
                            pts_delta_us = pts - self._preview_last_pts  # PTS increment in MICROSECONDS
                            wall_delta_us = int((current_time - self._preview_last_pts_time) * 1e6)  # Wall clock in MICROSECONDS
                            drift_us = pts_delta_us - wall_delta_us

                            # Log every frame for first 10, then every 60
                            if self._frame_count <= 10 or self._frame_count % 60 == 0:
                                # Cumulative drift from first frame (PTS is in MICROSECONDS)
                                total_pts_us = pts - self._preview_first_pts
                                total_wall_us = int((current_time - self._preview_first_pts_time) * 1e6)
                                total_drift_ms = (total_pts_us - total_wall_us) / 1e3  # us to ms

                                # TRUE_E2E calculation
                                true_e2e_ms = (current_time - udp_recv_time) * 1000 if udp_recv_time > 0 else 0

                                # Decode time (from capture_time to now)
                                decode_to_display_ms = (current_time - capture_time) * 1000 if capture_time > 0 else 0

                                logger.info(
                                    f"[PREVIEW_PTS] Frame #{self._frame_count}: "
                                    f"pts={pts}, pts_delta={pts_delta_us/1e3:.2f}ms, "  # us to ms
                                    f"wall_delta={wall_delta_us/1e3:.2f}ms, "  # us to ms
                                    f"drift={drift_us/1e3:.2f}ms, "  # us to ms
                                    f"total_drift={total_drift_ms:.0f}ms, "
                                    f"TRUE_E2E={true_e2e_ms:.0f}ms"
                                )

                                # Comprehensive timing breakdown
                                logger.info(
                                    f"[TIMING_BREAKDOWN] Frame #{self._frame_count}: "
                                    f"shm_read={shm_read_ms:.2f}ms, "
                                    f"udp_to_shm_read={udp_to_shm_read_ms:.1f}ms, "
                                    f"capture_to_now={capture_to_now_ms:.1f}ms, "
                                    f"timer_interval={timer_interval_ms:.1f}ms"
                                )

                        # Record first PTS
                        if self._preview_first_pts == 0:
                            self._preview_first_pts = pts
                            self._preview_first_pts_time = current_time
                            logger.info(f"[PREVIEW_PTS] First frame received: pts={pts}")

                        self._preview_last_pts = pts
                        self._preview_last_pts_time = current_time

                        # Calculate latency for window title (minimal overhead)
                        true_e2e_ms = (current_time - udp_recv_time) * 1000 if udp_recv_time > 0 else 0

                        # Update window title (only every 10 frames to reduce overhead)
                        if self._frame_count % 10 == 0:
                            self.setWindowTitle(f"{self._base_title} | E2E={true_e2e_ms:.0f}ms")

                        # Update widget - core operation
                        widget_update_start = time.time()
                        self._renderer._frame_available_time = frame_available_time
                        self._renderer.update_frame(frame, self._frame_count, frame_format)
                        widget_update_end = time.time()

                        # Trigger rendering
                        # QOpenGLWindow uses update(), QWidget uses repaint()
                        update_call_start = time.time()
                        if self._gl_window is not None:
                            self._gl_window.update()  # QOpenGLWindow
                        else:
                            self._widget.repaint()  # QWidget fallback
                        self._renderer._last_update_call_time = update_call_start
                        update_call_end = time.time()

                        if self._frame_count <= 5 or self._frame_count % 60 == 0:
                            widget_update_ms = (widget_update_end - widget_update_start) * 1000
                            update_call_ms = (update_call_end - update_call_start) * 1000
                            total_update_ms = (time.time() - shm_read_start) * 1000
                            logger.info(
                                f"[UPDATE_TIMING] Frame #{self._frame_count}: "
                                f"widget_update={widget_update_ms:.2f}ms, "
                                f"update_call={update_call_ms:.2f}ms, "
                                f"total_update={total_update_ms:.2f}ms"
                            )

                        # Auto-detect size change (rare event)
                        # For NV12, use _device_size (set elsewhere), for RGB use frame shape
                        if frame_format == 0:  # RGB24
                            if hasattr(frame, 'shape') and len(frame.shape) >= 2:
                                h, w = frame.shape[:2]
                                if (w, h) != self._device_size:
                                    self.set_device_size(w, h)
                        # For NV12, device size is tracked from main process

                # Priority 2: Fall back to queue
                elif not frame_queue.empty():
                    item = frame_queue.get_nowait()
                    if isinstance(item, tuple) and len(item) == 2:
                        w, h = item
                        self.set_device_size(w, h)
                    elif item is not None:
                        self._frame_count += 1
                        self._renderer.update_frame(item, self._frame_count)
                        if self._gl_window is not None:
                            self._gl_window.update()  # QOpenGLWindow
                        else:
                            self._widget.repaint()  # QWidget fallback

            except Exception as e:
                if self._frame_count < 10:
                    logger.warning(f"Frame update error: {e}")

        def keyPressEvent(self, event):
            """Handle key press for control events and text input."""
            key = event.key()
            text = event.text()
            modifiers = event.modifiers()

            # Debug: log all key events
            logger.debug(f"[KEY_EVENT] key={key}, text='{text}', modifiers={modifiers}")

            # Control key mappings
            key_map = {
                Qt.Key_Back: 'back',
                Qt.Key_Home: 'home',
                Qt.Key_Menu: 'menu',
                Qt.Key_Enter: 'enter',
                Qt.Key_Return: 'enter',
                Qt.Key_Escape: 'back',
                Qt.Key_Backtab: 'back',  # Shift+Tab
            }

            # Check for control keys first
            action = key_map.get(key)
            if action:
                try:
                    control_queue.put(('key', action), timeout=0.1)
                    logger.debug(f"[KEY] {action}")
                except Exception as e:
                    logger.warning(f"[KEY] Failed to send key: {e}")
                return

            # Handle text input (printable characters)
            # Accept single printable characters (including space, letters, numbers, symbols)
            if text:
                char = text[0] if len(text) > 0 else ''
                if char and (char.isprintable() or char in '\t\n'):
                    try:
                        control_queue.put(('text', char), timeout=0.1)
                        logger.debug(f"[TEXT] '{char}' (ord={ord(char)})")
                    except Exception as e:
                        logger.warning(f"[TEXT] Failed to send text: {e}")
                    return

            # Pass other keys to parent
            super().keyPressEvent(event)

        def showEvent(self, event):
            """Handle window show - activate and grab focus."""
            super().showEvent(event)
            # Activate window and grab keyboard focus
            self.activateWindow()
            self.raise_()
            self.setFocus()
            logger.debug("[PREVIEW] Window shown, focus grabbed")

        def focusInEvent(self, event):
            """Handle focus in."""
            super().focusInEvent(event)
            logger.debug("[PREVIEW] Window gained focus")

        def mousePressEvent(self, event):
            """Handle mouse press."""
            super().mousePressEvent(event)
            # Ensure window has focus when user clicks
            self.setFocus()

        def closeEvent(self, event):
            self._timer.stop()
            stop_event.set()
            event.accept()

    # Create Qt application
    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    # Create and show window
    # Create and show window (with notifier handle for event-driven mode)
    window = PreviewWindow(notifier_handle=notifier_handle)
    window.show()

    render_mode = "OpenGL (GPU)" if OPENGL_AVAILABLE else "CPU"
    logger.info(f"Preview window started: {device_name} ({width}x{height}), rendering: {render_mode}")

    # Signal that preview is ready to receive frames
    if ready_event is not None:
        ready_event.set()
        logger.info("Preview ready signal sent")

    # Use native Qt event loop for accurate timer timing
    # Create a timer to check for stop_event
    from PySide6.QtCore import QTimer

    stop_check_timer = QTimer()
    stop_check_timer.setInterval(50)  # Check every 50ms

    def check_stop():
        if stop_event.is_set():
            logger.info("Stop event detected, closing preview")
            window.close()
            app.quit()

    stop_check_timer.timeout.connect(check_stop)
    stop_check_timer.start()

    # Run native Qt event loop (accurate timer timing)
    app.exec()

    logger.info("Preview window stopped")

    # Clean up shared memory reader
    if shared_mem_reader is not None:
        try:
            shared_mem_reader.close()
            logger.debug("Shared memory reader closed")
        except Exception as e:
            logger.debug(f"Shared memory reader cleanup: {e}")

    # Clean up Qt resources
    try:
        window.close()
        app.quit()
    except Exception as e:
        logger.debug(f"Qt cleanup: {e}")


class PreviewManager:
    """
    Manager for the separated preview window process.

    This class handles starting and stopping the preview process,
    and provides methods to send frames to it.

    Example:
        manager = PreviewManager()

        # Start preview
        if manager.start("My Device", 1080, 2400):
            # Send frames
            manager.send_frame(frame)

        # Stop preview
        manager.stop()
    """

    def __init__(self, max_queue_size: int = 2, use_shared_memory: bool = True,
                 event_driven: bool = True):
        """
        Initialize preview manager.

        Args:
            max_queue_size: Maximum frames in queue (fallback for non-shared-mem mode)
            use_shared_memory: Use shared memory for low-latency frame transfer
            event_driven: Use event-driven rendering (recommended, lower CPU)
        """
        self._process: Optional[mp.Process] = None
        self._frame_queue: Optional[mp.Queue] = None
        self._control_queue: Optional[mp.Queue] = None
        self._stop_event: Optional[mp.Event] = None
        self._ready_event: Optional[mp.Event] = None  # Signal when preview is ready
        self._max_queue_size = max_queue_size
        self._use_shared_memory = use_shared_memory
        self._shared_mem_buffer: Optional['SharedMemoryFrameBuffer'] = None
        self._shared_mem_info: Optional[dict] = None
        self._is_running = False
        self._device_name = ""
        self._device_size = (0, 0)

        # Event-driven rendering
        self._event_driven = event_driven
        self._notifier: Optional[FrameNotifierBase] = None
        self._notifier_handle = None  # Handle passed to child process

    @property
    def is_running(self) -> bool:
        """Check if preview is running."""
        return self._is_running and self._process is not None and self._process.is_alive()

    @property
    def control_queue(self) -> Optional[mp.Queue]:
        """Get control queue for sending input events."""
        return self._control_queue

    def start(self, device_name: str, width: int, height: int) -> bool:
        """
        Start the preview window process.

        Args:
            device_name: Device name for window title
            width: Video width
            height: Video height

        Returns:
            True if started successfully
        """
        if self.is_running:
            logger.warning("Preview already running")
            return True

        try:
            # Create shared memory buffer for low-latency transfer
            if self._use_shared_memory:
                from scrcpy_py_ddlx.simple_shm import SimpleSHMWriter
                # Use max dimensions to handle any frame size (including rotation)
                # Use max of width/height to handle both portrait and landscape
                max_dim = max(width, height, 4096)  # Max dimension for any orientation
                self._shared_mem_buffer = SimpleSHMWriter(
                    max_width=max_dim,
                    max_height=max_dim,
                    channels=3
                )
                self._shared_mem_info = self._shared_mem_buffer.get_info()
                logger.info(f"Simple shared memory created: {self._shared_mem_info['name']}, max={max_dim}x{max_dim}")
            else:
                self._shared_mem_buffer = None
                self._shared_mem_info = None

            # Control queue (small, for touch events)
            self._control_queue = mp.Queue(maxsize=100)
            self._stop_event = mp.Event()
            self._ready_event = mp.Event()  # Preview will set this when ready

            # Fallback frame queue (not used in shared memory mode)
            self._frame_queue = mp.Queue(maxsize=self._max_queue_size)

            # Create event-driven notifier (if enabled)
            self._notifier = None
            self._notifier_handle = None
            if self._event_driven and self._use_shared_memory:
                try:
                    self._notifier = create_frame_notifier()
                    self._notifier_handle = self._notifier.get_child_handle()
                    notifier_type = type(self._notifier).__name__
                    logger.info(f"[PREVIEW] Event-driven mode enabled: {notifier_type}")
                    # Set notify callback on SHM writer
                    if self._shared_mem_buffer and self._notifier:
                        self._shared_mem_buffer._notify_callback = self._notifier.notify
                        logger.info("[PREVIEW] Notify callback set on SHM writer")
                except Exception as e:
                    logger.warning(f"[PREVIEW] Failed to create notifier, falling back to polling: {e}")
                    self._notifier = None
                    self._notifier_handle = None

            # Start process
            self._process = mp.Process(
                target=preview_window_process,
                args=(
                    self._frame_queue,
                    self._control_queue,
                    device_name,
                    width,
                    height,
                    self._stop_event,
                    self._shared_mem_info,  # Pass shared memory info
                    self._ready_event,  # Pass ready event
                    self._notifier_handle  # Pass notifier handle (socket or event name)
                ),
                daemon=True
            )
            self._process.start()

            # For LocalSocketNotifier, wait for child process to connect
            if isinstance(self._notifier, LocalSocketNotifier):
                logger.info("[PREVIEW] Waiting for child process to connect...")
                if not self._notifier.accept_connection(timeout_ms=5000):
                    logger.warning("[PREVIEW] Child process connection failed, falling back to polling")
                else:
                    logger.info("[PREVIEW] Child process connected via QLocalSocket")

            self._is_running = True
            self._device_name = device_name
            self._device_size = (width, height)

            mode = "event-driven" if self._notifier else "polling"
            logger.info(f"Preview process started: {device_name} (shared_mem={self._use_shared_memory}, mode={mode})")
            return True

        except Exception as e:
            logger.error(f"Failed to start preview: {e}")
            self._cleanup()
            return False

    def wait_for_ready(self, timeout: float = 5.0) -> bool:
        """
        Wait for preview window to be ready.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if preview is ready, False if timeout
        """
        if self._ready_event is None:
            return False
        ready = self._ready_event.wait(timeout=timeout)
        if ready:
            logger.info("Preview window is ready")
        else:
            logger.warning(f"Preview window not ready after {timeout}s timeout")
        return ready

    def get_shm_writer(self):
        """
        Get the SimpleSHMWriter for direct frame writing.

        Returns:
            SimpleSHMWriter instance, or None if not using shared memory
        """
        return self._shared_mem_buffer

    def send_frame(self, frame: np.ndarray, pts: int = 0, capture_time: float = 0.0, udp_recv_time: float = 0.0) -> bool:
        """
        Send a frame to the preview window.

        Args:
            frame: BGR numpy array (H, W, 3)
            pts: Presentation timestamp from device (nanoseconds)
            capture_time: Time when frame was decoded on PC (seconds)
            udp_recv_time: Time when UDP packet was received (seconds)

        Returns:
            True if frame was sent, False if queue is full
        """
        if not self.is_running:
            return False

        try:
            # Use shared memory for low-latency transfer
            if self._shared_mem_buffer is not None:
                success = self._shared_mem_buffer.write_frame(frame, pts, capture_time, udp_recv_time)
                logger.debug(f"[PREVIEW] write_frame success={success}, notifier={self._notifier is not None}")
                if success and self._notifier:
                    # Notify preview process (event-driven mode)
                    self._notifier.notify()
                return success
            else:
                # Fallback to queue (slower)
                if self._frame_queue.full():
                    # Drop oldest frame
                    try:
                        self._frame_queue.get_nowait()
                    except:
                        pass

                self._frame_queue.put(frame, block=True, timeout=0.1)
                return True
        except Exception as e:
            logger.debug(f"send_frame error: {e}")
            return False

    def stop(self) -> bool:
        """
        Stop the preview window process.

        Returns:
            True if stopped successfully
        """
        # Always cleanup if process reference exists (even if already stopped)
        if self._process is None:
            logger.debug("stop(): process is None, returning")
            return True

        try:
            # Signal stop if event exists
            logger.debug("stop(): signaling stop event")
            if self._stop_event is not None:
                self._stop_event.set()
                logger.debug("stop(): stop event set")

            # Check if process is still alive before waiting
            logger.debug(f"stop(): checking if process alive: {self._process.is_alive()}")
            if self._process.is_alive():
                # Wait for process to finish (shorter timeout)
                logger.debug("stop(): calling join(timeout=1.0)")
                self._process.join(timeout=1.0)
                logger.debug("stop(): join returned")

                if self._process.is_alive():
                    logger.warning("Preview process didn't stop gracefully, terminating...")
                    # Force terminate
                    self._process.terminate()
                    logger.debug("stop(): calling join after terminate")
                    self._process.join(timeout=0.5)

                    if self._process.is_alive():
                        # Last resort: kill
                        logger.warning("Preview process still alive, killing...")
                        try:
                            self._process.kill()
                            self._process.join(timeout=0.5)
                        except Exception as e:
                            logger.warning(f"Kill failed: {e}")

                logger.info("Preview process stopped")
            else:
                # Process already ended (e.g., user closed window)
                logger.debug("Preview process already ended")

            return True

        except Exception as e:
            logger.error(f"Error stopping preview: {e}")
            import traceback
            traceback.print_exc()
            return False

        finally:
            logger.debug("stop(): calling _cleanup")
            self._cleanup()
            logger.debug("stop(): _cleanup done")

    def _cleanup(self):
        """Clean up resources."""
        self._is_running = False
        self._process = None

        # Close shared memory buffer
        if self._shared_mem_buffer is not None:
            try:
                self._shared_mem_buffer.close()
            except Exception as e:
                logger.debug(f"Shared memory close error: {e}")
            finally:
                self._shared_mem_buffer = None
                self._shared_mem_info = None

        # Close frame queue - must cancel_join_thread to prevent blocking
        if self._frame_queue is not None:
            try:
                # Cancel the join thread to prevent QueueFeederThread from blocking
                self._frame_queue.cancel_join_thread()
                self._frame_queue.close()
            except Exception as e:
                logger.debug(f"Frame queue close error: {e}")
            finally:
                self._frame_queue = None

        # Close control queue
        if self._control_queue is not None:
            try:
                self._control_queue.cancel_join_thread()
                self._control_queue.close()
            except Exception as e:
                logger.debug(f"Control queue close error: {e}")
            finally:
                self._control_queue = None

        # Clear stop event reference
        self._stop_event = None

    def get_control_events(self) -> list:
        """
        Get pending control events from preview window.

        Returns:
            List of (type, *args) tuples
        """
        events = []
        if self._control_queue is None:
            return events

        try:
            while not self._control_queue.empty():
                event = self._control_queue.get_nowait()
                events.append(event)
        except:
            pass

        return events


__all__ = ['PreviewManager', 'preview_window_process']
