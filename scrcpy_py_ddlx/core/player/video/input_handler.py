"""
Input handling for video widgets.

This module provides shared input handling methods for mouse and keyboard events,
eliminating code duplication between VideoWidget and OpenGLVideoWidget.

Based on official scrcpy implementation:
- app/src/input_manager.c - Input event handling
- app/src/mouse_sdk.c - Mouse event processing
"""

import logging
from typing import Tuple, Optional, TYPE_CHECKING

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QMouseEvent, QWheelEvent, QKeyEvent
except ImportError:
    Qt = None
    QMouseEvent = None
    QWheelEvent = None
    QKeyEvent = None

from scrcpy_py_ddlx.core.protocol import (
    POINTER_ID_MOUSE,
    AndroidMotionEventAction,
    AndroidKeyEventAction,
    AndroidMotionEventButtons,
)

if TYPE_CHECKING:
    from scrcpy_py_ddlx.core.control import ControlMessageQueue

logger = logging.getLogger(__name__)


class InputHandler:
    """
    Base class for input handling in video widgets.

    This class provides shared methods for:
    - Mouse button state tracking
    - Coordinate mapping (widget to device)
    - Touch event sending
    - Scroll event sending
    - Keycode event sending
    """

    def __init__(self):
        """Initialize input handler."""
        # Mouse state tracking (per official scrcpy)
        self._mouse_buttons_state: int = 0  # bitmask of pressed buttons
        self._last_position: Tuple[int, int] = (0, 0)

        # Device size
        self._device_size: Tuple[int, int] = (0, 0)

        # Control queue (set by widget)
        self._control_queue: Optional["ControlMessageQueue"] = None

        # Input mode (following official scrcpy behavior)
        self._mouse_hover: bool = False  # Send hover move events when no button pressed

    def set_control_queue(self, queue: "ControlMessageQueue") -> None:
        """Set the control message queue."""
        self._control_queue = queue

    def set_device_size(self, width: int, height: int) -> None:
        """Set the device screen size."""
        self._device_size = (width, height)

    def _qt_button_to_android(self, qt_button: Qt.MouseButton) -> int:
        """
        Convert Qt mouse button to Android motion event button.

        Args:
            qt_button: Qt mouse button

        Returns:
            Android motion event button flag
        """
        if Qt is None:
            return 0

        if qt_button == Qt.LeftButton:
            return AndroidMotionEventButtons.PRIMARY
        elif qt_button == Qt.RightButton:
            return AndroidMotionEventButtons.SECONDARY
        elif qt_button == Qt.MiddleButton:
            return AndroidMotionEventButtons.TERTIARY
        elif qt_button == Qt.BackButton:
            return AndroidMotionEventButtons.BACK
        elif qt_button == Qt.ForwardButton:
            return AndroidMotionEventButtons.FORWARD
        return 0

    def _update_mouse_button_state(self, button: Qt.MouseButton, pressed: bool) -> None:
        """
        Update mouse button state bitmask.

        Args:
            button: Qt mouse button
            pressed: True if button is pressed, False if released
        """
        android_button = self._qt_button_to_android(button)
        if pressed:
            self._mouse_buttons_state |= android_button
        else:
            self._mouse_buttons_state &= ~android_button

    def _send_touch_event(
        self,
        action: AndroidMotionEventAction,
        pointer_id: int,
        position_x: int,
        position_y: int,
        pressure: float = 1.0,
        action_button: int = 0,
    ) -> None:
        """
        Send a touch event to the device.

        Following official scrcpy mouse_sdk.c design.

        Args:
            action: Motion event action
            pointer_id: Pointer ID (use POINTER_ID_* constants)
            position_x: X coordinate in device pixels
            position_y: Y coordinate in device pixels
            pressure: Touch pressure (0.0 to 1.0)
            action_button: Action button state
        """
        if self._control_queue is None:
            return

        from scrcpy_py_ddlx.core.control import ControlMessage, ControlMessageType

        msg = ControlMessage(ControlMessageType.INJECT_TOUCH_EVENT)
        msg.set_touch_event(
            action=action,
            pointer_id=pointer_id,
            position_x=position_x,
            position_y=position_y,
            screen_width=self._device_size[0],
            screen_height=self._device_size[1],
            pressure=pressure,
            action_button=action_button,
            buttons=self._mouse_buttons_state,
        )
        self._control_queue.put(msg)

    def _send_scroll_event(
        self,
        position_x: int,
        position_y: int,
        hscroll: float,
        vscroll: float,
    ) -> None:
        """
        Send a scroll event to the device.

        Following official scrcpy mouse_sdk.c design.

        Args:
            position_x: X coordinate in device pixels
            position_y: Y coordinate in device pixels
            hscroll: Horizontal scroll amount (-1.0 to 1.0)
            vscroll: Vertical scroll amount (-1.0 to 1.0)
        """
        if self._control_queue is None:
            return

        from scrcpy_py_ddlx.core.control import ControlMessage, ControlMessageType

        msg = ControlMessage(ControlMessageType.INJECT_SCROLL_EVENT)
        msg.set_scroll_event(
            position_x=position_x,
            position_y=position_y,
            screen_width=self._device_size[0],
            screen_height=self._device_size[1],
            hscroll=hscroll,
            vscroll=vscroll,
            buttons=self._mouse_buttons_state,
        )
        self._control_queue.put(msg)

    def _send_keycode_event(
        self,
        keycode: int,
        action: AndroidKeyEventAction,
        repeat: int = 0,
    ) -> None:
        """
        Send a keycode event to the device.

        Following official scrcpy input_manager.c design.

        Args:
            keycode: Android keycode
            action: Key event action (DOWN/UP/MULTIPLE)
            repeat: Repeat count
        """
        if self._control_queue is None:
            return

        from scrcpy_py_ddlx.core.control import ControlMessage, ControlMessageType

        msg = ControlMessage(ControlMessageType.INJECT_KEYCODE)
        msg.set_keycode(action, keycode, repeat, metastate=0)
        self._control_queue.put(msg)


class CoordinateMapper:
    """
    Helper class for coordinate mapping between widget and device space.

    This class handles the conversion from widget coordinates (screen pixels)
    to device coordinates (Android device pixels), accounting for aspect
    ratio and scaling.
    """

    def __init__(self):
        """Initialize coordinate mapper."""
        self._frame_width: int = 0
        self._frame_height: int = 0
        self._device_size: Tuple[int, int] = (0, 0)

    def set_frame_size(self, width: int, height: int) -> None:
        """Set the frame size."""
        self._frame_width = width
        self._frame_height = height

    def set_device_size(self, width: int, height: int) -> None:
        """Set the device screen size."""
        self._device_size = (width, height)

    def _get_render_rect(self, widget_width: int, widget_height: int) -> Tuple[int, int, int, int]:
        """
        Get the rendered video rectangle.

        Args:
            widget_width: Widget width in pixels
            widget_height: Widget height in pixels

        Returns:
            Tuple of (x, y, width, height) in widget coordinates
        """
        if self._frame_width == 0 or self._frame_height == 0:
            return 0, 0, widget_width, widget_height

        width = self._frame_width
        height = self._frame_height

        # Calculate scaling to fit while maintaining aspect ratio
        scale_x = widget_width / width
        scale_y = widget_height / height
        scale = min(scale_x, scale_y)

        render_w = int(width * scale)
        render_h = int(height * scale)

        x = (widget_width - render_w) // 2
        y = (widget_height - render_h) // 2

        return x, y, render_w, render_h

    def map_to_device_coords(
        self,
        widget_x: int,
        widget_y: int,
        widget_width: int,
        widget_height: int,
    ) -> Tuple[int, int]:
        """
        Map widget coordinates to device coordinates.

        Args:
            widget_x: Widget X coordinate
            widget_y: Widget Y coordinate
            widget_width: Widget width in pixels
            widget_height: Widget height in pixels

        Returns:
            Tuple of (device_x, device_y), or (-1, -1) if outside video area
        """
        if self._device_size == (0, 0):
            return widget_x, widget_y

        render_x, render_y, render_w, render_h = self._get_render_rect(
            widget_width, widget_height
        )

        # Check if point is within rendered area
        if not (render_x <= widget_x < render_x + render_w and
                render_y <= widget_y < render_y + render_h):
            return -1, -1  # Outside video area

        # Map to device coordinates
        device_x = (widget_x - render_x) * self._device_size[0] // render_w
        device_y = (widget_y - render_y) * self._device_size[1] // render_h

        # Clamp to device size
        device_x = max(0, min(device_x, self._device_size[0] - 1))
        device_y = max(0, min(device_y, self._device_size[1] - 1))

        return device_x, device_y


__all__ = [
    "InputHandler",
    "CoordinateMapper",
]
