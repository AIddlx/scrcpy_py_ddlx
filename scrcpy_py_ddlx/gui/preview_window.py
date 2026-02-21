"""
Preview window for scrcpy-py-ddlx GUI.

An independent window that displays the video stream.
Can be shown/hidden without affecting the connection.
"""

import logging
from typing import Optional, Tuple, TYPE_CHECKING

from PySide6.QtWidgets import QMainWindow, QApplication
from PySide6.QtCore import Signal

from scrcpy_py_ddlx.core.player.video.opengl_widget import OpenGLVideoWidget

if TYPE_CHECKING:
    from scrcpy_py_ddlx.core.control import ControlMessageQueue
    from scrcpy_py_ddlx.core.decoder import DelayBuffer

logger = logging.getLogger(__name__)


class PreviewWindow(QMainWindow):
    """
    Preview window for displaying video stream.

    This window:
    - Uses OpenGL for GPU-accelerated rendering
    - Can be shown/hidden independently
    - Does NOT quit the app when closed (just hides)
    - Supports all video widget features (touch, keyboard, etc.)
    """

    # Signal emitted when window is closed
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        """Setup UI components."""
        # Create video widget
        self._video_widget = OpenGLVideoWidget()
        self.setCentralWidget(self._video_widget)

        # Window setup
        self.setWindowTitle("scrcpy-py-ddlx 预览")
        self.resize(400, 600)

        # Device info
        self._device_name: str = ""
        self._device_size: Tuple[int, int] = (0, 0)

        # Hide instead of close
        self._hide_on_close = True

    def set_device_info(self, name: str, width: int, height: int) -> None:
        """
        Set device information.

        Args:
            name: Device name
            width: Screen width
            height: Screen height
        """
        self._device_name = name
        self._device_size = (width, height)
        self._video_widget.set_device_size(width, height)
        self.setWindowTitle(f"预览 - {name} ({width}x{height})")

        # Calculate window size
        self._update_window_size()

    def _update_window_size(self):
        """Update window size based on device size."""
        width, height = self._device_size
        if width == 0 or height == 0:
            return

        screen = QApplication.primaryScreen()
        if screen:
            screen_geometry = screen.availableGeometry()
            screen_width = screen_geometry.width()
            screen_height = screen_geometry.height()

            # Calculate size that fits in screen
            margin = 100
            max_width = min(screen_width - margin, 800)
            max_height = screen_height - margin

            # Calculate scaling
            scale_x = max_width / width
            scale_y = max_height / height
            scale = min(scale_x, scale_y, 1.0)  # Don't scale up

            window_width = int(width * scale)
            window_height = int(height * scale)

            # Minimum size
            window_width = max(window_width, 200)
            window_height = max(window_height, 300)

            self.resize(window_width, window_height)

    def update_frame(self, frame) -> None:
        """
        Update the displayed frame.

        Args:
            frame: BGR format numpy array (H, W, 3)
        """
        self._video_widget.update_frame(frame)

    def set_control_queue(self, queue: "ControlMessageQueue") -> None:
        """
        Set the control message queue.

        Args:
            queue: ControlMessageQueue instance
        """
        self._video_widget.set_control_queue(queue)

    def set_consume_callback(self, callback) -> None:
        """
        Set the consume callback.

        Args:
            callback: Function to call when frame is consumed
        """
        self._video_widget.set_consume_callback(callback)

    def set_delay_buffer(self, delay_buffer: 'DelayBuffer') -> None:
        """
        Set the DelayBuffer reference.

        Args:
            delay_buffer: The DelayBuffer from VideoDecoder
        """
        self._video_widget.set_delay_buffer(delay_buffer)
        self._video_widget.set_frame_size_changed_callback(self._on_frame_size_changed)

    def _on_frame_size_changed(self, width: int, height: int) -> None:
        """Handle frame size change (device rotation)."""
        self._device_size = (width, height)
        self._video_widget.set_device_size(width, height)
        self.setWindowTitle(f"预览 - {self._device_name} ({width}x{height})")
        self._update_window_size()

    @property
    def video_widget(self):
        """Get the video widget."""
        return self._video_widget

    def set_hide_on_close(self, hide: bool):
        """
        Set whether to hide or close when user clicks close button.

        Args:
            hide: True to hide, False to close
        """
        self._hide_on_close = hide

    def showEvent(self, event):
        """Handle show event."""
        super().showEvent(event)
        # Center on screen
        self._center_on_screen()

    def _center_on_screen(self):
        """Center the window on the primary screen."""
        screen = QApplication.primaryScreen()
        if screen:
            screen_geometry = screen.availableGeometry()
            x = (screen_geometry.width() - self.width()) // 2
            y = (screen_geometry.height() - self.height()) // 2
            self.move(x + screen_geometry.x(), y + screen_geometry.y())

    def closeEvent(self, event):
        """
        Handle window close event.

        If _hide_on_close is True, hide the window instead of closing.
        This allows the preview to be reopened without recreating.
        """
        if self._hide_on_close:
            event.ignore()
            self.hide()
            self.closed.emit()
            logger.info("Preview window hidden")
        else:
            self.closed.emit()
            super().closeEvent(event)
