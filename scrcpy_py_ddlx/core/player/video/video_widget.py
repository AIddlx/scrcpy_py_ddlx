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

        # Mark that new frame is available in DelayBuffer
        self._has_new_frame = True

        # Trigger update - paintEvent will consume from DelayBuffer
        self.update()

    def paintEvent(self, event) -> None:
        """Paint the video frame - called in GUI thread."""
        if QPainter is None:
            return

        painter = QPainter(self)

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

        # Consume from DelayBuffer when we have a new frame
        if has_new:
            try:
                new_frame = self._delay_buffer.consume()
                if new_frame is not None:
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
            except Exception as e:
                logger.error(f"[PAINT] Error consuming from DelayBuffer: {e}")

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

        # Make a complete independent copy of the frame data
        frame_array = frame_array.copy()

        # Ensure array is C-contiguous before converting to bytes
        if not frame_array.flags['C_CONTIGUOUS']:
            frame_array = np.ascontiguousarray(frame_array)

        # Convert to bytes
        frame_bytes = frame_array.tobytes()
        bytes_per_line = width * 3

        # Create QImage
        if QImage:
            image = QImage(
                frame_bytes,
                width,
                height,
                bytes_per_line,
                QImage.Format.Format_RGB888
            ).copy()

            if image.isNull():
                painter.fillRect(self.rect(), Qt.black) if Qt else None
                painter.end()
                return

            # Create and scale pixmap
            source_pixmap = QPixmap.fromImage(image)
            scaled_pixmap = source_pixmap.scaled(
                widget_size[0],
                widget_size[1],
                Qt.KeepAspectRatio if Qt else None,
                Qt.FastTransformation if Qt else None
            )

            # Render centered
            x = (widget_size[0] - scaled_pixmap.width()) // 2
            y = (widget_size[1] - scaled_pixmap.height()) // 2
            painter.drawPixmap(x, y, scaled_pixmap)

        painter.end()

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
