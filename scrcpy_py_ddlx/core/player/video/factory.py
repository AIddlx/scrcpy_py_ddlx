"""
Factory function for creating video windows.

This module provides the factory function for creating video windows,
with support for both Qt-based and OpenGL-based rendering.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def create_video_window(use_opengl: bool = False):
    """
    Create a video window instance.

    This factory function handles the creation of video windows with either
    Qt-based rendering (CPU) or OpenGL-based rendering (GPU accelerated).

    Args:
        use_opengl: If True, use OpenGL widget (GPU accelerated).
                    If False, use regular Qt widget.

    Returns:
        VideoWindow, OpenGLVideoWindow instance, or None if PySide6 is not available
    """
    # Import inside function to avoid circular import issues
    from scrcpy_py_ddlx.core.player.video.video_window import VideoWindow, OpenGLVideoWindow
    from scrcpy_py_ddlx.core.player.video.opengl_widget import OpenGLVideoWidget

    # Check for PySide6 at runtime (not at module import time)
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        logger.error("PySide6 is not installed. Install with: pip install 'scrcpy-py-ddlx[gui]'")
        return None

    # Check if QApplication exists
    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    # Check for OpenGL availability
    if use_opengl:
        try:
            # Check if OpenGLVideoWidget is the real class (not stub)
            # Test by trying to instantiate it first
            if hasattr(OpenGLVideoWidget, '__doc__') and 'Stub' in str(OpenGLVideoWidget.__doc__):
                raise ImportError("OpenGL widget is a stub - PyOpenGL not properly installed")
            # Try to create widget to verify it works
            test_widget = OpenGLVideoWidget()
            del test_widget  # Clean up test widget
            # Now create the actual window
            window = OpenGLVideoWindow()
            logger.info("Created OpenGL video window (GPU accelerated)")
        except (ImportError, RuntimeError) as e:
            logger.warning(f"OpenGL not available ({e}), falling back to regular window")
            window = VideoWindow()
    else:
        window = VideoWindow()

    return window


__all__ = [
    "create_video_window",
]
