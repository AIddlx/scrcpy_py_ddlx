"""
Device message deserialization and receiver for scrcpy.

This module provides functionality to:
1. Deserialize device messages received from Android device
2. Run a receiver thread to read and process device messages
3. Handle clipboard, ACK, and UHID messages via callbacks

Based on official scrcpy receiver implementation (app/src/receiver.c)
"""

import struct
import logging
import threading
from typing import Optional, Tuple, Callable, Any, List, Dict
from dataclasses import dataclass
from enum import Enum
from socket import socket as Socket, timeout as SocketTimeout, error as SocketError
from ctypes import memmove

# Import protocol constants
from .protocol import (
    CopyKey,
    DEVICE_NAME_FIELD_LENGTH,
    CONTROL_MSG_CLIPBOARD_TEXT_MAX_LENGTH,
)

logger = logging.getLogger(__name__)


# Device message types (from official scrcpy)
class DeviceMessageType(Enum):
    """Device message types from server to client"""
    CLIPBOARD = 0           # Clipboard content sync
    ACK_CLIPBOARD = 1        # Clipboard acknowledge
    UHID_OUTPUT = 2         # UHID output data
    APP_LIST = 3            # List of installed applications


# Receiver buffer size (from official scrcpy)
DEVICE_MSG_MAX_SIZE = 256 * 1024  # 256KB buffer


@dataclass
class ClipboardEvent:
    """
    Clipboard event received from device.

    Attributes:
        text: Clipboard text content
        sequence: Sequence number (for ACK)
    """
    text: str
    sequence: int


@dataclass
class UHIDOutputEvent:
    """
    UHID output event received from device.

    Attributes:
        id: UHID device ID
        data: Output data
        size: Data size
    """
    id: int
    data: bytes
    size: int


@dataclass
class ReceiverCallbacks:
    """
    Callback functions for receiver thread events.

    These callbacks are called from the receiver thread to notify
    the main thread about device events.
    """
    on_clipboard: Optional[Callable[[str, int], None]]
    on_uhid_output: Optional[Callable[[int, bytes, int], None]]
    on_app_list: Optional[Callable[[List[Dict[str, Any]]], None]]


class DeviceMessageReceiver:
    """
    Receiver thread for processing device messages from scrcpy server.

    Based on official scrcpy receiver implementation (app/src/receiver.c).
    This receiver runs in a dedicated thread and reads device messages
    from the control socket, parsing them and triggering callbacks.

    Example:
        >>> def on_clipboard(text, sequence):
        ...     print(f"Clipboard: {text}")
        >>>
        >>> receiver = DeviceMessageReceiver(
        ...     socket=control_socket,
        ...     callbacks=ReceiverCallbacks(on_clipboard=on_clipboard)
        ... )
        >>> receiver.start()
        >>> # Device messages will trigger callbacks
        >>> receiver.stop()
    """

    def __init__(
        self,
        socket: Socket,
        callbacks: ReceiverCallbacks,
        buffer_size: int = DEVICE_MSG_MAX_SIZE
    ):
        """
        Initialize device message receiver.

        Args:
            socket: Control socket to read messages from
            callbacks: Callback functions for device events
            buffer_size: Size of receive buffer (default: 256KB)
        """
        self._socket = socket
        self._callbacks = callbacks
        self._buffer_size = buffer_size

        # Threading
        self._thread: Optional[threading.Thread] = None
        self._stopped = threading.Event()

        # Statistics
        self._messages_received = 0
        self._clipboard_events = 0
        self._uhid_events = 0

    def start(self) -> None:
        """
        Start the receiver thread.

        The thread will continuously read from the control socket,
        parse device messages, and trigger callbacks.
        """
        if self._thread is not None:
            logger.warning("Receiver thread already running")
            return

        self._stopped.clear()
        self._thread = threading.Thread(
            target=self._run_receiver_loop,
            name="DeviceReceiver",
            daemon=True
        )
        self._thread.start()
        logger.info("Device message receiver thread started")

    def stop(self) -> None:
        """Stop the receiver thread and wait for it to finish."""
        if self._thread is None:
            return

        logger.info("Stopping device message receiver...")
        self._stopped.set()

        # Close socket to interrupt blocking recv
        try:
            self._socket.close()
        except Exception as e:
            logger.debug(f"Error closing socket: {e}")

        # Wait for thread to finish
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            logger.warning("Receiver thread did not stop gracefully")

        self._thread = None
        logger.info("Device message receiver stopped")

    def _run_receiver_loop(self) -> None:
        """Main receiver loop (runs in dedicated thread)."""
        buffer = bytearray(self._buffer_size)
        offset = 0

        try:
            while not self._stopped.is_set():
                try:
                    # Receive data into buffer
                    # Use recv() instead of recv_into() for better Windows compatibility
                    chunk_size = min(4096, self._buffer_size - offset)
                    chunk = self._socket.recv(chunk_size)

                    if not chunk:
                        logger.info(f"Device socket closed (buffer had {offset} bytes)")
                        break

                    size = len(chunk)
                    buffer[offset:offset + size] = chunk
                    offset += size
                    logger.debug(f"DeviceReceiver: Received {size} bytes (total buffer: {offset})")

                    # Process all complete messages in buffer
                    while offset > 0:
                        consumed = self._process_buffer(buffer, offset)

                        if consumed > 0:
                            # Remove processed data from buffer
                            if consumed < offset:
                                memmove(buffer, buffer + consumed, offset - consumed)
                            offset -= consumed
                        else:
                            # Incomplete message, wait for more data
                            break

                except SocketTimeout:
                    continue
                except SocketError as e:
                    if not self._stopped.is_set():
                        logger.error(f"Receiver socket error: {e}")
                    break

        except Exception as e:
            logger.error(f"Receiver loop error: {e}")
        finally:
            logger.info("Device receiver loop ended")

    def _process_buffer(self, buffer: bytearray, size: int) -> int:
        """
        Process device messages in buffer.

        Args:
            buffer: Data buffer
            size: Number of bytes in buffer

        Returns:
            Number of bytes consumed (0 if message incomplete)
        """
        if size < 1:
            return 0  # Need at least message type byte

        # Read message type
        msg_type = buffer[0]
        logger.debug(f"DeviceReceiver: Processing message type={msg_type}, size={size}")

        if msg_type == DeviceMessageType.CLIPBOARD.value:
            return self._process_clipboard(buffer, size)

        elif msg_type == DeviceMessageType.ACK_CLIPBOARD.value:
            return self._process_ack_clipboard(buffer, size)

        elif msg_type == DeviceMessageType.UHID_OUTPUT.value:
            return self._process_uhid_output(buffer, size)

        elif msg_type == DeviceMessageType.APP_LIST.value:
            return self._process_app_list(buffer, size)

        else:
            logger.warning(f"Unknown device message type: {msg_type}")
            # Try to skip unknown message
            return size

    def _process_clipboard(self, buffer: bytearray, size: int) -> int:
        """
        Process clipboard message (type 0).

        Format:
        - 1 byte: type = 0
        - 4 bytes: length (big-endian)
        - N bytes: UTF-8 text (null-terminated)

        Returns:
            Number of bytes consumed
        """
        if size < 5:  # type (1) + length (4) + at least 1 byte text
            return 0

        # Read length
        text_length = struct.unpack(">I", buffer[1:5])[0]

        if size < 5 + text_length:
            return 0  # Incomplete message

        # Extract text
        text_bytes = buffer[5:5 + text_length]
        # Remove null termination
        text_bytes = text_bytes.rstrip(b"\x00")
        text = text_bytes.decode("utf-8", errors="ignore")

        logger.info(f"[DeviceReceiver] CLIPBOARD message received: text_length={text_length}, text='{text[:50]}...'")

        # Trigger callback
        if self._callbacks.on_clipboard:
            try:
                # Note: We don't have sequence number in this format,
                # official scrcpy uses acksync for sequence tracking
                self._callbacks.on_clipboard(text, 0)
                self._clipboard_events += 1
                logger.debug(f"Clipboard callback executed successfully")
            except Exception as e:
                logger.error(f"Clipboard callback error: {e}")
        else:
            logger.warning("[DeviceReceiver] Clipboard message received but no callback registered!")

        return 5 + text_length

    def _process_ack_clipboard(self, buffer: bytearray, size: int) -> int:
        """
        Process ACK clipboard message (type 1).

        Format:
        - 1 byte: type = 1
        - 8 bytes: sequence (big-endian)

        Returns:
            Number of bytes consumed (always 9 if size >= 9)
        """
        if size < 9:
            return 0

        # Read sequence
        sequence = struct.unpack(">Q", buffer[1:9])[0]

        # This could trigger acksync callback
        logger.debug(f"Received clipboard ACK for sequence: {sequence}")
        self._messages_received += 1

        return 9

    def _process_uhid_output(self, buffer: bytearray, size: int) -> int:
        """
        Process UHID output message (type 2).

        Format:
        - 1 byte: type = 2
        - 2 bytes: id (big-endian)
        - 2 bytes: size (big-endian)
        - N bytes: data

        Returns:
            Number of bytes consumed
        """
        if size < 5:
            return 0

        # Read id and size
        uhid_id = struct.unpack(">H", buffer[1:3])[0]
        uhid_size = struct.unpack(">H", buffer[3:5])[0]

        if size < 5 + uhid_size:
            return 0  # Incomplete message

        # Extract data
        data = bytes(buffer[5:5 + uhid_size])

        # Trigger callback
        if self._callbacks.on_uhid_output:
            try:
                self._callbacks.on_uhid_output(uhid_id, data, uhid_size)
                self._uhid_events += 1
                logger.debug(f"UHID output: id={uhid_id}, size={uhid_size}")
            except Exception as e:
                logger.error(f"UHID callback error: {e}")

        return 5 + uhid_size

    def _process_app_list(self, buffer: bytearray, size: int) -> int:
        """
        Process app list message (type 3).

        Format:
        - 1 byte: type = 3
        - 2 bytes: count (big-endian, number of apps)
        - For each app:
          - 1 byte: system flag (0 = user app, 1 = system app)
          - 2 bytes: name length (big-endian)
          - N bytes: name (UTF-8)
          - 2 bytes: package length (big-endian)
          - M bytes: package name (UTF-8)

        Returns:
            Number of bytes consumed (0 if message incomplete)
        """
        logger.info(f"[_process_app_list] Called with size={size}")
        if size < 3:  # type (1) + count (2)
            logger.warning(f"[_process_app_list] Message too short: {size} < 3")
            return 0

        # Read app count
        count = struct.unpack(">H", buffer[1:3])[0]
        offset = 3
        apps = []

        # Parse each app
        for i in range(count):
            # Check we have at least: system (1) + name_len (2)
            if size < offset + 3:
                logger.warning(f"[_process_app_list] Incomplete header at app {i}, offset={offset}, size={size}")
                return 0  # Incomplete message

            system = buffer[offset]
            name_len = struct.unpack(">H", buffer[offset + 1:offset + 3])[0]
            offset += 3  # Move past system (1) + name_len (2)

            # Check we have name + pkg_len (2)
            if size < offset + name_len + 2:
                logger.warning(f"[_process_app_list] Incomplete name/pkg_len at app {i}, need {name_len + 2} bytes, have {size - offset}")
                return 0  # Incomplete message

            # Extract name
            name = buffer[offset:offset + name_len].decode('utf-8', errors='ignore')
            offset += name_len

            # Now read pkg_len (after name data)
            pkg_len = struct.unpack(">H", buffer[offset:offset + 2])[0]
            offset += 2

            # Check we have package data
            if size < offset + pkg_len:
                logger.warning(f"[_process_app_list] Incomplete package at app {i}, need {pkg_len} bytes, have {size - offset}")
                return 0  # Incomplete message

            # Extract package
            package = buffer[offset:offset + pkg_len].decode('utf-8', errors='ignore')
            offset += pkg_len

            apps.append({
                "name": name,
                "package": package,
                "system": bool(system)
            })

        logger.info(f"[DeviceReceiver] APP_LIST message received: {len(apps)} apps")

        # Trigger callback
        if self._callbacks.on_app_list:
            try:
                self._callbacks.on_app_list(apps)
                logger.info(f"[DeviceReceiver] App list callback executed successfully")
            except Exception as e:
                logger.error(f"App list callback error: {e}")
                import traceback
                traceback.print_exc()
        else:
            logger.warning("[DeviceReceiver] App list message received but no callback registered!")

        return offset


class DeviceMessageParser:
    """
    Parses device messages received from scrcpy server.

    Device messages are sent from server to client and include:
    - Device info (name + dimensions)
    - Clipboard content changes
    - Clipboard synchronization events
    - Acknowledgment messages
    """

    def __init__(self):
        """Initialize device message parser."""
        pass

    def parse_device_info(self, data: bytes) -> str:
        """
        Parse device information message.

        Device info format (from video socket):
        - 64 bytes: device_name (null-terminated string)

        Args:
            data: Raw bytes from video socket

        Returns:
            Device name string
        """
        if len(data) < DEVICE_NAME_FIELD_LENGTH:
            raise ValueError(
                f"Device info too short: {len(data)} bytes, "
                f"expected at least {DEVICE_NAME_FIELD_LENGTH}"
            )

        # Extract device name (null-terminated string)
        device_name_bytes = data[:DEVICE_NAME_FIELD_LENGTH]
        device_name = device_name_bytes.rstrip(b"\x00").decode("utf-8", errors="ignore")

        logger.info(f"Parsed device info: {device_name}")
        return device_name
