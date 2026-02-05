"""
Control message serialization for scrcpy.

This module provides functionality to serialize control messages that are sent
to the Android device during a scrcpy session. It implements all control message
types defined in the scrcpy protocol.
"""

import struct
import threading
import logging
from typing import Optional, Union
from collections import deque

# Import protocol enums and constants
from .protocol import (
    ControlMessageType,
    CopyKey,
    AndroidKeyEventAction as KeyEventAction,
    AndroidMotionEventAction as MotionEventAction,
    AndroidMotionEventButtons as MotionEventType,
    AndroidMetaState as MetaState,
    POINTER_ID_MOUSE,
    POINTER_ID_GENERIC_FINGER,
    POINTER_ID_VIRTUAL_FINGER,
    CONTROL_MSG_INJECT_TEXT_MAX_LENGTH,
    CONTROL_MSG_CLIPBOARD_TEXT_MAX_LENGTH,
    SCROLL_MULTIPLIER,
    PRESSURE_MULTIPLIER,
)

logger = logging.getLogger(__name__)


class ControlMessage:
    """
    Represents a control message to be sent to the Android device.

    This class provides methods to create different types of control messages
    and serialize them to bytes for transmission.
    """

    def __init__(self, msg_type: ControlMessageType):
        """
        Initialize a control message.

        Args:
            msg_type: The type of control message
        """
        self.type = msg_type
        self._data = {}

    def set_keycode(
        self, action: KeyEventAction, keycode: int, repeat: int = 0, metastate: int = 0
    ):
        """
        Set keycode injection parameters.

        Args:
            action: Key event action (DOWN, UP, or MULTIPLE)
            keycode: Android keycode constant
            repeat: Repeat count (0 for no repeat)
            metastate: Meta key state (0 for none, or SHIFT/ALT/CTRL from AndroidMetaState)
        """
        self._data["action"] = action
        self._data["keycode"] = keycode
        self._data["repeat"] = repeat
        self._data["metastate"] = metastate

    def set_text(self, text: str):
        """
        Set text to inject.

        Args:
            text: UTF-8 text string to inject
        """
        self._data["text"] = text

    def set_touch_event(
        self,
        action: MotionEventAction,
        pointer_id: int,
        position_x: int,
        position_y: int,
        screen_width: int,
        screen_height: int,
        pressure: float = 0.0,
        action_button: int = 0,
        buttons: int = 0,
    ):
        """
        Set touch event parameters.

        Args:
            action: Motion event action
            pointer_id: Pointer identifier (use POINTER_ID_* constants)
            position_x: X coordinate in screen pixels
            position_y: Y coordinate in screen pixels
            screen_width: Screen width in pixels
            screen_height: Screen height in pixels
            pressure: Touch pressure (0.0 to 1.0)
            action_button: Action button state
            buttons: Button state bitmask
        """
        self._data["action"] = action
        self._data["pointer_id"] = pointer_id
        self._data["position_x"] = position_x
        self._data["position_y"] = position_y
        self._data["screen_width"] = screen_width
        self._data["screen_height"] = screen_height
        self._data["pressure"] = max(0.0, min(1.0, pressure))
        self._data["action_button"] = action_button
        self._data["buttons"] = buttons

    def set_scroll_event(
        self,
        position_x: int,
        position_y: int,
        screen_width: int,
        screen_height: int,
        hscroll: float,
        vscroll: float,
        buttons: int = 0,
    ):
        """
        Set scroll event parameters.

        Args:
            position_x: X coordinate in screen pixels
            position_y: Y coordinate in screen pixels
            screen_width: Screen width in pixels
            screen_height: Screen height in pixels
            hscroll: Horizontal scroll amount (typically -16 to 16)
            vscroll: Vertical scroll amount (typically -16 to 16)
            buttons: Button state bitmask
        """
        self._data["position_x"] = position_x
        self._data["position_y"] = position_y
        self._data["screen_width"] = screen_width
        self._data["screen_height"] = screen_height
        self._data["hscroll"] = max(-16.0, min(16.0, hscroll))
        self._data["vscroll"] = max(-16.0, min(16.0, vscroll))
        self._data["buttons"] = buttons

    def set_back_or_screen_on(self, action: KeyEventAction):
        """
        Set back or screen on action.

        Args:
            action: Key event action (DOWN turns screen on)
        """
        self._data["action"] = action

    def set_copy_key(self, copy_key: CopyKey):
        """
        Set clipboard copy key.

        Args:
            copy_key: Copy key type (NONE, COPY, or CUT)
        """
        self._data["copy_key"] = copy_key

    def set_clipboard(self, sequence: int, text: str, paste: bool = False):
        """
        Set clipboard content.

        Args:
            sequence: Clipboard sequence number
            text: Text to set in clipboard
            paste: Whether to paste after setting
        """
        self._data["sequence"] = sequence
        self._data["text"] = text
        self._data["paste"] = paste

    def set_display_power(self, on: bool):
        """
        Set display power state.

        Args:
            on: True to turn display on, False to turn off
        """
        self._data["on"] = on

    def set_uhid_create(
        self,
        id: int,
        vendor_id: int,
        product_id: int,
        name: Optional[str],
        report_desc: bytes,
    ) -> None:
        """
        Set UHID create parameters.

        Args:
            id: UHID device ID
            vendor_id: Vendor ID
            product_id: Product ID
            name: Device name (optional)
            report_desc: HID report descriptor
        """
        self._data["id"] = id
        self._data["vendor_id"] = vendor_id
        self._data["product_id"] = product_id

        if name is not None:
            name_bytes = name.encode("utf-8")[:127]
            self._data["name_len"] = len(name_bytes)
            self._data["name"] = name_bytes

        if report_desc is not None:
            report_desc_len = len(report_desc)
            self._data["report_desc_len"] = report_desc_len
            self._data["report_desc"] = report_desc

    def set_uhid_input(self, id: int, data: bytes) -> None:
        """
        Set UHID input parameters.

        Args:
            id: UHID device ID
            data: Input data
        """
        self._data["id"] = id
        self._data["data"] = data

    def set_expand_notification_panel(self) -> None:
        """
        Expand notification panel.
        """
        self._data["action"] = 0  # DOWN

    def set_expand_settings_panel(self) -> None:
        """
        Expand settings panel.
        """
        self._data["action"] = 0  # DOWN

    def set_collapse_panels(self) -> None:
        """
        Collapse all panels.
        """
        self._data["action"] = 0  # DOWN

    def set_open_hard_keyboard_settings(self) -> None:
        """
        Open hard keyboard settings.
        """
        self._data["action"] = 0  # DOWN

    def set_start_app(self, name: str) -> None:
        """
        Start an application.

        Args:
            name: Package or activity name (max 255 bytes)
                Format: "com.example.app" or "com.example.app/com.example.MainActivity"
        """
        self._data["name"] = name

    def set_reset_video(self) -> None:
        """
        Reset video stream.
        """
        # Empty message, no additional data needed

    def set_rotate_device(self) -> None:
        """
        Rotate device portrait/landscape.
        """
        # Empty message, no additional data needed

    def set_uhid_destroy(self, id: int):
        """
        Set UHID destroy parameters.

        Args:
            id: UHID device ID to destroy
        """
        self._data["id"] = id

    def serialize(self) -> bytes:
        """
        Serialize control message to bytes.

        Returns:
            Serialized message as bytes

        Raises:
            ValueError: If message data is invalid
        """
        buf = bytearray()

        if self.type == ControlMessageType.INJECT_KEYCODE:
            buf.append(self.type)
            action = self._data.get("action", KeyEventAction.DOWN)
            keycode = self._data.get("keycode", 0)
            repeat = self._data.get("repeat", 0)
            metastate = self._data.get("metastate", 0)

            buf.append(action)
            buf.extend(struct.pack(">I", keycode))
            buf.extend(struct.pack(">I", repeat))
            buf.extend(struct.pack(">I", metastate))

        elif self.type == ControlMessageType.INJECT_TEXT:
            buf.append(self.type)
            text = self._data.get("text", "")
            text_bytes = text.encode("utf-8")[:CONTROL_MSG_INJECT_TEXT_MAX_LENGTH]
            buf.extend(struct.pack(">I", len(text_bytes)))
            buf.extend(text_bytes)

        elif self.type == ControlMessageType.INJECT_TOUCH_EVENT:
            buf.append(self.type)
            action = self._data.get("action", MotionEventAction.DOWN)
            pointer_id = self._data.get("pointer_id", POINTER_ID_GENERIC_FINGER)
            position_x = self._data.get("position_x", 0)
            position_y = self._data.get("position_y", 0)
            screen_width = self._data.get("screen_width", 1080)
            screen_height = self._data.get("screen_height", 1920)
            pressure = self._data.get("pressure", 0.0)
            action_button = self._data.get("action_button", 0)
            buttons = self._data.get("buttons", 0)

            buf.append(action)
            buf.extend(struct.pack(">Q", pointer_id & 0xFFFFFFFFFFFFFFFF))
            buf.extend(struct.pack(">i", position_x))
            buf.extend(struct.pack(">i", position_y))
            buf.extend(struct.pack(">H", screen_width))
            buf.extend(struct.pack(">H", screen_height))

            # Convert pressure (0.0-1.0) to uint16 fixed point (0-PRESSURE_MULTIPLIER, per official spec)
            pressure_u16 = int(pressure * PRESSURE_MULTIPLIER)
            pressure_u16 = max(0, min(PRESSURE_MULTIPLIER - 1, pressure_u16))
            buf.extend(struct.pack(">H", pressure_u16))

            buf.extend(struct.pack(">I", action_button))
            buf.extend(struct.pack(">I", buttons))

        elif self.type == ControlMessageType.INJECT_SCROLL_EVENT:
            buf.append(self.type)
            position_x = self._data.get("position_x", 0)
            position_y = self._data.get("position_y", 0)
            screen_width = self._data.get("screen_width", 1080)
            screen_height = self._data.get("screen_height", 1920)
            hscroll = self._data.get("hscroll", 0.0)
            vscroll = self._data.get("vscroll", 0.0)
            buttons = self._data.get("buttons", 0)

            buf.extend(struct.pack(">i", position_x))
            buf.extend(struct.pack(">i", position_y))
            buf.extend(struct.pack(">H", screen_width))
            buf.extend(struct.pack(">H", screen_height))

            # Fixed-point encoding: scroll value (-1.0 to 1.0) to int16 (multiplier SCROLL_MULTIPLIER)
            # Clamp to int16 range [-SCROLL_MULTIPLIER, SCROLL_MULTIPLIER-1] to handle edge case of exactly 1.0
            hscroll_i16 = max(-SCROLL_MULTIPLIER, min(SCROLL_MULTIPLIER - 1, int(max(-1.0, min(1.0, hscroll)) * SCROLL_MULTIPLIER)))
            vscroll_i16 = max(-SCROLL_MULTIPLIER, min(SCROLL_MULTIPLIER - 1, int(max(-1.0, min(1.0, vscroll)) * SCROLL_MULTIPLIER)))
            buf.extend(struct.pack(">h", hscroll_i16))
            buf.extend(struct.pack(">h", vscroll_i16))

            buf.extend(struct.pack(">I", buttons))

        elif self.type == ControlMessageType.BACK_OR_SCREEN_ON:
            buf.append(self.type)
            action = self._data.get("action", KeyEventAction.DOWN)
            buf.append(action)

        elif self.type == ControlMessageType.GET_CLIPBOARD:
            buf.append(self.type)
            copy_key = self._data.get("copy_key", CopyKey.NONE)
            buf.append(copy_key)

        elif self.type == ControlMessageType.SET_CLIPBOARD:
            buf.append(self.type)
            sequence = self._data.get("sequence", 0)
            text = self._data.get("text", "")
            paste = self._data.get("paste", False)

            buf.extend(struct.pack(">Q", sequence))
            buf.append(1 if paste else 0)

            text_bytes = text.encode("utf-8")[:CONTROL_MSG_CLIPBOARD_TEXT_MAX_LENGTH]
            buf.extend(struct.pack(">I", len(text_bytes)))
            buf.extend(text_bytes)

        elif self.type == ControlMessageType.SET_DISPLAY_POWER:
            buf.append(self.type)
            on = self._data.get("on", True)
            buf.append(1 if on else 0)

        elif self.type == ControlMessageType.UHID_CREATE:
            id = self._data.get("id", 0)
            vendor_id = self._data.get("vendor_id", 0)
            product_id = self._data.get("product_id", 0)
            name = self._data.get("name", "")
            report_desc = self._data.get("report_desc", b"")
            report_desc_size = self._data.get("report_desc_size", len(report_desc))

            buf.extend(struct.pack(">H", id))
            buf.extend(struct.pack(">H", vendor_id))
            buf.extend(struct.pack(">H", product_id))

            name_bytes = name.encode("utf-8")[:127]
            buf.append(len(name_bytes))
            buf.extend(name_bytes)

            buf.extend(struct.pack(">H", report_desc_size))
            buf.extend(report_desc)

        elif self.type == ControlMessageType.UHID_INPUT:
            id = self._data.get("id", 0)
            data = self._data.get("data", b"")
            size = self._data.get("size", len(data))

            buf.extend(struct.pack(">H", id))
            buf.extend(struct.pack(">H", size))
            buf.extend(data)

        elif self.type == ControlMessageType.UHID_DESTROY:
            id = self._data.get("id", 0)
            buf.extend(struct.pack(">H", id))

        elif self.type == ControlMessageType.START_APP:
            buf.append(self.type)  # Don't forget the type byte!
            name = self._data.get("name", "")
            name_bytes = name.encode("utf-8")[:255]
            buf.extend(struct.pack(">B", len(name_bytes)))  # 1 byte length
            buf.extend(name_bytes)

        elif self.type in [
            ControlMessageType.EXPAND_NOTIFICATION_PANEL,
            ControlMessageType.EXPAND_SETTINGS_PANEL,
            ControlMessageType.COLLAPSE_PANELS,
            ControlMessageType.ROTATE_DEVICE,
            ControlMessageType.OPEN_HARD_KEYBOARD_SETTINGS,
            ControlMessageType.RESET_VIDEO,
            ControlMessageType.SCREENSHOT,
            ControlMessageType.GET_APP_LIST,  # Empty message: only type byte
        ]:
            # Empty messages: only type byte, no additional data
            buf.append(self.type)


        else:
            logger.warning(f"Unknown message type: {self.type}")

        return bytes(buf)

    def is_droppable(self) -> bool:
        """
        Check if this message can be dropped if the buffer is full.

        Some messages (like UHID_CREATE and UHID_DESTROY) must never be dropped
        to avoid inconsistencies.

        Returns:
            True if message can be dropped, False otherwise
        """
        return self.type not in [
            ControlMessageType.UHID_CREATE,
            ControlMessageType.UHID_DESTROY,
        ]

    def __str__(self) -> str:
        """String representation of the control message."""
        type_name = (
            self.type.name
            if isinstance(self.type, ControlMessageType)
            else str(self.type)
        )

        if self.type == ControlMessageType.INJECT_KEYCODE:
            action = self._data.get("action", KeyEventAction.DOWN).name
            keycode = self._data.get("keycode", 0)
            return f"ControlMessage(INJECT_KEYCODE, action={action}, keycode={keycode})"

        elif self.type == ControlMessageType.INJECT_TEXT:
            text = self._data.get("text", "")
            return f"ControlMessage(INJECT_TEXT, text='{text[:20]}...')"

        elif self.type == ControlMessageType.INJECT_TOUCH_EVENT:
            action = self._data.get("action", MotionEventAction.DOWN).name
            x = self._data.get("position_x", 0)
            y = self._data.get("position_y", 0)
            return f"ControlMessage(INJECT_TOUCH_EVENT, action={action}, pos=({x},{y}))"

        elif self.type == ControlMessageType.INJECT_SCROLL_EVENT:
            x = self._data.get("position_x", 0)
            y = self._data.get("position_y", 0)
            hscroll = self._data.get("hscroll", 0.0)
            vscroll = self._data.get("vscroll", 0.0)
            return f"ControlMessage(INJECT_SCROLL_EVENT, pos=({x},{y}), scroll=({hscroll},{vscroll}))"

        elif self.type == ControlMessageType.SET_CLIPBOARD:
            text = self._data.get("text", "")
            paste = self._data.get("paste", False)
            return (
                f"ControlMessage(SET_CLIPBOARD, text='{text[:20]}...', paste={paste})"
            )

        return f"ControlMessage({type_name})"


class ControlMessageQueue:
    """
    Thread-safe queue for managing control messages.

    Based on official scrcpy controller queue design:
    - 64 slots total (60 droppable + 4 non-droppable)
    - Droppable messages can be dropped when queue is full
    - Non-droppable messages (UHID_CREATE/DESTROY) always enqueued
    - Queue expands for non-droppable messages if needed
    """

    # Official scrcpy queue sizes
    MAX_DROPPABLE_SIZE = 60   # Maximum droppable messages
    MAX_NON_DROPPABLE_SIZE = 4  # Reserved for non-droppable messages
    MAX_QUEUE_SIZE = MAX_DROPPABLE_SIZE + MAX_NON_DROPPABLE_SIZE  # 64 total

    def __init__(self, max_droppable: int = MAX_DROPPABLE_SIZE):
        """
        Initialize the control message queue.

        Args:
            max_droppable: Maximum number of droppable messages (default: 60)
        """
        self._queue: deque[ControlMessage] = deque()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._max_droppable = max_droppable
        self._dropped_count = 0

    @property
    def _max_size(self) -> int:
        """Maximum queue size including non-droppable messages."""
        return self._max_droppable + self.MAX_NON_DROPPABLE_SIZE

    MAX_NON_DROPPABLE_SIZE = MAX_NON_DROPPABLE_SIZE

    def put(self, msg: ControlMessage) -> bool:
        """
        Add a message to the queue.

        Queue behavior (per official scrcpy):
        - Droppable messages: limited to 60 slots, drop oldest if full
        - Non-droppable messages (UHID_CREATE/DESTROY): always enqueued,
          queue expands if needed

        Args:
            msg: Control message to add

        Returns:
            True if message was added, False only if critical error
        """
        with self._lock:
            if msg.is_droppable():
                # Droppable message: enforce 60-slot limit
                while len(self._queue) >= self._max_droppable:
                    # Check if we can drop the oldest message
                    if self._queue[0].is_droppable():
                        self._queue.popleft()
                        self._dropped_count += 1
                        logger.debug(
                            f"Dropped droppable control message (total dropped: {self._dropped_count})"
                        )
                    else:
                        # Queue full of non-droppable messages, shouldn't happen
                        logger.warning("Droppable queue full of non-droppable messages")
                        return False

                self._queue.append(msg)
                self._cond.notify_all()  # Wake up waiting threads
                return True
            else:
                # Non-droppable message: always enqueue, expand queue if needed
                self._queue.append(msg)
                logger.debug(f"Non-droppable message enqueued (queue size: {len(self._queue)})")
                self._cond.notify_all()
                return True

    def get(self, timeout: Optional[float] = None) -> Optional[ControlMessage]:
        """
        Get a message from the queue.

        Args:
            timeout: Maximum time to wait in seconds, or None to wait forever

        Returns:
            Control message or None if timeout
        """
        with self._cond:
            if not self._queue and timeout is not None:
                # Wait for message with timeout
                if not self._cond.wait(timeout):
                    return None
                if self._queue:
                    return self._queue.popleft()
                return None
            return self._queue.popleft()
            return None

    def peek(self) -> Optional[ControlMessage]:
        """
        Peek at the first message without removing it.

        Returns:
            First control message or None if queue is empty
        """
        with self._lock:
            if self._queue:
                return self._queue[0]
            return None

    def clear(self):
        """Clear all messages from the queue."""
        with self._lock:
            self._queue.clear()
            self._dropped_count = 0

    def size(self) -> int:
        """
        Get the current number of messages in the queue.

        Returns:
            Number of messages
        """
        with self._lock:
            return len(self._queue)

    def is_empty(self) -> bool:
        """
        Check if the queue is empty.

        Returns:
            True if queue is empty, False otherwise
        """
        with self._lock:
            return len(self._queue) == 0

    def get_dropped_count(self) -> int:
        """
        Get the total number of dropped messages.

        Returns:
            Number of dropped messages
        """
        with self._lock:
            return self._dropped_count
