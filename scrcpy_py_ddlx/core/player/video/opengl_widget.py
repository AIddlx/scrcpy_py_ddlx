"""
OpenGL video widget using GPU acceleration for displaying scrcpy video stream.

This module provides an OpenGL-based widget for hardware-accelerated rendering.
Like official scrcpy, this uses GPU textures for rendering.

Based on official scrcpy's SDL2 rendering in screen.c
"""

import logging
import time
from typing import Optional, Tuple, TYPE_CHECKING
from ctypes import c_void_p
import numpy as np

try:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtCore import Qt, QMutex, QMetaObject, QTimer
    from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent, QSurfaceFormat
except ImportError:
    QOpenGLWidget = object
    Qt = None
    QMutex = None
    QMetaObject = None
    QTimer = None
    QSurfaceFormat = None
    QKeyEvent = None
    QMouseEvent = None
    QWheelEvent = None

try:
    from OpenGL.GL import (
        glGenTextures, glBindTexture, glTexParameteri,
        glTexImage2D, glTexSubImage2D, glClearColor, glClear,
        glViewport, glMatrixMode, glLoadIdentity,
        glOrtho, glEnable, glDisable,
        glColor3f, glBegin, glEnd,
        glTexCoord2f, glVertex2f,
        GL_UNSIGNED_BYTE, GL_RGB, GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER,
        GL_TEXTURE_MAG_FILTER, GL_LINEAR, GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T,
        GL_CLAMP_TO_EDGE, GL_COLOR_BUFFER_BIT, GL_QUADS,
        GL_PROJECTION, GL_MODELVIEW
    )
    OPENGL_AVAILABLE = True
except ImportError:
    OPENGL_AVAILABLE = False

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


def create_opengl_video_widget_class():
    """
    Create the OpenGLVideoWidget class only if OpenGL is available.

    Returns:
        OpenGLVideoWidget class or None if OpenGL is not available
    """
    if not OPENGL_AVAILABLE or QOpenGLWidget is None:
        return None

    class OpenGLVideoWidget(QOpenGLWidget, InputHandler, CoordinateMapper):
        """
        OpenGL-based video widget using GPU acceleration.

        Like official scrcpy, this uses GPU textures for rendering,
        avoiding expensive CPU-side image copying.
        """

        # Class-level frame counter
        _frame_count = 0

        def __init__(self, parent=None):
            """Initialize OpenGL video widget."""
            super().__init__(parent)

            # Initialize base classes
            InputHandler.__init__(self)
            CoordinateMapper.__init__(self)

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
            format.setSamples(4)  # 4x MSAA
            self.setFormat(format)

            # Timer for periodic updates (ensures continuous rendering)
            # This is critical for QOpenGLWidget - unlike QWidget, it needs explicit updates
            if QTimer is not None:
                self._update_timer = QTimer()
                self._update_timer.timeout.connect(self._on_update_timer)
                self._update_timer.start(16)  # ~60fps
                logger.info("[OPENGL] Timer started (16ms interval, ~60fps)")
            else:
                self._update_timer = None
                logger.warning("QTimer not available, OpenGL widget may not update continuously")

        def initializeGL(self) -> None:
            """Initialize OpenGL resources. Called automatically by Qt."""
            logger.info("Initializing OpenGL...")

            # Generate texture
            self._texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self._texture_id)

            # Set texture parameters
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

            logger.info("OpenGL initialized successfully")

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
            # Track paintGL calls
            self._paint_count = getattr(self, '_paint_count', 0) + 1
            current_time = time.time()

            # Log periodically
            if self._paint_count % 60 == 1:
                logger.info(f"[OPENGL] paintGL #{self._paint_count}")

            # Clear screen
            glClearColor(0.0, 0.0, 0.0, 1.0)
            glClear(GL_COLOR_BUFFER_BIT)

            # Check if we have DelayBuffer
            if self._delay_buffer is None:
                return

            # Check if we have texture data
            if self._texture_id is None:
                return

            # ALWAYS try to consume from DelayBuffer when we have a new frame
            # Don't skip consumption even if has_new=False - handle retry case
            self._frame_lock.lock()
            has_new = self._has_new_frame
            if has_new:
                self._has_new_frame = False
            self._frame_lock.unlock()

            # Only consume if has_new is True to avoid consuming the same frame twice
            if has_new:
                try:
                    new_frame = self._delay_buffer.consume()
                    if new_frame is not None:
                        old_width, old_height = self._frame_width, self._frame_height
                        self._frame_lock.lock()
                        self._frame_array = new_frame
                        if hasattr(new_frame, 'shape'):
                            self._frame_width = new_frame.shape[1]
                            self._frame_height = new_frame.shape[0]
                            CoordinateMapper.set_frame_size(self, self._frame_width, self._frame_height)
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

            # Get current frame for rendering
            self._frame_lock.lock()
            frame_array = self._frame_array
            width = self._frame_width
            height = self._frame_height
            self._frame_lock.unlock()

            if frame_array is None or width == 0 or height == 0:
                return

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

            # Set up orthographic projection (legacy OpenGL)
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

            # Direct call to update() - Qt handles thread safety automatically
            # QMetaObject.invokeMethod is not reliable for triggering paintGL
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
