"""
Video window containers for scrcpy display.

This module provides the main window classes that contain the video widgets
and handle the Qt application lifecycle.
"""

import logging
from typing import Optional, Tuple, Union, TYPE_CHECKING
import numpy as np

try:
    from PySide6.QtWidgets import QApplication, QMainWindow
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QScreen
except ImportError:
    QApplication = None
    QMainWindow = None
    QCoreApplication = None
    QScreen = None

from scrcpy_py_ddlx.core.player.video.video_widget import VideoWidget
from scrcpy_py_ddlx.core.player.video.opengl_widget import OpenGLVideoWidget

if TYPE_CHECKING:
    from scrcpy_py_ddlx.core.control import ControlMessageQueue
    from scrcpy_py_ddlx.core.decoder import DelayBuffer

logger = logging.getLogger(__name__)


class VideoWindow(QMainWindow if QMainWindow else object):
    """
    Main video window for scrcpy display using Qt widget.

    This window contains the video widget and handles the Qt application lifecycle.
    """

    def __init__(self, parent=None):
        """Initialize video window."""
        if QMainWindow is None:
            raise RuntimeError("PySide6 is not available")

        super().__init__(parent)

        self._video_widget = VideoWidget()
        self.setCentralWidget(self._video_widget)

        # Window setup
        self.setWindowTitle("scrcpy-py-ddlx")
        self.resize(800, 600)

        # Device info
        self._device_name: str = ""
        self._device_size: Tuple[int, int] = (0, 0)

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
        self.setWindowTitle(f"scrcpy-py-ddlx - {name} ({width}x{height})")

        # Calculate window size to fit screen while maintaining aspect ratio
        if QScreen:
            screen = QApplication.primaryScreen()
            if screen:
                screen_geometry = screen.availableGeometry()
                screen_width = screen_geometry.width()
                screen_height = screen_geometry.height()

                # Calculate window size maintaining aspect ratio
                margin = 40
                available_width = screen_width - margin
                available_height = screen_height - margin

                # Calculate scaling factor to fit within screen
                scale_x = available_width / width
                scale_y = available_height / height
                scale = min(scale_x, scale_y)

                # Also limit maximum scale to avoid huge windows
                max_scale = 1.0  # Don't scale up, only down
                scale = min(scale, max_scale)

                # Calculate final window size
                window_width = int(width * scale)
                window_height = int(height * scale)

                # Ensure minimum size
                window_width = max(window_width, 200)
                window_height = max(window_height, 200)

                self.resize(window_width, window_height)
            else:
                # Fallback if screen info not available
                self.resize(width, height)

    def update_frame(self, frame: np.ndarray) -> None:
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

    def set_consume_callback(self, callback: Optional[callable]) -> None:
        """
        Set the consume callback to notify when frame has been rendered.

        Args:
            callback: Function to call when frame is consumed
        """
        self._video_widget.set_consume_callback(callback)

    def set_delay_buffer(self, delay_buffer: 'DelayBuffer') -> None:
        """
        Set the DelayBuffer reference for direct frame consumption.

        Args:
            delay_buffer: The DelayBuffer from VideoDecoder
        """
        self._video_widget.set_delay_buffer(delay_buffer)
        # Set up frame size change callback for device rotation
        self._video_widget.set_frame_size_changed_callback(self._on_frame_size_changed)

    def _on_frame_size_changed(self, width: int, height: int) -> None:
        """
        Handle frame size change (device rotation).

        Args:
            width: New frame width
            height: New frame height
        """
        # Update device size
        self._device_size = (width, height)
        self._video_widget.set_device_size(width, height)
        self.setWindowTitle(f"scrcpy-py-ddlx - {self._device_name} ({width}x{height})")

        # Recalculate window size
        if QScreen:
            screen = QApplication.primaryScreen()
            if screen:
                screen_geometry = screen.availableGeometry()
                screen_width = screen_geometry.width()
                screen_height = screen_geometry.height()

                margin = 40
                available_width = screen_width - margin
                available_height = screen_height - margin

                scale_x = available_width / width
                scale_y = available_height / height
                scale = min(scale_x, scale_y)
                scale = min(scale, 1.0)

                window_width = int(width * scale)
                window_height = int(height * scale)

                window_width = max(window_width, 200)
                window_height = max(window_height, 200)

                self.resize(window_width, window_height)
                logger.info(f"[ROTATION] Window resized to {window_width}x{window_height} for {width}x{height} frame")

    @property
    def video_widget(self) -> VideoWidget:
        """Get the video widget."""
        return self._video_widget

    def show(self) -> None:
        """Show the window and ensure Qt application is running."""
        # Center window on screen before showing
        self._center_on_screen()
        super().show()
        self.raise_()
        self.activateWindow()
        # Force window to be visible immediately
        self.setVisible(True)
        logger.info(f"VideoWindow shown, geometry={self.geometry()}, visible={self.isVisible()}")

    def _center_on_screen(self) -> None:
        """Center the window on the primary screen."""
        if QScreen:
            screen = QApplication.primaryScreen()
            if screen:
                screen_geometry = screen.availableGeometry()
                window_geometry = self.frameGeometry()

                # Calculate center position
                x = (screen_geometry.width() - window_geometry.width()) // 2
                y = (screen_geometry.height() - window_geometry.height()) // 2

                # Move window to center
                self.move(x + screen_geometry.x(), y + screen_geometry.y())

    def resizeEvent(self, event) -> None:
        """
        Handle window resize event.

        When user manually resizes the window, automatically adjust to match
        the video aspect ratio to eliminate black borders.
        """
        super().resizeEvent(event)

        # Get old and new window sizes
        old_width = event.oldSize().width()
        old_height = event.oldSize().height()
        new_width = event.size().width()
        new_height = event.size().height()

        # Get device frame size
        device_width, device_height = self._device_size
        if device_width == 0 or device_height == 0:
            return

        # Calculate target window size to match video aspect ratio
        device_aspect = device_width / device_height
        window_aspect = new_width / new_height

        # Determine if user is growing or shrinking the window
        old_area = old_width * old_height
        new_area = new_width * new_height
        is_growing = new_area > old_area

        # Calculate corrected size based on user intent
        if window_aspect > device_aspect:
            # Window is wider than video aspect ratio
            if is_growing:
                # User wants bigger - keep width, grow height
                corrected_width = new_width
                corrected_height = int(new_width / device_aspect)
            else:
                # User wants smaller - shrink width, keep height
                corrected_width = int(new_height * device_aspect)
                corrected_height = new_height
        else:
            # Window is taller than video aspect ratio
            if is_growing:
                # User wants bigger - keep height, grow width
                corrected_width = int(new_height * device_aspect)
                corrected_height = new_height
            else:
                # User wants smaller - shrink height, keep width
                corrected_width = new_width
                corrected_height = int(new_width / device_aspect)

        # Apply minimum size
        corrected_width = max(corrected_width, 200)
        corrected_height = max(corrected_height, 200)

        # If size doesn't match aspect ratio, correct it
        if corrected_width != new_width or corrected_height != new_height:
            # Use QTimer to avoid recursive resize events
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.resize(corrected_width, corrected_height))

    def closeEvent(self, event) -> None:
        """
        Handle window close event.

        When the user closes the window, we need to quit the Qt event loop
        to unblock the app.exec() call in run_with_qt().
        """
        if QCoreApplication:
            logger.info("VideoWindow closeEvent triggered, quitting Qt event loop")
            QCoreApplication.quit()
        super().closeEvent(event)


class OpenGLVideoWindow(QMainWindow if QMainWindow else object):
    """
    Main video window using OpenGL widget for GPU-accelerated rendering.
    """

    def __init__(self, parent=None):
        """Initialize OpenGL video window."""
        if QMainWindow is None:
            raise RuntimeError("PySide6 is not available")

        super().__init__(parent)

        self._video_widget = OpenGLVideoWidget()
        self.setCentralWidget(self._video_widget)

        # Window setup
        self.setWindowTitle("scrcpy-py-ddlx (OpenGL)")
        self.resize(800, 600)

        # Device info
        self._device_name: str = ""
        self._device_size: Tuple[int, int] = (0, 0)

    def set_device_info(self, name: str, width: int, height: int) -> None:
        """Set device information."""
        self._device_name = name
        self._device_size = (width, height)
        self._video_widget.set_device_size(width, height)
        self.setWindowTitle(f"scrcpy-py-ddlx (OpenGL) - {name} ({width}x{height})")

        # Calculate window size
        if QScreen:
            screen = QApplication.primaryScreen()
            if screen:
                screen_geometry = screen.availableGeometry()
                screen_width = screen_geometry.width()
                screen_height = screen_geometry.height()

                margin = 40
                available_width = screen_width - margin
                available_height = screen_height - margin

                scale_x = available_width / width
                scale_y = available_height / height
                scale = min(scale_x, scale_y)
                scale = min(scale, 1.0)

                window_width = int(width * scale)
                window_height = int(height * scale)

                window_width = max(window_width, 200)
                window_height = max(window_height, 200)

                self.resize(window_width, window_height)
            else:
                self.resize(width, height)

    def update_frame(self, frame: np.ndarray) -> None:
        """Update the displayed frame."""
        self._video_widget.update_frame(frame)

    def set_control_queue(self, queue: "ControlMessageQueue") -> None:
        """Set the control message queue."""
        self._video_widget.set_control_queue(queue)

    def set_consume_callback(self, callback: Optional[callable]) -> None:
        """Set the consume callback to notify when frame has been rendered."""
        self._video_widget.set_consume_callback(callback)

    def set_delay_buffer(self, delay_buffer: 'DelayBuffer') -> None:
        """
        Set the DelayBuffer reference for direct frame consumption.

        Args:
            delay_buffer: The DelayBuffer from VideoDecoder
        """
        self._video_widget.set_delay_buffer(delay_buffer)
        # Set up frame size change callback for device rotation
        self._video_widget.set_frame_size_changed_callback(self._on_frame_size_changed)

    def _on_frame_size_changed(self, width: int, height: int) -> None:
        """
        Handle frame size change (device rotation).

        Args:
            width: New frame width
            height: New frame height
        """
        # Update device size
        self._device_size = (width, height)
        self._video_widget.set_device_size(width, height)
        self.setWindowTitle(f"scrcpy-py-ddlx (OpenGL) - {self._device_name} ({width}x{height})")

        # Recalculate window size
        if QScreen:
            screen = QApplication.primaryScreen()
            if screen:
                screen_geometry = screen.availableGeometry()
                screen_width = screen_geometry.width()
                screen_height = screen_geometry.height()

                margin = 40
                available_width = screen_width - margin
                available_height = screen_height - margin

                scale_x = available_width / width
                scale_y = available_height / height
                scale = min(scale_x, scale_y)
                scale = min(scale, 1.0)

                window_width = int(width * scale)
                window_height = int(height * scale)

                window_width = max(window_width, 200)
                window_height = max(window_height, 200)

                self.resize(window_width, window_height)
                logger.info(f"[ROTATION] Window resized to {window_width}x{window_height} for {width}x{height} frame")

    @property
    def video_widget(self) -> OpenGLVideoWidget:
        """Get the video widget."""
        return self._video_widget

    def show(self) -> None:
        """Show the window and ensure Qt application is running."""
        # Center window on screen before showing
        self._center_on_screen()
        super().show()
        self.raise_()
        self.activateWindow()
        # Force window to be visible immediately
        self.setVisible(True)
        logger.info(f"OpenGLVideoWindow shown, geometry={self.geometry()}, visible={self.isVisible()}")

    def _center_on_screen(self) -> None:
        """Center the window on the primary screen."""
        if QScreen:
            screen = QApplication.primaryScreen()
            if screen:
                screen_geometry = screen.availableGeometry()
                window_geometry = self.frameGeometry()

                # Calculate center position
                x = (screen_geometry.width() - window_geometry.width()) // 2
                y = (screen_geometry.height() - window_geometry.height()) // 2

                # Move window to center
                self.move(x + screen_geometry.x(), y + screen_geometry.y())

    def resizeEvent(self, event) -> None:
        """
        Handle window resize event.

        When user manually resizes the window, automatically adjust to match
        the video aspect ratio to eliminate black borders.
        """
        super().resizeEvent(event)

        # Get old and new window sizes
        old_width = event.oldSize().width()
        old_height = event.oldSize().height()
        new_width = event.size().width()
        new_height = event.size().height()

        # Get device frame size
        device_width, device_height = self._device_size
        if device_width == 0 or device_height == 0:
            return

        # Calculate target window size to match video aspect ratio
        device_aspect = device_width / device_height
        window_aspect = new_width / new_height

        # Determine if user is growing or shrinking the window
        old_area = old_width * old_height
        new_area = new_width * new_height
        is_growing = new_area > old_area

        # Calculate corrected size based on user intent
        if window_aspect > device_aspect:
            # Window is wider than video aspect ratio
            if is_growing:
                # User wants bigger - keep width, grow height
                corrected_width = new_width
                corrected_height = int(new_width / device_aspect)
            else:
                # User wants smaller - shrink width, keep height
                corrected_width = int(new_height * device_aspect)
                corrected_height = new_height
        else:
            # Window is taller than video aspect ratio
            if is_growing:
                # User wants bigger - keep height, grow width
                corrected_width = int(new_height * device_aspect)
                corrected_height = new_height
            else:
                # User wants smaller - shrink height, keep width
                corrected_width = new_width
                corrected_height = int(new_width / device_aspect)

        # Apply minimum size
        corrected_width = max(corrected_width, 200)
        corrected_height = max(corrected_height, 200)

        # If size doesn't match aspect ratio, correct it
        if corrected_width != new_width or corrected_height != new_height:
            # Use QTimer to avoid recursive resize events
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.resize(corrected_width, corrected_height))

    def closeEvent(self, event) -> None:
        """
        Handle window close event.

        When the user closes the window, we need to quit the Qt event loop
        to unblock the app.exec() call in run_with_qt().
        """
        if QCoreApplication:
            logger.info("OpenGLVideoWindow closeEvent triggered, quitting Qt event loop")
            QCoreApplication.quit()
        super().closeEvent(event)


__all__ = [
    "VideoWindow",
    "OpenGLVideoWindow",
]
