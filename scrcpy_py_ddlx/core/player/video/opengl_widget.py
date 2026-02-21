"""
OpenGL video widget using GPU acceleration for displaying scrcpy video stream.

This module provides an OpenGL-based widget for hardware-accelerated rendering.
Like official scrcpy, this uses GPU textures for rendering.

Based on official scrcpy's SDL2 rendering in screen.c
"""

import logging
import os
import time
from typing import Optional, Tuple, TYPE_CHECKING
from ctypes import c_void_p
from collections import defaultdict
import numpy as np

# 性能监控开关（设置环境变量 PROFILE_OPENGL=1 启用）
PROFILE_OPENGL = os.environ.get('PROFILE_OPENGL', '0') == '1'
_profile_timings = defaultdict(list) if PROFILE_OPENGL else None

def _profile_time(name):
    """性能计时的上下文管理器"""
    class Timer:
        def __enter__(self):
            self.start = time.perf_counter()
            return self
        def __exit__(self, *args):
            if _profile_timings is not None:
                elapsed = (time.perf_counter() - self.start) * 1000
                _profile_timings[name].append(elapsed)
    return Timer()

def _profile_report():
    """输出性能报告"""
    if _profile_timings is None:
        return
    logger.info("=" * 60)
    logger.info("OpenGL 性能报告")
    logger.info("=" * 60)
    for name, times in sorted(_profile_timings.items()):
        if times:
            avg = sum(times) / len(times)
            max_t = max(times)
            logger.info(f"{name}: avg={avg:.3f}ms, max={max_t:.3f}ms, calls={len(times)}")

# 实验性零拷贝GPU模式（需要环境变量启用）
_ZERO_COPY_GPU_ENABLED = os.environ.get('SCRCPY_ZERO_COPY_GPU', '0') == '1'

try:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtCore import Qt, QMutex, QMetaObject, QTimer, Signal, Q_ARG
    from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent, QSurfaceFormat
except ImportError:
    QOpenGLWidget = object
    Qt = None
    QMutex = None
    QMetaObject = None
    QTimer = None
    Signal = None
    QSurfaceFormat = None
    QKeyEvent = None
    QMouseEvent = None
    QWheelEvent = None

try:
    from OpenGL.GL import (
        glGenTextures, glBindTexture, glTexParameteri, glDeleteTextures,
        glTexImage2D, glTexSubImage2D, glClearColor, glClear,
        glViewport, glMatrixMode, glLoadIdentity,
        glOrtho, glEnable, glDisable,
        glColor3f, glBegin, glEnd,
        glTexCoord2f, glMultiTexCoord2f, glVertex2f,
        glActiveTexture, glPixelStorei,
        GL_UNSIGNED_BYTE, GL_RGB, GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER,
        GL_TEXTURE_MAG_FILTER, GL_LINEAR, GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T,
        GL_CLAMP_TO_EDGE, GL_COLOR_BUFFER_BIT, GL_QUADS,
        GL_PROJECTION, GL_MODELVIEW, GL_TEXTURE0, GL_TEXTURE1, GL_TEXTURE2,
        GL_UNPACK_ALIGNMENT, GL_LUMINANCE
    )
    # Try to import QOpenGLShader for NV12 GPU rendering
    try:
        from PySide6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram
        SHADER_AVAILABLE = True
    except ImportError:
        QOpenGLShader = None
        QOpenGLShaderProgram = None
        SHADER_AVAILABLE = False
    OPENGL_AVAILABLE = True
except ImportError:
    OPENGL_AVAILABLE = False
    SHADER_AVAILABLE = False

from scrcpy_py_ddlx.core.protocol import (
    POINTER_ID_MOUSE,
    AndroidMotionEventAction,
    AndroidKeyEventAction,
)
from scrcpy_py_ddlx.core.player.video.input_handler import InputHandler, CoordinateMapper
from scrcpy_py_ddlx.core.player.video.keycode_mapping import qt_key_to_android_keycode

if TYPE_CHECKING:
    from scrcpy_py_ddlx.core.control import ControlMessageQueue
    from scrcpy_py_ddlx.core.decoder import DelayBuffer

logger = logging.getLogger(__name__)

# CUDA-OpenGL Interop support via ctypes (optional)
_CUDA_GL_INTEROP_AVAILABLE = False
_cuda_lib = None
_cuda_funcs = None

def _init_cuda_gl_interop_lib():
    """
    Initialize CUDA Runtime library for OpenGL Interop.

    Uses ctypes to directly load CUDA runtime library and access
    CUDA-OpenGL interop functions.
    """
    global _cuda_lib, _cuda_funcs, _CUDA_GL_INTEROP_AVAILABLE

    if _cuda_funcs is not None:
        return _CUDA_GL_INTEROP_AVAILABLE

    import ctypes
    import platform
    import os

    # Find CUDA runtime library
    cuda_lib_paths = []

    # Check CUDA_PATH environment variable first
    cuda_path = os.environ.get('CUDA_PATH', '')
    if cuda_path:
        bin_path = os.path.join(cuda_path, 'bin')
        if platform.system() == "Windows":
            cuda_lib_paths.extend([
                os.path.join(bin_path, "cudart64_13.dll"),
                os.path.join(bin_path, "cudart64_12.dll"),
                os.path.join(bin_path, "cudart64_11.dll"),
            ])

    # Standard paths
    if platform.system() == "Windows":
        # Common CUDA installation paths on Windows
        program_files = os.environ.get('ProgramFiles', 'C:\\Program Files')
        nvidia_cuda = os.path.join(program_files, 'NVIDIA GPU Computing Toolkit', 'CUDA')
        if os.path.exists(nvidia_cuda):
            for version_dir in sorted(os.listdir(nvidia_cuda), reverse=True):
                # Try both bin/ and bin/x64/ paths
                for subpath in ['bin', os.path.join('bin', 'x64')]:
                    bin_path = os.path.join(nvidia_cuda, version_dir, subpath)
                    if os.path.exists(bin_path):
                        # Try different naming conventions:
                        # v13.0 -> cudart64_13.dll
                        # v12.x -> cudart64_12.dll
                        # v11.8 -> cudart64_110.dll
                        version_parts = version_dir.replace('v', '').split('.')
                        major = version_parts[0]
                        minor = version_parts[1] if len(version_parts) > 1 else '0'
                        # Try: cudart64_13.dll (major only)
                        cuda_lib_paths.append(os.path.join(bin_path, f"cudart64_{major}.dll"))
                        # Try: cudart64_110.dll (major + minor for 11.x)
                        if major == '11':
                            cuda_lib_paths.append(os.path.join(bin_path, f"cudart64_{major}{minor}0.dll"))
        # Fallback names
        cuda_lib_paths.extend(["cudart64_13.dll", "cudart64_12.dll", "cudart64_110.dll",
                               "cudart64_11.dll", "cudart.dll"])
    elif platform.system() == "Linux":
        cuda_lib_paths = ["/usr/local/cuda/lib64/libcudart.so", "/usr/lib/x86_64-linux-gnu/libcudart.so",
                          "libcudart.so", "libcudart.so.12", "libcudart.so.11.0"]
    elif platform.system() == "Darwin":
        cuda_lib_paths = ["/usr/local/cuda/lib/libcudart.dylib", "libcudart.dylib"]

    for lib_path in cuda_lib_paths:
        try:
            _cuda_lib = ctypes.CDLL(lib_path)
            logger.info(f"[CUDA-GL] Loaded CUDA runtime: {lib_path}")
            break
        except OSError as e:
            logger.debug(f"[CUDA-GL] Failed to load {lib_path}: {e}")
            continue

    if _cuda_lib is None:
        logger.debug("[CUDA-GL] CUDA runtime library not found")
        _cuda_funcs = {}
        return False

    # Define function signatures
    try:
        # cudaGraphicsRegisterFlags
        cudaGraphicsRegisterFlagsNone = 0
        cudaGraphicsRegisterFlagsReadOnly = 1
        cudaGraphicsRegisterFlagsWriteDiscard = 2
        cudaGraphicsRegisterFlagsSurfaceLoadStore = 4
        cudaGraphicsRegisterFlagsTextureGather = 8

        # cudaError_t cudaGraphicsGLRegisterImage(cudaGraphicsResource **resource,
        #     GLuint image, GLenum target, unsigned int flags)
        _cuda_lib.cudaGraphicsGLRegisterImage.restype = ctypes.c_int
        _cuda_lib.cudaGraphicsGLRegisterImage.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),  # cudaGraphicsResource**
            ctypes.c_uint,  # GLuint image
            ctypes.c_uint,  # GLenum target
            ctypes.c_uint   # unsigned int flags
        ]

        # cudaError_t cudaGraphicsMapResources(int count,
        #     cudaGraphicsResource **resources, cudaStream_t stream)
        _cuda_lib.cudaGraphicsMapResources.restype = ctypes.c_int
        _cuda_lib.cudaGraphicsMapResources.argtypes = [
            ctypes.c_int,  # count
            ctypes.POINTER(ctypes.c_void_p),  # resources
            ctypes.c_void_p  # stream
        ]

        # cudaError_t cudaGraphicsSubResourceGetMappedArray(cudaArray_t *array,
        #     cudaGraphicsResource *resource, unsigned int arrayIndex,
        #     unsigned int mipLevel)
        _cuda_lib.cudaGraphicsSubResourceGetMappedArray.restype = ctypes.c_int
        _cuda_lib.cudaGraphicsSubResourceGetMappedArray.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),  # cudaArray_t*
            ctypes.c_void_p,  # resource
            ctypes.c_uint,  # arrayIndex
            ctypes.c_uint   # mipLevel
        ]

        # cudaError_t cudaMemcpy2DToArray(cudaArray_t dst, size_t dstXInBytes,
        #     size_t dstY, const void *src, size_t srcPitch, size_t width,
        #     size_t height, cudaMemcpyKind kind)
        _cuda_lib.cudaMemcpy2DToArray.restype = ctypes.c_int
        _cuda_lib.cudaMemcpy2DToArray.argtypes = [
            ctypes.c_void_p,  # dst
            ctypes.c_size_t,  # dstXInBytes
            ctypes.c_size_t,  # dstY
            ctypes.c_void_p,  # src
            ctypes.c_size_t,  # srcPitch
            ctypes.c_size_t,  # width
            ctypes.c_size_t,  # height
            ctypes.c_int     # kind (cudaMemcpyDeviceToDevice = 2)
        ]

        # cudaError_t cudaGraphicsUnmapResources(int count,
        #     cudaGraphicsResource **resources, cudaStream_t stream)
        _cuda_lib.cudaGraphicsUnmapResources.restype = ctypes.c_int
        _cuda_lib.cudaGraphicsUnmapResources.argtypes = [
            ctypes.c_int,  # count
            ctypes.POINTER(ctypes.c_void_p),  # resources
            ctypes.c_void_p  # stream
        ]

        _cuda_funcs = {
            'cudaGraphicsGLRegisterImage': _cuda_lib.cudaGraphicsGLRegisterImage,
            'cudaGraphicsMapResources': _cuda_lib.cudaGraphicsMapResources,
            'cudaGraphicsSubResourceGetMappedArray': _cuda_lib.cudaGraphicsSubResourceGetMappedArray,
            'cudaMemcpy2DToArray': _cuda_lib.cudaMemcpy2DToArray,
            'cudaGraphicsUnmapResources': _cuda_lib.cudaGraphicsUnmapResources,
            'flags': {
                'WriteDiscard': cudaGraphicsRegisterFlagsWriteDiscard
            }
        }
        _CUDA_GL_INTEROP_AVAILABLE = True
        logger.info("[CUDA-GL] CUDA-OpenGL Interop functions loaded successfully")
        return True

    except AttributeError as e:
        logger.debug(f"[CUDA-GL] CUDA-OpenGL Interop functions not found: {e}")
        _cuda_funcs = {}
        return False

# Try to initialize on import
try:
    _init_cuda_gl_interop_lib()
except Exception as e:
    logger.debug(f"[CUDA-GL] Failed to initialize: {e}")


def create_opengl_video_widget_class():
    """
    Create the OpenGLVideoWidget class only if OpenGL is available.

    Returns:
        OpenGLVideoWidget class or None if OpenGL is not available
    """
    if not OPENGL_AVAILABLE or QOpenGLWidget is None:
        return None

    # NV12 YUV Shader sources (for GPU color space conversion)
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
    uniform sampler2D u_texture;
    uniform sampler2D v_texture;
    void main() {
        mediump float y = texture2D(y_texture, v_texCoord).r;
        mediump float u = texture2D(u_texture, v_texCoord).r - 0.5;
        mediump float v = texture2D(v_texture, v_texCoord).r - 0.5;
        highp float r = y + 1.402 * v;
        highp float g = y - 0.344136 * u - 0.714136 * v;
        highp float b = y + 1.772 * u;
        gl_FragColor = vec4(r, g, b, 1.0);
    }
    """

    class OpenGLVideoWidget(QOpenGLWidget, InputHandler, CoordinateMapper):
        """
        OpenGL-based video widget using GPU acceleration.

        Like official scrcpy, this uses GPU textures for rendering,
        avoiding expensive CPU-side image copying.

        Supports both RGB (CPU conversion) and NV12 (GPU conversion) formats.
        """

        # Signal for thread-safe repaint triggering
        _repaint_requested = Signal()

        # Class-level frame counter
        _frame_count = 0

        def __init__(self, parent=None):
            """Initialize OpenGL video widget."""
            super().__init__(parent)

            # Initialize base classes
            InputHandler.__init__(self)
            CoordinateMapper.__init__(self)

            # Connect signal to repaint slot (thread-safe)
            self._repaint_requested.connect(self._do_repaint)

            # Thread lock to protect frame data access
            self._frame_lock = QMutex()

            # Direct access to DelayBuffer (set by client)
            self._delay_buffer: Optional['DelayBuffer'] = None

            # Store numpy array (decoder thread writes, GPU reads)
            self._frame_array: Optional[np.ndarray] = None
            self._frame_width: int = 0
            self._frame_height: int = 0

            # OpenGL texture IDs
            self._texture_id: Optional[int] = None
            self._texture_width: int = 0
            self._texture_height: int = 0

            # NV12 GPU rendering support
            self._y_texture_id: Optional[int] = None
            self._u_texture_id: Optional[int] = None
            self._v_texture_id: Optional[int] = None
            self._nv12_shader: Optional['QOpenGLShaderProgram'] = None
            self._nv12_initialized: bool = False
            self._frame_format: int = 0  # 0=RGB, 1=NV12

            # NV12 texture size tracking (for glTexSubImage2D optimization)
            self._nv12_y_tex_width: int = 0
            self._nv12_y_tex_height: int = 0
            self._nv12_uv_tex_width: int = 0
            self._nv12_uv_tex_height: int = 0

            # Track if we have a new frame to display
            self._has_new_frame: bool = False

            # Frame counter
            self._frame_count = 0

            # Control queue (set by client)
            self._control_queue: Optional["ControlMessageQueue"] = None

            # Consume callback (called after frame is rendered)
            self._consume_callback: Optional[callable] = None

            # Frame size change callback (called when frame resolution changes)
            self._frame_size_changed_callback: Optional[callable] = None

            # Mouse state tracking
            self._mouse_buttons_state: int = 0
            self._last_position: Tuple[int, int] = (0, 0)

            # Input mode
            self._mouse_hover: bool = False
            self.setMouseTracking(True)
            self.setFocusPolicy(Qt.StrongFocus)
            self.setCursor(Qt.ArrowCursor)

            # Configure surface format for OpenGL
            format = QSurfaceFormat()

            # Enable vertical synchronization (V-Sync)
            format.setSwapInterval(1)  # 1 = wait for v-sync

            # Explicitly use double buffering
            format.setSwapBehavior(QSurfaceFormat.DoubleBuffer)

            format.setDepthBufferSize(24)
            format.setStencilBufferSize(8)
            # MSAA disabled for video - video doesn't need anti-aliasing
            # and MSAA significantly increases GPU/CPU overhead
            # format.setSamples(4)  # Disabled for performance
            self.setFormat(format)

            # Timer for periodic updates (ensures continuous rendering)
            # 16ms matches typical 60fps video - no need for faster updates
            if QTimer is not None:
                self._update_timer = QTimer()
                self._update_timer.timeout.connect(self._on_update_timer)
                self._update_timer.start(16)  # 16ms = ~60fps (matches video)
                logger.info("[OPENGL] Timer started (16ms interval, ~60fps)")
            else:
                self._update_timer = None
                logger.warning("QTimer not available, OpenGL widget may not update continuously")

        def initializeGL(self) -> None:
            """Initialize OpenGL resources. Called automatically by Qt."""
            logger.info("Initializing OpenGL...")

            # Set pixel alignment for texture upload
            glPixelStorei(GL_UNPACK_ALIGNMENT, 1)

            # Generate RGB texture (for CPU conversion fallback)
            self._texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self._texture_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

            # Generate NV12 textures (Y, U, V)
            self._y_texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self._y_texture_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

            self._u_texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self._u_texture_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

            self._v_texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self._v_texture_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

            # Initialize NV12 shader
            if SHADER_AVAILABLE and QOpenGLShader is not None:
                self._nv12_shader = QOpenGLShaderProgram(self)
                if self._nv12_shader.addShaderFromSourceCode(QOpenGLShader.Vertex, NV12_VERTEX_SHADER):
                    if self._nv12_shader.addShaderFromSourceCode(QOpenGLShader.Fragment, NV12_FRAGMENT_SHADER):
                        if self._nv12_shader.link():
                            self._nv12_initialized = True
                            logger.info("NV12 GPU shader initialized successfully")
                        else:
                            logger.warning(f"NV12 shader link failed: {self._nv12_shader.log()}")
                    else:
                        logger.warning(f"NV12 fragment shader failed: {self._nv12_shader.log()}")
                else:
                    logger.warning(f"NV12 vertex shader failed: {self._nv12_shader.log()}")

            # Initialize CUDA-OpenGL Interop resources (optional, only for zero-copy mode)
            self._cuda_gl_resources = None
            self._cuda_interop_initialized = False
            if _CUDA_GL_INTEROP_AVAILABLE and _ZERO_COPY_GPU_ENABLED:
                try:
                    self._init_cuda_gl_interop()
                except Exception as e:
                    logger.warning(f"CUDA-OpenGL Interop init failed: {e}")

            logger.info("OpenGL initialized successfully")

        def _init_cuda_gl_interop(self) -> None:
            """
            Initialize CUDA-OpenGL Interop resources.

            This registers OpenGL textures with CUDA so that CUDA kernels
            can write directly to them, enabling true zero-copy GPU rendering.
            """
            import ctypes

            if _cuda_funcs is None or not _cuda_funcs:
                logger.warning("[CUDA-GL] CUDA functions not available")
                return

            logger.info("[CUDA-GL] Starting initialization...")
            logger.info(f"[CUDA-GL] Texture IDs: Y={self._y_texture_id}, U={self._u_texture_id}, V={self._v_texture_id}")

            # 检查CUDA设备信息
            try:
                import cupy as cp
                device = cp.cuda.Device()
                logger.info(f"[CUDA-GL] CUDA device: {device.id}, name: {device.name.decode()}")
                logger.info(f"[CUDA-GL] CUDA compute capability: {device.compute_capability}")
            except Exception as e:
                logger.warning(f"[CUDA-GL] Failed to get CUDA device info: {e}")

            # 必须先分配存储空间才能注册到CUDA
            # 使用最大可能的分辨率（4K）
            max_width, max_height = 4096, 4096

            # 分配Y纹理存储空间
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D, self._y_texture_id)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_LUMINANCE, max_width, max_height, 0,
                        GL_LUMINANCE, GL_UNSIGNED_BYTE, None)
            logger.info(f"[CUDA-GL] Allocated Y texture storage: {max_width}x{max_height}")

            # 分配U纹理存储空间
            glActiveTexture(GL_TEXTURE1)
            glBindTexture(GL_TEXTURE_2D, self._u_texture_id)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_LUMINANCE, max_width // 2, max_height // 2, 0,
                        GL_LUMINANCE, GL_UNSIGNED_BYTE, None)
            logger.info(f"[CUDA-GL] Allocated U texture storage: {max_width // 2}x{max_height // 2}")

            # 分配V纹理存储空间
            glActiveTexture(GL_TEXTURE2)
            glBindTexture(GL_TEXTURE_2D, self._v_texture_id)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_LUMINANCE, max_width // 2, max_height // 2, 0,
                        GL_LUMINANCE, GL_UNSIGNED_BYTE, None)
            logger.info(f"[CUDA-GL] Allocated V texture storage: {max_width // 2}x{max_height // 2}")

            glActiveTexture(GL_TEXTURE0)

            # Register Y, U, V textures with CUDA
            # Use WriteDiscard flag since we only write to textures (no read)
            self._cuda_gl_resources = []
            flags = _cuda_funcs['flags']['WriteDiscard']
            # GL_TEXTURE_2D constant for CUDA
            GL_TEXTURE_2D_CUDA = 0x0DE1

            for tex_id, name in [(self._y_texture_id, 'Y'),
                                  (self._u_texture_id, 'U'),
                                  (self._v_texture_id, 'V')]:
                # cudaGraphicsGLRegisterImage(texture, target, flags)
                # Returns a cudaGraphicsResource pointer
                resource = ctypes.c_void_p()
                err = _cuda_funcs['cudaGraphicsGLRegisterImage'](
                    ctypes.byref(resource),
                    ctypes.c_uint(int(tex_id)),
                    ctypes.c_uint(GL_TEXTURE_2D_CUDA),
                    ctypes.c_uint(flags)
                )
                if err != 0:
                    logger.error(f"[CUDA-GL] Failed to register {name} texture: error={err}")
                    # 注册失败，需要清理已注册的资源
                    self._cleanup_cuda_gl_resources()
                    return
                self._cuda_gl_resources.append(resource)
                logger.info(f"[CUDA-GL] Registered {name} texture: id={tex_id}, resource={resource.value}")

            self._cuda_interop_initialized = True
            logger.info("[CUDA-GL] Interop initialized successfully")

        def _cleanup_cuda_gl_resources(self) -> None:
            """Clean up CUDA-OpenGL Interop resources on failure."""
            if self._cuda_gl_resources:
                import ctypes
                for resource in self._cuda_gl_resources:
                    try:
                        # cudaGraphicsUnregisterResource
                        _cuda_lib.cudaGraphicsUnregisterResource(resource)
                    except:
                        pass
            self._cuda_gl_resources = None
            self._cuda_interop_initialized = False

            # 重新创建纹理（不分配存储空间，让CPU路径在渲染时分配）
            glDeleteTextures([self._y_texture_id, self._u_texture_id, self._v_texture_id])
            self._y_texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self._y_texture_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

            self._u_texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self._u_texture_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

            self._v_texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self._v_texture_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

            logger.info("[CUDA-GL] Cleaned up and recreated textures for CPU path")

        def _upload_gpu_to_opengl(self, y_gpu, uv_gpu, width: int, height: int) -> bool:
            """
            Upload GPU arrays directly to OpenGL textures using CUDA-OpenGL Interop.

            This is the true zero-copy path: GPU decode -> GPU memory -> GPU texture.
            No CPU transfer involved.

            Args:
                y_gpu: CuPy array for Y plane (height, width) on GPU
                uv_gpu: CuPy array for UV plane (height//2, width) on GPU
                width: Frame width
                height: Frame height

            Returns:
                True if successful, False if fallback to CPU needed
            """
            import ctypes

            if not self._cuda_interop_initialized or self._cuda_gl_resources is None:
                return False

            if _cuda_funcs is None or not _cuda_funcs:
                return False

            # 使用CuPy的CUDA runtime来确保context正确
            try:
                import cupy as cp
            except ImportError:
                logger.error("[CUDA-GL] CuPy not available")
                return False

            try:
                # Map CUDA resources for access
                # Convert resource list to ctypes array
                resources_array = (ctypes.c_void_p * len(self._cuda_gl_resources))()
                for i, res in enumerate(self._cuda_gl_resources):
                    resources_array[i] = res

                # cudaGraphicsMapResources(count, resources, stream=0)
                err = _cuda_funcs['cudaGraphicsMapResources'](
                    ctypes.c_int(len(self._cuda_gl_resources)),
                    resources_array,
                    ctypes.c_void_p(0)  # NULL stream
                )
                if err != 0:
                    logger.warning(f"[CUDA-GL] Failed to map resources: error={err}")
                    return False

                # 获取CUDA数组（使用try-finally确保unmap）
                try:
                    # Get mapped arrays for each texture
                    y_resource, u_resource, v_resource = self._cuda_gl_resources

                    # Get CUDA array from graphics resource
                    def get_mapped_array(resource):
                        arr = ctypes.c_void_p()
                        err = _cuda_funcs['cudaGraphicsSubResourceGetMappedArray'](
                            ctypes.byref(arr),
                            resource,
                            ctypes.c_uint(0),  # arrayIndex
                            ctypes.c_uint(0)   # mipLevel
                        )
                        if err != 0:
                            raise RuntimeError(f"GetMappedArray failed: {err}")
                        return arr

                    y_array = get_mapped_array(y_resource)
                    u_array = get_mapped_array(u_resource)
                    v_array = get_mapped_array(v_resource)

                    # 调试：打印数组信息
                    if not hasattr(self, '_cuda_gl_debug_logged'):
                        self._cuda_gl_debug_logged = True
                        logger.info(f"[CUDA-GL] y_array ptr: {y_array.value}")
                        logger.info(f"[CUDA-GL] y_gpu ptr: {y_gpu.data.ptr}, strides: {y_gpu.strides}")
                        # 检查y_gpu的内存类型
                        try:
                            mem_info = cp.cuda.runtime.pointerGetAttributes(y_gpu.data.ptr)
                            logger.info(f"[CUDA-GL] y_gpu memory type: {mem_info.type}")
                        except Exception as e:
                            logger.info(f"[CUDA-GL] pointerGetAttributes failed: {e}")

                    # Prepare Y plane data
                    # 注意：PyAV的DLPack导出的内存可能与CUDA runtime不直接兼容
                    # 使用CuPy分配一个中间缓冲区来确保兼容性
                    y_height = height
                    y_width = width

                    # 创建CuPy分配的标准设备内存缓冲区
                    y_buf = cp.empty((y_height, y_width), dtype=cp.uint8)

                    # 调试：检查缓冲区属性
                    if not hasattr(self, '_cupy_buf_debug_logged'):
                        self._cupy_buf_debug_logged = True
                        logger.info(f"[CUDA-GL] y_buf ptr: {y_buf.data.ptr}, strides: {y_buf.strides}")
                        try:
                            mem_info = cp.cuda.runtime.pointerGetAttributes(y_buf.data.ptr)
                            logger.info(f"[CUDA-GL] y_buf memory type: {mem_info.type}")
                        except Exception as e:
                            logger.info(f"[CUDA-GL] y_buf pointerGetAttributes failed: {e}")

                    # 使用CuPy的切片一次性复制（比逐行快得多）
                    y_buf[:] = y_gpu[:y_height, :y_width]

                    # 同步确保复制完成
                    cp.cuda.Stream.null.synchronize()

                    # 调试：尝试用cudaMemcpy测试cudaArray是否可写
                    if not hasattr(self, '_cuda_memcpy_test_done'):
                        self._cuda_memcpy_test_done = True
                        # 尝试简单的cudaMemcpy（不是2D版本）
                        test_data = cp.array([128], dtype=cp.uint8)
                        try:
                            # cudaMemcpyToArray(dst, wOffset, hOffset, src, count, kind)
                            if hasattr(_cuda_lib, 'cudaMemcpyToArray'):
                                _cuda_lib.cudaMemcpyToArray.restype = ctypes.c_int
                                _cuda_lib.cudaMemcpyToArray.argtypes = [
                                    ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t,
                                    ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int
                                ]
                                err = _cuda_lib.cudaMemcpyToArray(
                                    y_array, 0, 0,
                                    ctypes.c_void_p(int(test_data.data.ptr)),
                                    ctypes.c_size_t(1),
                                    ctypes.c_int(2)
                                )
                                logger.info(f"[CUDA-GL] cudaMemcpyToArray test: error={err}")
                            else:
                                logger.info("[CUDA-GL] cudaMemcpyToArray not available")
                        except Exception as e:
                            logger.info(f"[CUDA-GL] cudaMemcpyToArray test failed: {e}")

                    # 现在使用标准缓冲区进行拷贝
                    err = _cuda_funcs['cudaMemcpy2DToArray'](
                        y_array,
                        ctypes.c_size_t(0),
                        ctypes.c_size_t(0),
                        ctypes.c_void_p(int(y_buf.data.ptr)),
                        ctypes.c_size_t(y_width),  # 标准stride
                        ctypes.c_size_t(y_width),
                        ctypes.c_size_t(y_height),
                        ctypes.c_int(2)  # cudaMemcpyDeviceToDevice
                    )
                    if err != 0:
                        logger.warning(f"[CUDA-GL] Y plane copy failed: error={err}, ptr={y_buf.data.ptr}")
                        return False

                    # Split UV plane into U and V
                    uv_height = height // 2
                    uv_width = width // 2

                    if uv_gpu.ndim == 3:
                        # 3维格式: (height/2, width/2, 2)
                        # uv[:, :, 0] = U, uv[:, :, 1] = V
                        u_gpu = uv_gpu[:, :, 0]
                        v_gpu = uv_gpu[:, :, 1]
                    else:
                        # 2维交错格式
                        u_gpu = uv_gpu[:, ::2]
                        v_gpu = uv_gpu[:, 1::2]

                    # 创建UV缓冲区并复制
                    u_buf = cp.empty((uv_height, uv_width), dtype=cp.uint8)
                    v_buf = cp.empty((uv_height, uv_width), dtype=cp.uint8)
                    u_buf[:] = u_gpu[:, :uv_width]
                    v_buf[:] = v_gpu[:, :uv_width]

                    # Copy U and V planes
                    err = _cuda_funcs['cudaMemcpy2DToArray'](
                        u_array,
                        ctypes.c_size_t(0),
                        ctypes.c_size_t(0),
                        ctypes.c_void_p(int(u_buf.data.ptr)),
                        ctypes.c_size_t(uv_width),
                        ctypes.c_size_t(uv_width),
                        ctypes.c_size_t(uv_height),
                        ctypes.c_int(2)
                    )
                    if err != 0:
                        logger.warning(f"[CUDA-GL] U plane copy failed: error={err}")

                    err = _cuda_funcs['cudaMemcpy2DToArray'](
                        v_array,
                        ctypes.c_size_t(0),
                        ctypes.c_size_t(0),
                        ctypes.c_void_p(int(v_buf.data.ptr)),
                        ctypes.c_size_t(uv_width),
                        ctypes.c_size_t(uv_width),
                        ctypes.c_size_t(uv_height),
                        ctypes.c_int(2)
                    )
                    if err != 0:
                        logger.warning(f"[CUDA-GL] V plane copy failed: error={err}")

                    return True

                finally:
                    # 始终unmap资源
                    err = _cuda_funcs['cudaGraphicsUnmapResources'](
                        ctypes.c_int(len(self._cuda_gl_resources)),
                        resources_array,
                        ctypes.c_void_p(0)
                    )
                    if err != 0:
                        logger.warning(f"[CUDA-GL] Failed to unmap resources: error={err}")

            except Exception as e:
                logger.warning(f"[CUDA-GL] GPU upload failed: {e}")
                return False

        def _on_update_timer(self) -> None:
            """Called periodically by QTimer to trigger repaint for continuous rendering."""
            # Always call update() to ensure paintGL is called periodically
            # This ensures smooth video playback even if frame updates are delayed
            self.update()
            # Log periodically for debugging
            count = getattr(self, '_timer_count', 0) + 1
            self._timer_count = count
            if count % 300 == 0:  # Every ~5 seconds
                logger.info(f"[OPENGL] Timer tick #{count} (calling update())")

        def resizeGL(self, w: int, h: int) -> None:
            """Handle window resize."""
            glViewport(0, 0, w, h)

        def paintGL(self) -> None:
            """Render the video frame using OpenGL."""
            # Track paintGL calls (for debugging)
            self._paint_count = getattr(self, '_paint_count', 0) + 1
            current_time = time.time()

            # Log paintGL calls periodically (separate from frame consumption)
            if self._paint_count % 60 == 1:
                frame_count = getattr(self, '_frame_consume_count', 0)
                logger.info(f"[OPENGL] paintGL #{self._paint_count}, frames_consumed={frame_count}")

            # === 性能监控: 清屏 ===
            with _profile_time('glClear'):
                glClearColor(0.0, 0.0, 0.0, 1.0)
                glClear(GL_COLOR_BUFFER_BIT)

            # Check if we have DelayBuffer
            if self._delay_buffer is None:
                return

            # Check if we have texture data
            if self._texture_id is None:
                return

            # === 性能监控: consume ===
            with _profile_time('consume'):
                try:
                    result = self._delay_buffer.consume()
                    if result is not None:
                        # Increment frame consumption counter
                        self._frame_consume_count = getattr(self, '_frame_consume_count', 0) + 1
                        frame_num = self._frame_consume_count

                        # Record consume time for end-to-end latency tracking
                        consume_time = time.time()

                        # DelayBuffer returns FrameWithMetadata, extract frame
                        new_frame = result.frame if hasattr(result, 'frame') else result

                        # Calculate and log end-to-end latency
                        e2e_client_ms = 0.0
                        e2e_device_ms = 0.0

                        # Client-side E2E: UDP recv → consume
                        if hasattr(result, 'udp_recv_time') and result.udp_recv_time > 0:
                            e2e_client_ms = (consume_time - result.udp_recv_time) * 1000

                        # Full E2E: Device send → consume (requires clock sync)
                        if hasattr(result, 'send_time_ns') and result.send_time_ns > 0:
                            # Convert consume_time to nanoseconds and calculate latency
                            consume_time_ns = consume_time * 1e9
                            e2e_device_ms = (consume_time_ns - result.send_time_ns) / 1e6

                        packet_id = getattr(result, 'packet_id', -1)

                        # Log every frame for first 10, then every 60 frames
                        if frame_num <= 10 or frame_num % 60 == 0:
                            if e2e_device_ms > 0:
                                logger.info(f"[E2E] Frame #{frame_num}: packet_id={packet_id}, "
                                           f"Device→consume={e2e_device_ms:.1f}ms, "
                                           f"UDP→consume={e2e_client_ms:.1f}ms")
                            elif e2e_client_ms > 0:
                                logger.info(f"[E2E] Frame #{frame_num}: packet_id={packet_id}, "
                                           f"UDP→consume={e2e_client_ms:.1f}ms")

                        if self._paint_count <= 10 or self._paint_count % 60 == 0:
                            if e2e_device_ms > 0:
                                logger.info(f"[E2E-FULL] Frame #{self._paint_count}: packet_id={packet_id}, "
                                           f"Device→render={e2e_device_ms:.1f}ms (full pipeline)")
                            elif e2e_client_ms > 0:
                                logger.info(f"[E2E] Frame #{self._paint_count}: packet_id={packet_id}, "
                                           f"UDP→consume={e2e_client_ms:.1f}ms")

                        old_width, old_height = self._frame_width, self._frame_height
                        self._frame_lock.lock()
                        self._frame_array = new_frame

                        # Detect frame format: NV12 is dict with Y/U/V planes, RGB is (H, W, 3)
                        if isinstance(new_frame, dict) and ('y' in new_frame or 'y_gpu' in new_frame):
                            # NV12 format as dict with separate Y, U, V planes (CPU or GPU)
                            self._frame_format = 1  # NV12
                            if 'y_gpu' in new_frame:
                                # GPU零拷贝模式
                                self._frame_width = new_frame['width']
                                self._frame_height = new_frame['height']
                            else:
                                # CPU模式
                                self._frame_width = new_frame['y'].shape[1]
                                self._frame_height = new_frame['y'].shape[0]
                            CoordinateMapper.set_frame_size(self, self._frame_width, self._frame_height)
                        elif hasattr(new_frame, 'shape') and len(new_frame.shape) >= 2:
                            # Array format (RGB or NV12)
                            if len(new_frame.shape) == 3 and new_frame.shape[2] == 3:
                                # RGB format (H, W, 3)
                                self._frame_format = 0  # RGB
                                self._frame_width = new_frame.shape[1]
                                self._frame_height = new_frame.shape[0]
                            elif len(new_frame.shape) == 2:
                                # NV12 format (H*3/2, W) - semi-planar
                                self._frame_format = 1  # NV12
                                self._frame_width = new_frame.shape[1]
                                self._frame_height = int(new_frame.shape[0] * 2 / 3)
                            else:
                                # Unknown format, assume RGB
                                self._frame_format = 0
                                self._frame_width = new_frame.shape[1] if len(new_frame.shape) > 1 else 0
                                self._frame_height = new_frame.shape[0] if len(new_frame.shape) > 0 else 0
                            CoordinateMapper.set_frame_size(self, self._frame_width, self._frame_height)
                        else:
                            # Unknown format, log warning
                            logger.warning(f"[OPENGL] Unknown frame format: {type(new_frame)}")
                        self._frame_lock.unlock()

                        # Check if frame size changed (device rotation)
                        if (old_width, old_height) != (self._frame_width, self._frame_height):
                            if old_width > 0 and old_height > 0:  # Not first frame
                                logger.info(
                                    f"[OPENGL] Frame size changed: "
                                    f"{old_width}x{old_height} -> {self._frame_width}x{self._frame_height}"
                                )
                                # Notify parent window to resize
                                if self._frame_size_changed_callback:
                                    self._frame_size_changed_callback(self._frame_width, self._frame_height)
                except Exception as e:
                    logger.error(f"[OPENGL] Error consuming from DelayBuffer: {e}")

            # === 性能监控: 获取帧 ===
            with _profile_time('get_frame'):
                self._frame_lock.lock()
                frame_array = self._frame_array
                width = self._frame_width
                height = self._frame_height
                frame_format = self._frame_format
                self._frame_lock.unlock()

            if frame_array is None or width == 0 or height == 0:
                return

            # === 性能监控: 投影设置 ===
            with _profile_time('projection'):
                widget_size = self.size()
                glMatrixMode(GL_PROJECTION)
                glLoadIdentity()
                glOrtho(0, widget_size.width(), widget_size.height(), 0, -1, 1)

                glMatrixMode(GL_MODELVIEW)
                glLoadIdentity()

            # Calculate aspect ratio and render rectangle
            if width > 0 and height > 0:
                scale_x = widget_size.width() / width
                scale_y = widget_size.height() / height
                scale = min(scale_x, scale_y)

                render_w = int(width * scale)
                render_h = int(height * scale)
                x = (widget_size.width() - render_w) // 2
                y = (widget_size.height() - render_h) // 2
            else:
                x = y = render_w = render_h = 0

            # Render based on frame format
            if frame_format == 1:
                # NV12 GPU rendering (required - no CPU fallback for debugging)
                if not self._nv12_initialized:
                    logger.error("[OPENGL] NV12 mode required but shader not initialized!")
                    return
                with _profile_time('paint_nv12'):
                    self._paint_nv12(frame_array, width, height, x, y, render_w, render_h)
            else:
                # RGB rendering (CPU conversion fallback)
                # Log warning if we expected NV12
                if hasattr(self, '_expected_nv12') and self._expected_nv12:
                    logger.error(f"[OPENGL] Expected NV12 but got format={frame_format}, type={type(frame_array)}")
                with _profile_time('paint_rgb'):
                    self._paint_rgb(frame_array, width, height, x, y, render_w, render_h)

            # 定期输出性能报告
            if PROFILE_OPENGL and self._paint_count % 300 == 0:
                _profile_report()

        def _paint_rgb(self, frame_array: np.ndarray, width: int, height: int,
                       x: int, y: int, render_w: int, render_h: int) -> None:
            """Render RGB frame using single texture (CPU color conversion fallback)."""
            # Ensure contiguous array for OpenGL
            if not frame_array.flags['C_CONTIGUOUS']:
                frame_array = np.ascontiguousarray(frame_array)

            # Get data pointer
            data_ptr = frame_array.ctypes.data_as(c_void_p)

            # Update texture if needed
            if self._texture_width != width or self._texture_height != height:
                # Re-create texture for new size
                glBindTexture(GL_TEXTURE_2D, self._texture_id)
                glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, width, height, 0,
                             GL_RGB, GL_UNSIGNED_BYTE, data_ptr)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
                self._texture_width = width
                self._texture_height = height
            else:
                # Update texture data (fast - just upload to GPU)
                glBindTexture(GL_TEXTURE_2D, self._texture_id)
                glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, width, height,
                              GL_RGB, GL_UNSIGNED_BYTE, data_ptr)

            # Draw textured quad (legacy OpenGL)
            glEnable(GL_TEXTURE_2D)
            glBindTexture(GL_TEXTURE_2D, self._texture_id)

            # Set color to white (so texture shows correctly)
            glColor3f(1.0, 1.0, 1.0)

            glBegin(GL_QUADS)
            glTexCoord2f(0.0, 0.0)
            glVertex2f(x, y)
            glTexCoord2f(1.0, 0.0)
            glVertex2f(x + render_w, y)
            glTexCoord2f(1.0, 1.0)
            glVertex2f(x + render_w, y + render_h)
            glTexCoord2f(0.0, 1.0)
            glVertex2f(x, y + render_h)
            glEnd()

            glDisable(GL_TEXTURE_2D)

        def _paint_nv12(self, frame_array, width: int, height: int,
                        x: int, y: int, render_w: int, render_h: int) -> None:
            """Render NV12 frame using Y/U/V textures and GPU shader."""
            if not self._nv12_initialized or self._nv12_shader is None:
                logger.warning("[OPENGL] NV12 shader not initialized, falling back to RGB")
                return

            # Track zero-copy mode usage
            use_zero_copy = False

            # Initialize debug counter (used in all paths)
            nv12_debug_count = getattr(self, '_nv12_debug_count', 0) + 1
            self._nv12_debug_count = nv12_debug_count

            # Handle NV12 data format
            if isinstance(frame_array, dict):
                # Check if GPU arrays (zero-copy mode)
                if frame_array.get('is_gpu', False):
                    y_gpu = frame_array['y_gpu']
                    uv_gpu = frame_array['uv_gpu']

                    # 零拷贝模式：必须使用CUDA-OpenGL Interop
                    if not self._cuda_interop_initialized:
                        logger.error("[OPENGL] GPU frame received but CUDA-GL Interop not initialized!")
                        return

                    if self._upload_gpu_to_opengl(y_gpu, uv_gpu, width, height):
                        use_zero_copy = True
                        if nv12_debug_count <= 3:
                            logger.info(f"[OPENGL] GPU NV12 via CUDA-GL Interop: y={y_gpu.shape}, uv={uv_gpu.shape}")
                    else:
                        logger.error("[OPENGL] CUDA-GL Interop upload failed! No CPU fallback in zero-copy mode.")
                        return
                else:
                    # CPU arrays (standard path)
                    # Separate Y, U, V planes (from decoder, padding already removed)
                    y_plane = frame_array['y']
                    u_plane = frame_array['u']
                    v_plane = frame_array['v']
                    # Use actual array dimensions (padding already removed by decoder)
                    y_tex_width = y_plane.shape[1] if len(y_plane.shape) > 1 else width
                    uv_tex_width = u_plane.shape[1] if len(u_plane.shape) > 1 else width // 2

                    # Debug logging (periodic) - only for CPU path
                    if nv12_debug_count <= 3 or nv12_debug_count % 60 == 0:
                        # Check array properties
                        logger.info(f"[OPENGL] NV12 dict: y_shape={y_plane.shape}, u_shape={u_plane.shape}, "
                                   f"v_shape={v_plane.shape}, y_tex_w={y_tex_width}, uv_tex_w={uv_tex_width}")
                        logger.info(f"[OPENGL] NV12 data: y_contig={y_plane.flags['C_CONTIGUOUS']}, "
                                   f"u_contig={u_plane.flags['C_CONTIGUOUS']}, v_contig={v_plane.flags['C_CONTIGUOUS']}")
                        logger.info(f"[OPENGL] NV12 render: x={x}, y={y}, w={render_w}, h={render_h}, "
                                   f"widget={self.width()}x{self.height()}")
            else:
                # Semi-planar NV12: (H*3/2, W) format (from SHM, may have padding)
                # Y plane: height rows
                # UV plane: height/2 rows (interleaved U,V)
                y_plane = frame_array[:height, :]
                uv_plane = frame_array[height:, :]

                # Extract U and V from interleaved UV
                u_plane = uv_plane[::2, :]
                v_plane = uv_plane[1::2, :]

                # Use frame width (may include padding from SHM)
                y_tex_width = frame_array.shape[1]
                uv_tex_width = y_tex_width // 2

            # Upload textures (skip if already uploaded via CUDA-OpenGL Interop)
            if not use_zero_copy:
                # === 性能监控: 数组处理 ===
                with _profile_time('array_contiguous'):
                    # Ensure contiguous arrays (only needed for CPU path)
                    if not y_plane.flags['C_CONTIGUOUS']:
                        y_plane = np.ascontiguousarray(y_plane)
                    if not u_plane.flags['C_CONTIGUOUS']:
                        u_plane = np.ascontiguousarray(u_plane)
                    if not v_plane.flags['C_CONTIGUOUS']:
                        v_plane = np.ascontiguousarray(v_plane)

                # Calculate actual dimensions
                uv_width = width // 2
                uv_height = height // 2

                # Check if texture size changed (need reallocation)
                y_size_changed = (self._nv12_y_tex_width != y_tex_width or
                                  self._nv12_y_tex_height != height)
                uv_size_changed = (self._nv12_uv_tex_width != uv_tex_width or
                                   self._nv12_uv_tex_height != uv_height)

                # === 性能监控: 纹理上传 ===
                with _profile_time('tex_upload'):
                    # Upload Y texture - only reallocate if size changed
                    glActiveTexture(GL_TEXTURE0)
                    glBindTexture(GL_TEXTURE_2D, self._y_texture_id)
                    if y_size_changed:
                        glTexImage2D(GL_TEXTURE_2D, 0, GL_LUMINANCE, y_tex_width, height, 0,
                                    GL_LUMINANCE, GL_UNSIGNED_BYTE,
                                    y_plane.ctypes.data_as(c_void_p))
                        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
                        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
                        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
                        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
                        self._nv12_y_tex_width = y_tex_width
                        self._nv12_y_tex_height = height
                    else:
                        # Fast path: just update texture data (no reallocation)
                        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, y_tex_width, height,
                                       GL_LUMINANCE, GL_UNSIGNED_BYTE,
                                       y_plane.ctypes.data_as(c_void_p))

                # Upload U texture - only reallocate if size changed
                glActiveTexture(GL_TEXTURE1)
                glBindTexture(GL_TEXTURE_2D, self._u_texture_id)
                if uv_size_changed:
                    glTexImage2D(GL_TEXTURE_2D, 0, GL_LUMINANCE, uv_tex_width, uv_height, 0,
                                GL_LUMINANCE, GL_UNSIGNED_BYTE,
                                u_plane.ctypes.data_as(c_void_p))
                    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
                    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
                    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
                    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
                else:
                    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, uv_tex_width, uv_height,
                                   GL_LUMINANCE, GL_UNSIGNED_BYTE,
                                   u_plane.ctypes.data_as(c_void_p))

                # Upload V texture - only reallocate if size changed
                glActiveTexture(GL_TEXTURE2)
                glBindTexture(GL_TEXTURE_2D, self._v_texture_id)
                if uv_size_changed:
                    glTexImage2D(GL_TEXTURE_2D, 0, GL_LUMINANCE, uv_tex_width, uv_height, 0,
                                GL_LUMINANCE, GL_UNSIGNED_BYTE,
                                v_plane.ctypes.data_as(c_void_p))
                    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
                    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
                    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
                    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
                    self._nv12_uv_tex_width = uv_tex_width
                    self._nv12_uv_tex_height = uv_height
                else:
                    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, uv_tex_width, uv_height,
                                   GL_LUMINANCE, GL_UNSIGNED_BYTE,
                                   v_plane.ctypes.data_as(c_void_p))

            # Log zero-copy mode usage periodically
            nv12_debug_count = getattr(self, '_nv12_debug_count', 0)
            if nv12_debug_count <= 3 or nv12_debug_count % 60 == 0:
                if use_zero_copy:
                    logger.info(f"[OPENGL] Using TRUE ZERO-COPY (CUDA-GL Interop) for frame #{nv12_debug_count}")
                elif isinstance(frame_array, dict) and frame_array.get('is_gpu', False):
                    logger.info(f"[OPENGL] GPU frame with CPU fallback (no interop) for frame #{nv12_debug_count}")

            # Bind shader
            self._nv12_shader.bind()

            # Bind textures to texture units
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D, self._y_texture_id)
            self._nv12_shader.setUniformValue1i("y_texture", 0)

            glActiveTexture(GL_TEXTURE1)
            glBindTexture(GL_TEXTURE_2D, self._u_texture_id)
            self._nv12_shader.setUniformValue1i("u_texture", 1)

            glActiveTexture(GL_TEXTURE2)
            glBindTexture(GL_TEXTURE_2D, self._v_texture_id)
            self._nv12_shader.setUniformValue1i("v_texture", 2)

            # IMPORTANT: Reset to TEXTURE0 for glTexCoord2f to work
            glActiveTexture(GL_TEXTURE0)

            # Draw textured quad with shader
            glEnable(GL_TEXTURE_2D)
            glColor3f(1.0, 1.0, 1.0)

            glBegin(GL_QUADS)
            # Top-left
            glTexCoord2f(0.0, 0.0)
            glVertex2f(x, y)
            # Top-right
            glTexCoord2f(1.0, 0.0)
            glVertex2f(x + render_w, y)
            # Bottom-right
            glTexCoord2f(1.0, 1.0)
            glVertex2f(x + render_w, y + render_h)
            # Bottom-left
            glTexCoord2f(0.0, 1.0)
            glVertex2f(x, y + render_h)
            glEnd()

            glDisable(GL_TEXTURE_2D)

            # Release shader
            self._nv12_shader.release()

            # Reset active texture
            glActiveTexture(GL_TEXTURE0)

        def update_frame(self, frame: np.ndarray) -> None:
            """
            Update the displayed frame. Thread-safe - can be called from any thread.

            DEPRECATED: This method is kept for compatibility but no longer stores frames.
            Frames are now consumed directly from DelayBuffer in paintEvent.

            Args:
                frame: RGB format numpy array (H, W, 3) from decoder - IGNORED
            """
            self._frame_count += 1

            # Log periodically for debugging (use INFO level to ensure visibility)
            if self._frame_count % 60 == 1:
                logger.info(f"[OPENGL] update_frame called (count={self._frame_count})")

            # Mark that new frame is available in DelayBuffer
            self._has_new_frame = True

            # Use signal to trigger repaint in main thread (thread-safe)
            # This is faster than update() which queues to event loop
            self._repaint_requested.emit()

        def _do_repaint(self):
            """Slot for repaint signal - called in main thread."""
            try:
                self.repaint()
            except Exception as e:
                # Fallback to update() if repaint fails
                logger.debug(f"[OPENGL] repaint failed, falling back to update(): {e}")
                self.update()

        def set_consume_callback(self, callback: Optional[callable]) -> None:
            """Set the consume callback to notify when frame has been rendered."""
            self._consume_callback = callback

        def set_delay_buffer(self, delay_buffer: 'DelayBuffer') -> None:
            """Set the DelayBuffer reference for direct frame consumption."""
            self._delay_buffer = delay_buffer
            logger.debug("OpenGLVideoWidget DelayBuffer reference set")

        def set_frame_size_changed_callback(self, callback: Optional[callable]) -> None:
            """Set the callback to notify when frame size changes (device rotation)."""
            self._frame_size_changed_callback = callback

        def set_nv12_mode(self, enabled: bool) -> None:
            """
            Enable or disable NV12 GPU rendering mode.

            When enabled, the widget expects NV12 format frames and uses GPU
            shaders for YUV to RGB color space conversion.

            Args:
                enabled: True to enable NV12 mode, False for RGB mode
            """
            if enabled and not self._nv12_initialized:
                logger.error("[OPENGL] NV12 mode requested but shader not initialized! This will cause rendering failure.")
            self._frame_format = 1 if enabled else 0
            self._expected_nv12 = enabled  # Track expected mode for debugging
            logger.info(f"[OPENGL] NV12 mode {'enabled' if enabled else 'disabled'}, shader_ready={self._nv12_initialized}")

        def is_nv12_supported(self) -> bool:
            """Check if NV12 GPU rendering is supported."""
            return self._nv12_initialized and SHADER_AVAILABLE

        # ========================================================================
        # Mouse Event Handlers
        # ========================================================================

        def mousePressEvent(self, event: QMouseEvent) -> None:
            """Handle mouse press event."""
            if event is None:
                return

            device_x, device_y = self.map_to_device_coords(
                int(event.position().x()),
                int(event.position().y()),
                self.width(),
                self.height()
            )

            if device_x < 0 or device_y < 0:
                return

            button = event.button()
            self._update_mouse_button_state(button, True)

            action_button = self._qt_button_to_android(button)

            self._send_touch_event(
                action=AndroidMotionEventAction.DOWN,
                pointer_id=POINTER_ID_MOUSE,
                position_x=device_x,
                position_y=device_y,
                pressure=1.0,
                action_button=action_button,
            )

        def mouseReleaseEvent(self, event: QMouseEvent) -> None:
            """Handle mouse release event."""
            if event is None:
                return

            device_x, device_y = self.map_to_device_coords(
                int(event.position().x()),
                int(event.position().y()),
                self.width(),
                self.height()
            )

            if device_x < 0 or device_y < 0:
                return

            button = event.button()
            self._update_mouse_button_state(button, False)

            action_button = self._qt_button_to_android(button)

            self._send_touch_event(
                action=AndroidMotionEventAction.UP,
                pointer_id=POINTER_ID_MOUSE,
                position_x=device_x,
                position_y=device_y,
                pressure=0.0,
                action_button=action_button,
            )

        def mouseMoveEvent(self, event: QMouseEvent) -> None:
            """Handle mouse move event."""
            if event is None:
                return

            device_x, device_y = self.map_to_device_coords(
                int(event.position().x()),
                int(event.position().y()),
                self.width(),
                self.height()
            )

            if device_x < 0 or device_y < 0:
                return

            self._last_position = (device_x, device_y)

            # Only send move events if buttons are pressed OR mouse_hover is enabled
            if self._mouse_buttons_state == 0 and not self._mouse_hover:
                return

            # Determine action: MOVE or HOVER_MOVE
            if self._mouse_buttons_state:
                action = AndroidMotionEventAction.MOVE
                pressure = 1.0
            else:
                action = AndroidMotionEventAction.HOVER_MOVE
                pressure = 0.0

            from scrcpy_py_ddlx.core.control import ControlMessage, ControlMessageType

            msg = ControlMessage(ControlMessageType.INJECT_TOUCH_EVENT)
            msg.set_touch_event(
                action=action,
                pointer_id=POINTER_ID_MOUSE,
                position_x=device_x,
                position_y=device_y,
                screen_width=self._device_size[0],
                screen_height=self._device_size[1],
                pressure=pressure,
                action_button=0,
                buttons=self._mouse_buttons_state,
            )
            self._control_queue.put(msg) if self._control_queue else None

        def wheelEvent(self, event: QWheelEvent) -> None:
            """Handle mouse wheel event."""
            if event is None:
                return

            device_x, device_y = self.map_to_device_coords(
                int(event.position().x()),
                int(event.position().y()),
                self.width(),
                self.height()
            )

            if device_x < 0 or device_y < 0:
                return

            # Get scroll delta (Qt returns degrees, convert to normalized -1.0 to 1.0)
            delta = event.angleDelta()
            hscroll = delta.x() / 120.0
            vscroll = delta.y() / 120.0

            # Clamp to [-1.0, 1.0] range
            hscroll = max(-1.0, min(1.0, hscroll))
            vscroll = max(-1.0, min(1.0, vscroll))

            self._send_scroll_event(device_x, device_y, hscroll, vscroll)

        # ========================================================================
        # Keyboard Event Handlers
        # ========================================================================

        def keyPressEvent(self, event: QKeyEvent) -> None:
            """Handle key press event."""
            if event is None:
                return

            android_keycode = qt_key_to_android_keycode(event.key())

            if android_keycode == 0:
                # Unknown key, try to send as text
                text = event.text()
                if text and self._control_queue:
                    from scrcpy_py_ddlx.core.control import ControlMessage, ControlMessageType
                    msg = ControlMessage(ControlMessageType.INJECT_TEXT)
                    msg.set_text(text)
                    self._control_queue.put(msg)
                return

            self._send_keycode_event(
                keycode=android_keycode,
                action=AndroidKeyEventAction.DOWN,
                repeat=0,
            )

        def keyReleaseEvent(self, event: QKeyEvent) -> None:
            """Handle key release event."""
            if event is None:
                return

            android_keycode = qt_key_to_android_keycode(event.key())

            if android_keycode == 0:
                return

            self._send_keycode_event(
                keycode=android_keycode,
                action=AndroidKeyEventAction.UP,
                repeat=0,
            )

    return OpenGLVideoWidget


# Create the class at import time
_OpenGLVideoWidgetClass = create_opengl_video_widget_class()

# Export the class or a stub
if _OpenGLVideoWidgetClass is not None:
    OpenGLVideoWidget = _OpenGLVideoWidgetClass
else:
    # Create a stub class if OpenGL is not available
    class OpenGLVideoWidget:
        """Stub class when OpenGL is not available."""
        def __init__(self, *args, **kwargs):
            raise RuntimeError("OpenGL is not available. Install PyOpenGL package.")


__all__ = [
    "OpenGLVideoWidget",
    "create_opengl_video_widget_class",
]
