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
    from PySide6.QtCore import QCoreApplication, QTimer, QMetaObject, Qt, Q_ARG
    from PySide6.QtGui import QScreen
except ImportError:
    QApplication = None
    QMainWindow = None
    QCoreApplication = None
    QTimer = None
    QMetaObject = None
    Qt = None
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

        # User's preferred scale factor (window size relative to frame size)
        # This is the key to consistent sizing across orientations
        self._user_scale: Optional[float] = None

        # Debounce for frame size change (prevent duplicate resize calls)
        self._last_resize_time: float = 0.0
        self._last_resize_size: Tuple[int, int] = (0, 0)
        self._resize_debounce_ms: int = 100  # Ignore resize if same size within 100ms

        # Counter to skip multiple resizeEvent during programmatic resize
        # Qt may trigger multiple resizeEvent during animation
        self._skip_resize_count: int = 0

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

        This method is thread-safe - it can be called from any thread
        (decoder thread, OpenGL thread, etc.) and will safely update the UI
        on the main Qt thread.

        Args:
            width: New frame width
            height: New frame height
        """
        logger.info(f"[ROTATION] _on_frame_size_changed called: {width}x{height}")

        # Debounce at scheduling level - only keep the latest size request
        # This prevents multiple resize calls for the same size
        new_size = (width, height)
        if new_size == self._last_resize_size:
            # Same size already pending, skip
            logger.debug(f"[ROTATION] Skipping duplicate size change: {new_size}")
            return

        self._last_resize_size = new_size

        # Check if we're on the main thread by trying to access Qt properties
        # If we are, call directly; otherwise use QTimer
        try:
            # This will raise an exception if not on main thread
            _ = self.isVisible()
            # We're on main thread, call directly
            logger.info(f"[ROTATION] Calling _do_frame_size_changed directly")
            self._do_frame_size_changed(width, height)
        except Exception as e:
            # Not on main thread, use QTimer to schedule on main thread
            logger.debug(f"[ROTATION] Not on main thread ({e}), using QTimer")
            if QTimer is not None:
                # Use a closure to capture the values
                def do_resize():
                    self._do_frame_size_changed(width, height)
                QTimer.singleShot(0, do_resize)

    def _do_frame_size_changed(self, width: int, height: int) -> None:
        """
        Internal method to perform frame size change UI updates.

        This MUST be called on the main Qt thread.

        Args:
            width: New frame width
            height: New frame height
        """

        # BEFORE updating _device_size, calculate current scale from window size
        # This preserves user's manual resize
        if self._device_size[0] > 0 and self._device_size[1] > 0:
            old_frame_w, old_frame_h = self._device_size
            window_w, window_h = self.width(), self.height()
            if window_w >= 300 and window_h >= 300:
                scale_x = window_w / old_frame_w
                scale_y = window_h / old_frame_h
                self._user_scale = min(scale_x, scale_y)
                logger.info(f"[ROTATION] Saved scale before rotation: {self._user_scale:.3f}")

        # Update device size
        self._device_size = (width, height)
        self._video_widget.set_device_size(width, height)
        self.setWindowTitle(f"scrcpy-py-ddlx - {self._device_name} ({width}x{height})")

        # Calculate window size using user's preferred scale
        if self._user_scale is not None:
            # Use user's saved scale
            scale = self._user_scale
            logger.info(f"[ROTATION] Using user scale: {scale:.3f}")
        else:
            # First time - calculate initial scale based on screen
            if QScreen:
                screen = QApplication.primaryScreen()
                if screen:
                    screen_geometry = screen.availableGeometry()
                    screen_width = screen_geometry.width()
                    screen_height = screen_geometry.height()

                    margin = 40
                    available_width = screen_width - margin
                    available_height = screen_height - margin

                    # Calculate scale to fit screen
                    scale_x = available_width / width
                    scale_y = available_height / height
                    scale = min(scale_x, scale_y)

                    # Don't scale up, minimum 50%
                    scale = min(scale, 1.0)
                    scale = max(scale, 0.5)
                else:
                    scale = 0.8  # Default fallback
            else:
                scale = 0.8

            # Save initial scale
            self._user_scale = scale
            logger.info(f"[ROTATION] Initial scale: {scale:.3f}")

        # Calculate window size
        window_width = int(width * scale)
        window_height = int(height * scale)

        # Ensure minimum size
        window_width = max(window_width, 400)
        window_height = max(window_height, 400)

        # Ensure it fits on screen
        if QScreen:
            screen = QApplication.primaryScreen()
            if screen:
                screen_geometry = screen.availableGeometry()
                available_width = screen_geometry.width() - 40
                available_height = screen_geometry.height() - 40
                window_width = min(window_width, available_width)
                window_height = min(window_height, available_height)

        logger.info(f"[ROTATION] Window size: {window_width}x{window_height} for frame {width}x{height} (scale={scale:.3f})")

        # Skip only the immediate resizeEvent from this programmatic resize
        # Don't skip user's manual resize after rotation
        self._skip_resize_count = 1
        self.resize(window_width, window_height)

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

        Maintain video aspect ratio while allowing user to resize.
        Uses delayed adjustment to avoid conflicts with Qt event loop.
        """
        super().resizeEvent(event)

        # Skip if this is a programmatic resize (e.g., device rotation)
        if self._skip_resize_count > 0:
            self._skip_resize_count -= 1
            return

        # Prevent recursive resize correction
        if hasattr(self, '_in_resize_correction') and self._in_resize_correction:
            return

        device_width, device_height = self._device_size
        if device_width == 0 or device_height == 0:
            return

        new_width = event.size().width()
        new_height = event.size().height()

        # Calculate target size maintaining video aspect ratio
        device_aspect = device_width / device_height
        window_aspect = new_width / new_height

        # Only correct if aspect ratio is significantly different
        if abs(window_aspect - device_aspect) <= 0.01:
            # Aspect ratio is correct, just update scale
            scale_x = new_width / device_width
            scale_y = new_height / device_height
            new_scale = min(scale_x, scale_y)
            if self._user_scale is None or abs(new_scale - self._user_scale) > 0.01:
                self._user_scale = new_scale
                logger.debug(f"[WINDOW] User scale updated: {new_scale:.3f}")
            return

        # Use oldSize to determine user's intent
        old_size = event.oldSize()
        if old_size is not None and old_size.width() > 0 and old_size.height() > 0:
            old_width = old_size.width()
            old_height = old_size.height()

            # Calculate relative change in each dimension
            width_change_ratio = abs(new_width - old_width) / old_width if old_width > 0 else 0
            height_change_ratio = abs(new_height - old_height) / old_height if old_height > 0 else 0

            logger.debug(f"[WINDOW] old={old_width}x{old_height} new={new_width}x{new_height} "
                        f"w_ratio={width_change_ratio:.3f} h_ratio={height_change_ratio:.3f}")

            # Keep the dimension that changed more (user's primary intent)
            if width_change_ratio >= height_change_ratio:
                # Width changed more: keep new width, adjust height
                corrected_width = new_width
                corrected_height = int(new_width / device_aspect)
                logger.debug(f"[WINDOW] Keep width: {corrected_width}x{corrected_height}")
            else:
                # Height changed more: keep new height, adjust width
                corrected_width = int(new_height * device_aspect)
                corrected_height = new_height
                logger.debug(f"[WINDOW] Keep height: {corrected_width}x{corrected_height}")
        else:
            # No valid oldSize, use area-based approach
            size_from_width = (new_width, int(new_width / device_aspect))
            size_from_height = (int(new_height * device_aspect), new_height)

            logger.debug(f"[WINDOW] No oldSize, area comparison: from_w={size_from_width} from_h={size_from_height}")

            # Choose the one that gives larger area
            if size_from_width[0] * size_from_width[1] >= size_from_height[0] * size_from_height[1]:
                corrected_width, corrected_height = size_from_width
            else:
                corrected_width, corrected_height = size_from_height

        # Apply minimum size
        corrected_width = max(corrected_width, 200)
        corrected_height = max(corrected_height, 200)

        # Check if correction is needed
        if abs(corrected_width - new_width) > 2 or abs(corrected_height - new_height) > 2:
            # Store pending resize for delayed execution
            self._pending_resize = (corrected_width, corrected_height)

            # Cancel any existing timer and start a new one
            if not hasattr(self, '_resize_timer'):
                self._resize_timer = QTimer(self)
                self._resize_timer.setSingleShot(True)
                self._resize_timer.timeout.connect(self._apply_pending_resize)

            # Restart timer (debounce: only apply after 50ms of no new resize events)
            self._resize_timer.start(50)
        else:
            # No correction needed, update scale directly
            scale_x = new_width / device_width
            scale_y = new_height / device_height
            new_scale = min(scale_x, scale_y)
            if self._user_scale is None or abs(new_scale - self._user_scale) > 0.01:
                self._user_scale = new_scale
                logger.debug(f"[WINDOW] User scale updated: {new_scale:.3f}")

    def _apply_pending_resize(self) -> None:
        """Apply the pending resize correction after debounce period."""
        if not hasattr(self, '_pending_resize') or self._pending_resize is None:
            return

        corrected_width, corrected_height = self._pending_resize
        self._pending_resize = None

        device_width, device_height = self._device_size
        if device_width == 0 or device_height == 0:
            return

        # Apply the resize
        self._in_resize_correction = True
        try:
            self.resize(corrected_width, corrected_height)
            logger.debug(f"[WINDOW] Aspect ratio corrected: {corrected_width}x{corrected_height}")
        finally:
            self._in_resize_correction = False

        # Update scale based on corrected size
        scale_x = corrected_width / device_width
        scale_y = corrected_height / device_height
        new_scale = min(scale_x, scale_y)
        if self._user_scale is None or abs(new_scale - self._user_scale) > 0.01:
            self._user_scale = new_scale
            logger.debug(f"[WINDOW] User scale updated: {new_scale:.3f}")

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

        # User's preferred scale factor (window size relative to frame size)
        # This is the key to consistent sizing across orientations
        self._user_scale: Optional[float] = None

        # Debounce for frame size change (prevent duplicate resize calls)
        self._last_resize_time: float = 0.0
        self._last_resize_size: Tuple[int, int] = (0, 0)
        self._resize_debounce_ms: int = 100  # Ignore resize if same size within 100ms

        # Counter to skip multiple resizeEvent during programmatic resize
        # Qt may trigger multiple resizeEvent during animation
        self._skip_resize_count: int = 0

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

        This method is thread-safe - it can be called from any thread
        (decoder thread, OpenGL thread, etc.) and will safely update the UI
        on the main Qt thread.

        Args:
            width: New frame width
            height: New frame height
        """
        logger.info(f"[ROTATION] _on_frame_size_changed called: {width}x{height}")

        # Debounce at scheduling level - only keep the latest size request
        # This prevents multiple resize calls for the same size
        new_size = (width, height)
        if new_size == self._last_resize_size:
            # Same size already pending, skip
            logger.debug(f"[ROTATION] Skipping duplicate size change: {new_size}")
            return

        self._last_resize_size = new_size

        # Check if we're on the main thread by trying to access Qt properties
        # If we are, call directly; otherwise use QTimer
        try:
            # This will raise an exception if not on main thread
            _ = self.isVisible()
            # We're on main thread, call directly
            logger.info(f"[ROTATION] Calling _do_frame_size_changed directly")
            self._do_frame_size_changed(width, height)
        except Exception as e:
            # Not on main thread, use QTimer to schedule on main thread
            logger.debug(f"[ROTATION] Not on main thread ({e}), using QTimer")
            if QTimer is not None:
                # Use a closure to capture the values
                def do_resize():
                    self._do_frame_size_changed(width, height)
                QTimer.singleShot(0, do_resize)

    def _do_frame_size_changed(self, width: int, height: int) -> None:
        """
        Internal method to perform frame size change UI updates.

        This MUST be called on the main Qt thread.

        Args:
            width: New frame width
            height: New frame height
        """

        # BEFORE updating _device_size, calculate current scale from window size
        # This preserves user's manual resize
        if self._device_size[0] > 0 and self._device_size[1] > 0:
            old_frame_w, old_frame_h = self._device_size
            window_w, window_h = self.width(), self.height()
            if window_w >= 300 and window_h >= 300:
                scale_x = window_w / old_frame_w
                scale_y = window_h / old_frame_h
                self._user_scale = min(scale_x, scale_y)
                logger.info(f"[ROTATION] Saved scale before rotation: {self._user_scale:.3f}")

        # Update device size
        self._device_size = (width, height)
        self._video_widget.set_device_size(width, height)
        self.setWindowTitle(f"scrcpy-py-ddlx (OpenGL) - {self._device_name} ({width}x{height})")

        # Calculate window size using user's preferred scale
        if self._user_scale is not None:
            # Use user's saved scale
            scale = self._user_scale
            logger.info(f"[ROTATION] Using user scale: {scale:.3f}")
        else:
            # First time - calculate initial scale based on screen
            if QScreen:
                screen = QApplication.primaryScreen()
                if screen:
                    screen_geometry = screen.availableGeometry()
                    screen_width = screen_geometry.width()
                    screen_height = screen_geometry.height()

                    margin = 40
                    available_width = screen_width - margin
                    available_height = screen_height - margin

                    # Calculate scale to fit screen
                    scale_x = available_width / width
                    scale_y = available_height / height
                    scale = min(scale_x, scale_y)

                    # Don't scale up, minimum 50%
                    scale = min(scale, 1.0)
                    scale = max(scale, 0.5)
                else:
                    scale = 0.8  # Default fallback
            else:
                scale = 0.8

            # Save initial scale
            self._user_scale = scale
            logger.info(f"[ROTATION] Initial scale: {scale:.3f}")

        # Calculate window size
        window_width = int(width * scale)
        window_height = int(height * scale)

        # Ensure minimum size
        window_width = max(window_width, 400)
        window_height = max(window_height, 400)

        # Ensure it fits on screen
        if QScreen:
            screen = QApplication.primaryScreen()
            if screen:
                screen_geometry = screen.availableGeometry()
                available_width = screen_geometry.width() - 40
                available_height = screen_geometry.height() - 40
                window_width = min(window_width, available_width)
                window_height = min(window_height, available_height)

        logger.info(f"[ROTATION] Window size: {window_width}x{window_height} for frame {width}x{height} (scale={scale:.3f})")

        # Skip only the immediate resizeEvent from this programmatic resize
        # Don't skip user's manual resize after rotation
        self._skip_resize_count = 1
        self.resize(window_width, window_height)

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

        Maintain video aspect ratio while allowing user to resize.
        Uses delayed adjustment to avoid conflicts with Qt event loop.
        """
        super().resizeEvent(event)

        # Skip if this is a programmatic resize (e.g., device rotation)
        if self._skip_resize_count > 0:
            self._skip_resize_count -= 1
            return

        # Prevent recursive resize correction
        if hasattr(self, '_in_resize_correction') and self._in_resize_correction:
            return

        device_width, device_height = self._device_size
        if device_width == 0 or device_height == 0:
            return

        new_width = event.size().width()
        new_height = event.size().height()

        # Calculate target size maintaining video aspect ratio
        device_aspect = device_width / device_height
        window_aspect = new_width / new_height

        # Only correct if aspect ratio is significantly different
        if abs(window_aspect - device_aspect) <= 0.01:
            # Aspect ratio is correct, just update scale
            scale_x = new_width / device_width
            scale_y = new_height / device_height
            new_scale = min(scale_x, scale_y)
            if self._user_scale is None or abs(new_scale - self._user_scale) > 0.01:
                self._user_scale = new_scale
                logger.debug(f"[WINDOW] User scale updated: {new_scale:.3f}")
            return

        # Use oldSize to determine user's intent
        old_size = event.oldSize()
        if old_size is not None and old_size.width() > 0 and old_size.height() > 0:
            old_width = old_size.width()
            old_height = old_size.height()

            # Calculate relative change in each dimension
            width_change_ratio = abs(new_width - old_width) / old_width if old_width > 0 else 0
            height_change_ratio = abs(new_height - old_height) / old_height if old_height > 0 else 0

            logger.debug(f"[WINDOW] old={old_width}x{old_height} new={new_width}x{new_height} "
                        f"w_ratio={width_change_ratio:.3f} h_ratio={height_change_ratio:.3f}")

            # Keep the dimension that changed more (user's primary intent)
            if width_change_ratio >= height_change_ratio:
                # Width changed more: keep new width, adjust height
                corrected_width = new_width
                corrected_height = int(new_width / device_aspect)
                logger.debug(f"[WINDOW] Keep width: {corrected_width}x{corrected_height}")
            else:
                # Height changed more: keep new height, adjust width
                corrected_width = int(new_height * device_aspect)
                corrected_height = new_height
                logger.debug(f"[WINDOW] Keep height: {corrected_width}x{corrected_height}")
        else:
            # No valid oldSize, use area-based approach
            size_from_width = (new_width, int(new_width / device_aspect))
            size_from_height = (int(new_height * device_aspect), new_height)

            logger.debug(f"[WINDOW] No oldSize, area comparison: from_w={size_from_width} from_h={size_from_height}")

            # Choose the one that gives larger area
            if size_from_width[0] * size_from_width[1] >= size_from_height[0] * size_from_height[1]:
                corrected_width, corrected_height = size_from_width
            else:
                corrected_width, corrected_height = size_from_height

        # Apply minimum size
        corrected_width = max(corrected_width, 200)
        corrected_height = max(corrected_height, 200)

        # Check if correction is needed
        if abs(corrected_width - new_width) > 2 or abs(corrected_height - new_height) > 2:
            # Store pending resize for delayed execution
            self._pending_resize = (corrected_width, corrected_height)

            # Cancel any existing timer and start a new one
            if not hasattr(self, '_resize_timer'):
                self._resize_timer = QTimer(self)
                self._resize_timer.setSingleShot(True)
                self._resize_timer.timeout.connect(self._apply_pending_resize)

            # Restart timer (debounce: only apply after 50ms of no new resize events)
            self._resize_timer.start(50)
        else:
            # No correction needed, update scale directly
            scale_x = new_width / device_width
            scale_y = new_height / device_height
            new_scale = min(scale_x, scale_y)
            if self._user_scale is None or abs(new_scale - self._user_scale) > 0.01:
                self._user_scale = new_scale
                logger.debug(f"[WINDOW] User scale updated: {new_scale:.3f}")

    def _apply_pending_resize(self) -> None:
        """Apply the pending resize correction after debounce period."""
        if not hasattr(self, '_pending_resize') or self._pending_resize is None:
            return

        corrected_width, corrected_height = self._pending_resize
        self._pending_resize = None

        device_width, device_height = self._device_size
        if device_width == 0 or device_height == 0:
            return

        # Apply the resize
        self._in_resize_correction = True
        try:
            self.resize(corrected_width, corrected_height)
            logger.debug(f"[WINDOW] Aspect ratio corrected: {corrected_width}x{corrected_height}")
        finally:
            self._in_resize_correction = False

        # Update scale based on corrected size
        scale_x = corrected_width / device_width
        scale_y = corrected_height / device_height
        new_scale = min(scale_x, scale_y)
        if self._user_scale is None or abs(new_scale - self._user_scale) > 0.01:
            self._user_scale = new_scale
            logger.debug(f"[WINDOW] User scale updated: {new_scale:.3f}")

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
