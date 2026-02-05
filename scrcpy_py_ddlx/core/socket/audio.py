"""
Audio Socket Module

This module provides the AudioSocket class for handling audio stream
reception from the scrcpy server.

Merges functionality from the original socket.py and audio_socket.py modules.
"""

import logging
import struct
from typing import Tuple

from .base import ScrcpySocket
from .types import SocketConfig, SocketType, SocketReadError

logger = logging.getLogger(__name__)


class AudioSocket(ScrcpySocket):
    """
    Audio stream socket

    Handles audio data reception from scrcpy server.
    Audio data is typically Opus/AAC/FLAC/RAW encoded.

    Example:
        >>> config = SocketConfig(socket_type=SocketType.AUDIO, port=27183)
        >>> audio_sock = AudioSocket(config)
        >>> audio_sock.connect_and_validate()
        >>> while True:
        ...     codec_id, payload_size, pts, flags, audio_data = audio_sock.recv_audio_packet()
        ...     # Process audio
    """

    def __init__(self, config: SocketConfig | None = None):
        """
        Initialize audio socket

        Args:
            config: Socket configuration (uses audio defaults if None)
        """
        if config is None:
            config = SocketConfig(socket_type=SocketType.AUDIO)

        config.buffer_size = 256 * 1024  # 256KB buffer for audio
        super().__init__(config)

    def connect_and_validate(self) -> bool:
        """
        Connect and validate audio socket

        Returns:
            True if connection valid
        """
        self.connect()
        logger.debug("Audio socket connected")
        return True

    def recv_audio_packet(self) -> Tuple[int, int, int, int, bytes]:
        """
        Receive a complete audio packet from scrcpy server.

        Audio packet format (16 bytes header):
        - Bytes 0-3: Codec ID (32-bit big-endian)
        - Bytes 4-11: PTS and Flags (64-bit big-endian)
        - Bytes 12-15: Payload Size (32-bit big-endian)

        Args:
            None

        Returns:
            Tuple of (codec_id, payload_size, pts, flags, payload_data)
            - codec_id: Audio codec ID (from CodecId enum)
            - payload_size: Size of audio payload in bytes
            - pts: Presentation timestamp
            - flags: Packet flags (CONFIG/KEY_FRAME)
            - payload_data: Raw audio payload bytes

        Raises:
            SocketReadError: If socket read fails
        """
        # Read 16-byte header
        header = self._recv(16)

        # Parse header fields
        codec_id = struct.unpack(">I", header[0:4])[0]
        pts_info = struct.unpack(">Q", header[4:12])[0]
        payload_size = struct.unpack(">I", header[12:16])[0]

        # Extract flags
        is_config = bool(pts_info & (1 << 63))
        is_key_frame = bool(pts_info & (1 << 62))
        pts = pts_info & 0x3FFFFFFFFFFFFFFF  # Lower 62 bits

        # Construct flags value
        flags = 0
        if is_config:
            flags |= 0x01
        if is_key_frame:
            flags |= 0x02

        # Read payload
        payload_data = self._recv(payload_size)

        logger.debug(
            f"Audio packet: codec={codec_id}, "
            f"size={payload_size}, "
            f"pts={pts}, "
            f"config={is_config}, "
            f"key_frame={is_key_frame}"
        )

        return codec_id, payload_size, pts, flags, payload_data

    def _recv(self, size: int) -> bytes:
        """
        Receive exact number of bytes from socket.

        Args:
            size: Number of bytes to receive

        Returns:
            Bytes received

        Raises:
            SocketReadError: If read fails
        """
        if not self.is_connected:
            raise SocketReadError("Socket not connected")

        data = bytearray()
        remaining = size

        while remaining > 0:
            chunk = self.recv(min(remaining, self.config.buffer_size))
            if not chunk:
                raise SocketReadError("Connection closed")

            data.extend(chunk)
            remaining -= len(chunk)

        return bytes(data)
