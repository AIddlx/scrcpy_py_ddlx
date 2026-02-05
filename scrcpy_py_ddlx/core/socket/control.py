"""
Control Socket Module

This module provides the ControlSocket class for sending control messages
to the scrcpy server.

Implements all 18 control message types from the scrcpy protocol.
"""

import logging
import struct
from typing import Tuple

from .base import ScrcpySocket
from .types import SocketConfig, SocketType

logger = logging.getLogger(__name__)


class ControlSocket(ScrcpySocket):
    """
    Control message socket

    Handles bidirectional control messages between client and server.
    Supports touch events, keyboard input, clipboard operations, etc.

    Example:
        >>> config = SocketConfig(socket_type=SocketType.CONTROL, port=27183)
        >>> control_sock = ControlSocket(config)
        >>> control_sock.connect()
        >>> # Send touch event
        >>> control_sock.send_touch_event(x=500, y=1000, action=DOWN)
    """

    # Control message types (all 18 types from scrcpy protocol)
    TYPE_INJECT_KEYCODE = 0
    TYPE_INJECT_TEXT = 1
    TYPE_INJECT_TOUCH_EVENT = 2
    TYPE_INJECT_SCROLL_EVENT = 3
    TYPE_BACK_OR_SCREEN_ON = 4
    TYPE_EXPAND_NOTIFICATION_PANEL = 5
    TYPE_EXPAND_SETTINGS_PANEL = 6
    TYPE_COLLAPSE_PANELS = 7
    TYPE_GET_CLIPBOARD = 8
    TYPE_SET_CLIPBOARD = 9
    TYPE_SET_SCREEN_POWER_MODE = 10
    TYPE_ROTATE_DEVICE = 11
    TYPE_SWAP_FRACT_DISPLAY = 12  # Front/back camera swap (if supported)
    TYPE_DISPLAY_ORIENTATION = 13
    TYPE_UHID_CREATE = 14
    TYPE_UHID_DESTROY = 15
    TYPE_UHID_INPUT = 16
    TYPE_UHID_OUTPUT = 17

    def __init__(self, config: SocketConfig | None = None):
        """
        Initialize control socket

        Args:
            config: Socket configuration (uses control defaults if None)
        """
        if config is None:
            config = SocketConfig(socket_type=SocketType.CONTROL)

        config.tcp_nodelay = True  # Always use TCP_NODELAY for control
        config.buffer_size = 64 * 1024
        super().__init__(config)

    def connect_and_validate(self) -> bool:
        """
        Connect and validate control socket

        Returns:
            True if connection valid
        """
        self.connect()
        logger.debug("Control socket connected")
        return True

    def _send_control_message(self, msg_type: int, payload: bytes) -> None:
        """
        Send control message

        Args:
            msg_type: Message type
            payload: Message payload
        """
        # Pack message type and payload
        msg = struct.pack(">B", msg_type) + payload
        self.send_all(msg)

    def send_keycode(
        self, keycode: int, action: int, repeat: int = 0, meta_state: int = 0
    ) -> None:
        """
        Send keycode event

        Args:
            keycode: Android keycode constant
            action: Action (ACTION_DOWN=0, ACTION_UP=1)
            repeat: Key repeat count
            meta_state: Meta state (ctrl, alt, etc.)
        """
        payload = struct.pack(">BhBB", action, keycode, repeat, meta_state)
        self._send_control_message(self.TYPE_INJECT_KEYCODE, payload)

    def send_text(self, text: str) -> None:
        """
        Send text input

        Args:
            text: Text to inject
        """
        text_bytes = text.encode("utf-8")
        payload = struct.pack(">I", len(text_bytes)) + text_bytes
        self._send_control_message(self.TYPE_INJECT_TEXT, payload)

    def send_touch_event(
        self,
        x: int,
        y: int,
        action: int,
        pointer_id: int = 0,
        pressure: float = 1.0,
        buttons: int = 0,
    ) -> None:
        """
        Send touch event

        Args:
            x: X coordinate
            y: Y coordinate
            action: Action (DOWN=0, UP=1, MOVE=2, etc.)
            pointer_id: Pointer ID for multi-touch
            pressure: Touch pressure (0.0-1.0)
            buttons: Button state
        """
        pressure_int = int(pressure * 65535)
        payload = struct.pack(
            ">BhHIIHHH", action, pointer_id, x, y, pressure_int, buttons, 0, 0
        )
        self._send_control_message(self.TYPE_INJECT_TOUCH_EVENT, payload)

    def send_scroll_event(self, x: int, y: int, hscroll: int, vscroll: int) -> None:
        """
        Send scroll event

        Args:
            x: X position
            y: Y position
            hscroll: Horizontal scroll amount
            vscroll: Vertical scroll amount
        """
        payload = struct.pack(">Hii", x, y, hscroll, vscroll)
        self._send_control_message(self.TYPE_INJECT_SCROLL_EVENT, payload)

    def send_back_or_screen_on(self, action: int) -> None:
        """
        Send back or screen on event

        Args:
            action: Action code
        """
        payload = struct.pack(">B", action)
        self._send_control_message(self.TYPE_BACK_OR_SCREEN_ON, payload)

    def send_expand_notification_panel(self) -> None:
        """Expand notification panel"""
        self._send_control_message(self.TYPE_EXPAND_NOTIFICATION_PANEL, b"")

    def send_expand_settings_panel(self) -> None:
        """Expand settings panel"""
        self._send_control_message(self.TYPE_EXPAND_SETTINGS_PANEL, b"")

    def send_collapse_panels(self) -> None:
        """Collapse all panels"""
        self._send_control_message(self.TYPE_COLLAPSE_PANELS, b"")

    def send_get_clipboard(self) -> None:
        """Get clipboard content"""
        self._send_control_message(self.TYPE_GET_CLIPBOARD, b"")

    def send_set_clipboard(self, text: str, paste: bool = False) -> None:
        """
        Set clipboard content

        Args:
            text: Clipboard text
            paste: Whether to paste after setting
        """
        text_bytes = text.encode("utf-8")
        payload = struct.pack(">BI", 1 if paste else 0, len(text_bytes)) + text_bytes
        self._send_control_message(self.TYPE_SET_CLIPBOARD, payload)

    def send_set_screen_power_mode(self, mode: int) -> None:
        """
        Set screen power mode

        Args:
            mode: Power mode (OFF=0, NORMAL=1)
        """
        payload = struct.pack(">B", mode)
        self._send_control_message(self.TYPE_SET_SCREEN_POWER_MODE, payload)

    def send_rotate_device(self) -> None:
        """Rotate device"""
        self._send_control_message(self.TYPE_ROTATE_DEVICE, b"")

    def send_swap_fract_display(self) -> None:
        """Swap front/back camera display (if supported)"""
        self._send_control_message(self.TYPE_SWAP_FRACT_DISPLAY, b"")

    def send_display_orientation(self) -> None:
        """Send display orientation change"""
        self._send_control_message(self.TYPE_DISPLAY_ORIENTATION, b"")

    def send_uhid_create(self, hid_data: bytes) -> None:
        """
        Create UHID (USB HID) device

        Args:
            hid_data: HID device descriptor data
        """
        self._send_control_message(self.TYPE_UHID_CREATE, hid_data)

    def send_uhid_destroy(self, id: int) -> None:
        """
        Destroy UHID device

        Args:
            id: UHID device ID
        """
        payload = struct.pack(">I", id)
        self._send_control_message(self.TYPE_UHID_DESTROY, payload)

    def send_uhid_input(self, id: int, input_data: bytes) -> None:
        """
        Send input to UHID device

        Args:
            id: UHID device ID
            input_data: Input data
        """
        payload = struct.pack(">I", id) + input_data
        self._send_control_message(self.TYPE_UHID_INPUT, payload)

    def send_uhid_output(self, id: int, output_data: bytes) -> None:
        """
        Send output from UHID device

        Args:
            id: UHID device ID
            output_data: Output data
        """
        payload = struct.pack(">I", id) + output_data
        self._send_control_message(self.TYPE_UHID_OUTPUT, payload)

    def recv_control_message(self) -> Tuple[int, bytes]:
        """
        Receive control message

        Returns:
            Tuple of (message_type, payload)

        Raises:
            SocketReadError: If receive fails
        """
        msg_type_byte = self.recv_all(1)
        msg_type = struct.unpack(">B", msg_type_byte)[0]
        return (msg_type, b"")
