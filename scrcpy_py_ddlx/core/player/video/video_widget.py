"""
Video widget using Qt for displaying scrcpy video stream.

This module provides a Qt-based widget that displays video frames and
handles input events, following the official scrcpy design.

Based on official scrcpy's SDL2 event handling in input_manager.c
"""

import logging
import time
from typing import Optional, Tuple, TYPE_CHECKING
import numpy as np

try:
    from PySide6.QtWidgets import QWidget
    from PySide6.QtCore import Qt, QTimer, QMutex
    from PySide6.QtGui import QImage, QPixmap, QPainter, QKeyEvent, QMouseEvent, QWheelEvent
except ImportError:
    QWidget = object
    Qt = None
    QTimer = None
    QMutex = None
    QImage = None
    QPixmap = None
    QPainter = None
    QKeyEvent = None
    QMouseEvent = None
    QWheelEvent = None

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

# ============ 端到端延迟追踪 ============
# 用于追踪当前帧的 packet_id，以便在渲染完成时记录 RENDER 时间
_current_render_packet_id: int = -1


class VideoWidget(QWidget if QWidget else object, InputHandler, CoordinateMapper):
    """
    Video display widget that renders frames and handles input events.

    This widget displays video frames and converts input events to
    scrcpy control messages following the official design.

    Thread Safety:
    - update_frame() can be called from any thread
    - Frame data is consumed from DelayBuffer
    - The actual rendering happens in the GUI thread via paintEvent
    """

    # Class-level frame counter
    _frame_count = 0

    def __init__(self, parent=None):
        """Initialize video widget."""
        super().__init__(parent)

        # Initialize base classes
        InputHandler.__init__(self)
        CoordinateMapper.__init__(self)

        # Thread lock to protect frame data access
        self._frame_lock = QMutex() if QMutex else None

        # Direct access to DelayBuffer (set by client)
        self._delay_buffer: Optional['DelayBuffer'] = None

        # Current frame (consumed from DelayBuffer, kept for rendering)
        self._current_frame: Optional[np.ndarray] = None
        self._frame_width: int = 0
        self._frame_height: int = 0

        # Track if we have a new frame to display
        self._has_new_frame: bool = False

        # Frame counter
        self._frame_count = 0

        # Track last update time to limit refresh rate (prevent > 60fps)
        self._last_update_time: float = 0
        self._min_update_interval: float = 0.016  # 16ms = 60fps max

        # Consume callback (set by client to notify when frame is rendered)
        self._consume_callback: Optional[callable] = None

        # Frame size change callback (called when frame resolution changes)
        self._frame_size_changed_callback: Optional[callable] = None

        # Enable mouse tracking for move events (required for hover)
        self.setMouseTracking(True)

        # Focus policy for keyboard events
        self.setFocusPolicy(Qt.StrongFocus) if Qt else None

        # Cursor
        self.setCursor(Qt.ArrowCursor) if Qt else None

        # Timer for periodic updates (like SDL event loop)
        # Use 16ms for 60fps for smooth video playback
        if QTimer:
            self._update_timer = QTimer()
            self._update_timer.timeout.connect(self._on_update_timer)
            self._update_timer.start(16)  # ~60fps

        # ============ Qt 渲染层延迟追踪 ============
        self._qt_paint_count = 0
        self._qt_last_paint_time = 0
        self._qt_update_request_time = 0  # 记录 update() 被调用的时间
        self._qt_pending_updates = 0  # 待处理的 update 请求数

    def set_consume_callback(self, callback: Optional[callable]) -> None:
        """
        Set the consume callback to notify when frame has been rendered.

        Args:
            callback: Function to call when frame is consumed (takes no arguments)
        """
        self._consume_callback = callback

    def set_delay_buffer(self, delay_buffer: 'DelayBuffer') -> None:
        """
        Set the DelayBuffer reference for direct frame consumption.

        Args:
            delay_buffer: The DelayBuffer from VideoDecoder
        """
        self._delay_buffer = delay_buffer
        logger.debug("VideoWidget DelayBuffer reference set")

    def set_frame_size_changed_callback(self, callback: Optional[callable]) -> None:
        """Set the callback to notify when frame size changes (device rotation)."""
        self._frame_size_changed_callback = callback

    def _on_update_timer(self) -> None:
        """Called periodically by QTimer to trigger repaint."""
        if self._has_new_frame:
            self.update()

    def resizeEvent(self, event) -> None:
        """
        Handle window resize event.

        Invalidate cache and trigger repaint.
        """
        super().resizeEvent(event)
        self.update()

    def update_frame(self, frame: np.ndarray) -> None:
        """
        Update the displayed frame. Thread-safe - called from decoder thread.

        DEPRECATED: This method is kept for compatibility but no longer stores frames.
        Frames are now consumed directly from DelayBuffer in paintEvent.

        Args:
            frame: RGB format numpy array (H, W, 3) from decoder - IGNORED
        """
        self._frame_count += 1
        now = time.time()

        # 记录 update() 被调用的时间
        if self._qt_update_request_time > 0:
            # 计算距离上次 update() 的时间
            interval = (now - self._qt_update_request_time) * 1000
            # 每 60 帧记录一次
            if self._frame_count % 60 == 0:
                logger.info(f"[QT_UPDATE] Frame #{self._frame_count}: update() called, "
                           f"interval_since_last={interval:.1f}ms, pending_updates={self._qt_pending_updates}")

        self._qt_update_request_time = now
        self._qt_pending_updates += 1

        # Mark that new frame is available in DelayBuffer
        self._has_new_frame = True

        # Trigger update - paintEvent will consume from DelayBuffer
        self.update()

    def paintEvent(self, event) -> None:
        """Paint the video frame - called in GUI thread."""
        paint_start = time.time()
        self._qt_paint_count += 1

        if QPainter is None:
            return

        painter = QPainter(self)

        # ============ 阶段1: paintEvent 开始 ============
        stage1_time = time.time()

        # 计算 update() 到 paintEvent() 的延迟
        update_to_paint_ms = 0
        if self._qt_update_request_time > 0:
            update_to_paint_ms = (paint_start - self._qt_update_request_time) * 1000

        # 记录待处理的 update 请求数变化
        pending_before = self._qt_pending_updates
        if self._qt_pending_updates > 0:
            self._qt_pending_updates -= 1

        # Update last render time
        self._last_update_time = time.time()

        # Check if we have DelayBuffer connected
        if self._delay_buffer is None:
            painter.fillRect(self.rect(), Qt.black) if Qt else None
            painter.end()
            return

        widget_size = (self.width(), self.height())

        # Atomically read and clear has_new_frame flag
        if self._frame_lock:
            self._frame_lock.lock()
        has_new = self._has_new_frame
        if has_new:
            self._has_new_frame = False
        if self._frame_lock:
            self._frame_lock.unlock()

        # ============ 阶段2: DelayBuffer consume ============
        stage2_time = time.time()
        consume_start = time.time()

        # 当前帧的 packet_id (用于延迟追踪)
        current_packet_id = -1

        # Consume from DelayBuffer when we have a new frame
        if has_new:
            try:
                frame_with_metadata = self._delay_buffer.consume()
                consume_time = (time.time() - consume_start) * 1000

                if frame_with_metadata is not None:
                    # 提取帧数据和 packet_id
                    new_frame = frame_with_metadata.frame
                    current_packet_id = frame_with_metadata.packet_id

                    # ============ 端到端延迟追踪: SHM_READ ============
                    # 在 Qt 窗口中，DelayBuffer.consume() 相当于 SHM_READ
                    # 记录从 SHM_WRITE (解码完成) 到 SHM_READ (Qt 消费) 的时间
                    if current_packet_id >= 0:
                        try:
                            from scrcpy_py_ddlx.latency_tracker import get_tracker
                            get_tracker().record_shm_read(current_packet_id)
                            logger.debug(f"[QT_TRACK] SHM_READ packet_id={current_packet_id}")
                        except Exception as e:
                            logger.debug(f"[QT_TRACK] Failed to record SHM_READ: {e}")

                    old_width, old_height = self._frame_width, self._frame_height
                    if self._frame_lock:
                        self._frame_lock.lock()
                    self._current_frame = new_frame
                    if hasattr(new_frame, 'shape'):
                        self._frame_width = new_frame.shape[1]
                        self._frame_height = new_frame.shape[0]
                        CoordinateMapper.set_frame_size(self, self._frame_width, self._frame_height)
                    if self._frame_lock:
                        self._frame_lock.unlock()

                    # Check if frame size changed (device rotation)
                    if (old_width, old_height) != (self._frame_width, self._frame_height):
                        if old_width > 0 and old_height > 0:  # Not first frame
                            logger.info(
                                f"[WIDGET] Frame size changed: "
                                f"{old_width}x{old_height} -> {self._frame_width}x{self._frame_height}"
                            )
                            # Notify parent window to resize
                            if self._frame_size_changed_callback:
                                self._frame_size_changed_callback(self._frame_width, self._frame_height)

                    # ============ Qt 渲染层延迟日志 ============
                    # 每 60 帧或延迟超过 50ms 时记录
                    if self._qt_paint_count % 60 == 0 or update_to_paint_ms > 50:
                        logger.info(
                            f"[QT_PAINT] #{self._qt_paint_count}: "
                            f"update→paint={update_to_paint_ms:.1f}ms, "
                            f"consume={consume_time:.2f}ms, "
                            f"pending={pending_before}, "
                            f"has_new={has_new}, "
                            f"packet_id={current_packet_id}"
                        )
            except Exception as e:
                logger.error(f"[PAINT] Error consuming from DelayBuffer: {e}")

        stage3_time = time.time()

        # Get current frame for rendering
        if self._frame_lock:
            self._frame_lock.lock()
        frame_array = self._current_frame
        width = self._frame_width
        height = self._frame_height
        if self._frame_lock:
            self._frame_lock.unlock()

        # If no frame available, show black screen
        if frame_array is None:
            painter.fillRect(self.rect(), Qt.black) if Qt else None
            painter.end()
            return

        # ============ 阶段3: 帧数据复制 ============
        copy_start = time.time()
        frame_array = frame_array.copy()
        copy_time = (time.time() - copy_start) * 1000

        # Ensure array is C-contiguous before converting to bytes
        if not frame_array.flags['C_CONTIGUOUS']:
            frame_array = np.ascontiguousarray(frame_array)

        # ============ 阶段4: 转换为 bytes ============
        tobytes_start = time.time()
        frame_bytes = frame_array.tobytes()
        tobytes_time = (time.time() - tobytes_start) * 1000
        bytes_per_line = width * 3

        # ============ 阶段5: 创建 QImage ============
        qimage_start = time.time()
        if QImage:
            image = QImage(
                frame_bytes,
                width,
                height,
                bytes_per_line,
                QImage.Format.Format_RGB888
            ).copy()
            qimage_time = (time.time() - qimage_start) * 1000

            if image.isNull():
                painter.fillRect(self.rect(), Qt.black) if Qt else None
                painter.end()
                return

            # ============ 阶段6: QPixmap 转换 ============
            pixmap_start = time.time()
            source_pixmap = QPixmap.fromImage(image)
            pixmap_time = (time.time() - pixmap_start) * 1000

            # ============ 阶段7: 缩放 ============
            scale_start = time.time()
            scaled_pixmap = source_pixmap.scaled(
                widget_size[0],
                widget_size[1],
                Qt.KeepAspectRatio if Qt else None,
                Qt.FastTransformation if Qt else None
            )
            scale_time = (time.time() - scale_start) * 1000

            # ============ 阶段8: 绘制 ============
            draw_start = time.time()
            x = (widget_size[0] - scaled_pixmap.width()) // 2
            y = (widget_size[1] - scaled_pixmap.height()) // 2
            painter.drawPixmap(x, y, scaled_pixmap)
            draw_time = (time.time() - draw_start) * 1000

        painter.end()

        # ============ 端到端延迟追踪: RENDER ============
        # paintEvent 完成意味着帧已渲染到屏幕
        # 记录从 SHM_READ (消费) 到 RENDER (绘制完成) 的时间
        if current_packet_id >= 0:
            try:
                from scrcpy_py_ddlx.latency_tracker import get_tracker
                get_tracker().record_render(current_packet_id)
            except Exception as e:
                logger.debug(f"[QT_TRACK] Failed to record RENDER: {e}")

        # ============ 总结 paintEvent 各阶段耗时 ============
        total_paint_time = (time.time() - paint_start) * 1000

        # 每 60 帧或总耗时超过 30ms 时记录详细日志
        if self._qt_paint_count % 60 == 0 or total_paint_time > 30:
            logger.info(
                f"[QT_TIMING] #{self._qt_paint_count}: "
                f"TOTAL={total_paint_time:.1f}ms | "
                f"copy={copy_time:.1f}ms | "
                f"bytes={tobytes_time:.1f}ms | "
                f"qimage={qimage_time:.1f}ms | "
                f"pixmap={pixmap_time:.1f}ms | "
                f"scale={scale_time:.1f}ms | "
                f"draw={draw_time:.1f}ms | "
                f"packet_id={current_packet_id}"
            )

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
            return  # Outside video area

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
            return  # Outside video area

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
            return  # Outside video area

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
            return  # Outside video area

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
            return  # Unknown key, already handled in press event

        self._send_keycode_event(
            keycode=android_keycode,
            action=AndroidKeyEventAction.UP,
            repeat=0,
        )


__all__ = [
    "VideoWidget",
]
