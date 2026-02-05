"""
Qt to Android keycode mapping.

This module provides the mapping between Qt key codes and Android keycodes.
Based on android/keycodes.h and scrcpy input_handler.c.
"""

import logging

try:
    from PySide6.QtCore import Qt
except ImportError:
    Qt = None

logger = logging.getLogger(__name__)


def _build_keycode_mapping():
    """
    Build Qt to Android keycode mapping safely.

    Using a safe approach - only include keys that exist in all Qt versions.
    This prevents AttributeError on different Qt versions.

    Returns:
        Dictionary mapping Qt key constants to Android keycodes
    """
    if Qt is None:
        return {}

    mapping = {}

    # Helper to safely add key if it exists
    def add_key(qt_key_const, android_code):
        try:
            mapping[qt_key_const] = android_code
        except AttributeError:
            pass  # Key doesn't exist in this Qt version

    # Special keys
    add_key(Qt.Key_Space, 62)
    add_key(Qt.Key_Tab, 61)
    add_key(Qt.Key_Enter, 66)
    add_key(Qt.Key_Return, 66)
    add_key(Qt.Key_Backspace, 67)
    add_key(Qt.Key_Escape, 111)

    # D-pad
    add_key(Qt.Key_Left, 21)
    add_key(Qt.Key_Right, 22)
    add_key(Qt.Key_Up, 19)
    add_key(Qt.Key_Down, 20)

    # Navigation keys
    add_key(Qt.Key_Home, 3)
    add_key(Qt.Key_Back, 4)
    add_key(Qt.Key_Menu, 82)

    # Number keys
    add_key(Qt.Key_0, 7)
    add_key(Qt.Key_1, 8)
    add_key(Qt.Key_2, 9)
    add_key(Qt.Key_3, 10)
    add_key(Qt.Key_4, 11)
    add_key(Qt.Key_5, 12)
    add_key(Qt.Key_6, 13)
    add_key(Qt.Key_7, 14)
    add_key(Qt.Key_8, 15)
    add_key(Qt.Key_9, 16)

    # Letters
    add_key(Qt.Key_A, 29)
    add_key(Qt.Key_B, 30)
    add_key(Qt.Key_C, 31)
    add_key(Qt.Key_D, 32)
    add_key(Qt.Key_E, 33)
    add_key(Qt.Key_F, 34)
    add_key(Qt.Key_G, 35)
    add_key(Qt.Key_H, 36)
    add_key(Qt.Key_I, 37)
    add_key(Qt.Key_J, 38)
    add_key(Qt.Key_K, 39)
    add_key(Qt.Key_L, 40)
    add_key(Qt.Key_M, 41)
    add_key(Qt.Key_N, 42)
    add_key(Qt.Key_O, 43)
    add_key(Qt.Key_P, 44)
    add_key(Qt.Key_Q, 45)
    add_key(Qt.Key_R, 46)
    add_key(Qt.Key_S, 47)
    add_key(Qt.Key_T, 48)
    add_key(Qt.Key_U, 49)
    add_key(Qt.Key_V, 50)
    add_key(Qt.Key_W, 51)
    add_key(Qt.Key_X, 52)
    add_key(Qt.Key_Y, 53)
    add_key(Qt.Key_Z, 54)

    # Function keys
    add_key(Qt.Key_F1, 131)
    add_key(Qt.Key_F2, 132)
    add_key(Qt.Key_F3, 133)
    add_key(Qt.Key_F4, 134)
    add_key(Qt.Key_F5, 135)
    add_key(Qt.Key_F6, 136)
    add_key(Qt.Key_F7, 137)
    add_key(Qt.Key_F8, 138)
    add_key(Qt.Key_F9, 139)
    add_key(Qt.Key_F10, 140)
    add_key(Qt.Key_F11, 141)
    add_key(Qt.Key_F12, 142)

    # Media keys
    add_key(Qt.Key_VolumeUp, 24)
    add_key(Qt.Key_VolumeDown, 25)

    # Symbols
    add_key(Qt.Key_Comma, 55)
    add_key(Qt.Key_Period, 56)
    add_key(Qt.Key_Semicolon, 74)
    add_key(Qt.Key_Slash, 76)
    add_key(Qt.Key_Minus, 69)
    add_key(Qt.Key_Equal, 70)
    add_key(Qt.Key_BracketLeft, 71)
    add_key(Qt.Key_BracketRight, 72)

    # Modifier keys
    add_key(Qt.Key_Shift, 59)
    add_key(Qt.Key_Control, 113)
    add_key(Qt.Key_Meta, 117)
    add_key(Qt.Key_Alt, 57)

    # Other
    add_key(Qt.Key_Delete, 67)
    add_key(Qt.Key_Insert, 124)
    add_key(Qt.Key_PageUp, 92)
    add_key(Qt.Key_PageDown, 93)

    return mapping


# Build the mapping at import time
QT_TO_ANDROID_KEYCODE = _build_keycode_mapping()


def qt_key_to_android_keycode(qt_key: int) -> int:
    """
    Convert Qt key code to Android keycode.

    Args:
        qt_key: Qt key code

    Returns:
        Android keycode, or 0 if unknown
    """
    return QT_TO_ANDROID_KEYCODE.get(qt_key, 0)


__all__ = [
    "QT_TO_ANDROID_KEYCODE",
    "qt_key_to_android_keycode",
]
