"""
scrcpy_py_ddlx/core/player/video

Video display package for scrcpy-py-ddlx.

This package contains the video display components split from the original video_window.py:
- keycode_mapping: Qt to Android keycode mapping
- input_handler: Shared input handling methods
- video_widget: Qt-based video widget (CPU rendering)
- opengl_widget: OpenGL-based video widget (GPU rendering)
- video_window: Main window containers
- factory: Factory function for creating windows
"""

# Check if PySide6 is available
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt, QTimer
    PYSIDE6_AVAILABLE = True
except ImportError:
    PYSIDE6_AVAILABLE = False

from .keycode_mapping import (
    QT_TO_ANDROID_KEYCODE,
    qt_key_to_android_keycode,
)
from .input_handler import (
    InputHandler,
    CoordinateMapper,
)
from .video_widget import VideoWidget
from .opengl_widget import OpenGLVideoWidget, create_opengl_video_widget_class
from .video_window import VideoWindow, OpenGLVideoWindow
from .factory import create_video_window

__all__ = [
    # PySide6 availability
    "PYSIDE6_AVAILABLE",

    # Keycode mapping
    "QT_TO_ANDROID_KEYCODE",
    "qt_key_to_android_keycode",

    # Input handling
    "InputHandler",
    "CoordinateMapper",

    # Video widgets
    "VideoWidget",
    "OpenGLVideoWidget",
    "create_opengl_video_widget_class",

    # Video windows
    "VideoWindow",
    "OpenGLVideoWindow",

    # Factory
    "create_video_window",
]
