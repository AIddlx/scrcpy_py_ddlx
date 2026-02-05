"""
scrcpy_py_ddlx/core/stream.py

Stream parser for scrcpy video data packets.

This module handles parsing the scrcpy stream protocol, including:
- Parsing 12-byte packet headers
- Extracting PTS, flags, and payload size
- Handling CONFIG and KEY_FRAME packets
- Merging H.264/H.265 config packets with following media packets
"""

import struct
from dataclasses import dataclass
from typing import Optional, Tuple

from .protocol import (
    PACKET_FLAG_CONFIG,
    PACKET_FLAG_KEY_FRAME,
    PACKET_PTS_MASK,
    PACKET_HEADER_SIZE,
    is_config_packet,
    is_key_frame,
    extract_pts,
    CodecId,
)


@dataclass
class PacketHeader:
    """
    Represents a scrcpy packet header.

    Attributes:
        pts_flags: Raw PTS value with flags in the upper bits
        pts: Presentation Time Stamp (extracted from pts_flags)
        size: Size of the packet payload in bytes
        is_config: True if this is a configuration packet (SPS/PPS)
        is_key_frame: True if this is a key frame
        is_screenshot: True if this is a screenshot frame
    """
    pts_flags: int
    pts: int
    size: int
    is_config: bool
    is_key_frame: bool
    is_screenshot: bool = False  # Default to False for backward compatibility

    def __str__(self) -> str:
        """Return human-readable representation of the header."""
        flags = []
        if self.is_config:
            flags.append("CONFIG")
        if self.is_key_frame:
            flags.append("KEY_FRAME")
        if self.is_screenshot:
            flags.append("SCREENSHOT")
        flag_str = "|".join(flags) if flags else "NONE"
        return f"PacketHeader(pts={self.pts}, size={self.size}, flags={flag_str})"


@dataclass
class VideoPacket:
    """
    Represents a complete video packet with header and payload.

    Attributes:
        header: The packet header
        data: The packet payload data (may be merged with config data)
        codec_id: The codec ID (H264, H265, or AV1)
    """
    header: PacketHeader
    data: bytes
    codec_id: int

    @property
    def size(self) -> int:
        """Return the size of the packet payload."""
        return len(self.data)


class PacketMerger:
    """
    Merges configuration packets with media packets for H.264/H.265.

    In scrcpy, configuration packets (containing SPS/PPS for H.264 or
    VPS/SPS/PPS for H.265) must be prepended to the next media packet
    for correct decoding.

    This class buffers the most recent config packet and merges it with
    the following non-config packet.
    """

    def __init__(self) -> None:
        """Initialize the packet merger with no buffered config."""
        self._config_data: Optional[bytes] = None

    def merge(self, packet: VideoPacket) -> VideoPacket:
        """
        Merge a pending config packet with a media packet if applicable.

        Args:
            packet: The packet to potentially merge with buffered config

        Returns:
            The packet (possibly merged with config data)

        Note:
            If the input packet is a config packet, it is buffered and
            returned unchanged. If it's a media packet and a config is
            buffered, the config is prepended to the media packet data.
        """
        if packet.header.is_config:
            # Store the config packet for next media packet
            self._config_data = packet.data
            return packet

        if self._config_data is not None:
            # Prepend config data to media packet
            merged_data = self._config_data + packet.data
            self._config_data = None

            # Create new packet with merged data
            return VideoPacket(
                header=packet.header,
                data=merged_data,
                codec_id=packet.codec_id
            )

        return packet

    def clear(self) -> None:
        """Clear any buffered configuration packet."""
        self._config_data = None

    @property
    def has_pending_config(self) -> bool:
        """Check if there is a pending config packet to merge."""
        return self._config_data is not None


class StreamParser:
    """
    Parser for scrcpy video stream protocol.

    Handles parsing of the scrcpy stream format including:
    - Initial codec ID (4 bytes)
    - Video dimensions (8 bytes for video streams)
    - Sequence of video packets (12-byte header + payload)

    Example:
        >>> parser = StreamParser()
        >>> parser.parse_codec_id(socket)
        >>> parser.parse_video_size(socket)
        >>> while True:
        ...     packet = parser.parse_packet(socket, codec_id)
        ...     if packet is None:
        ...         break
    """

    def __init__(self) -> None:
        """Initialize the stream parser."""
        self._packet_merger = PacketMerger()
        self._codec_id: Optional[int] = None

    def parse_codec_id(self, data: bytes) -> Tuple[int, bytes]:
        """
        Parse the codec ID from stream data.

        Args:
            data: Raw bytes containing the codec ID (at least 4 bytes)

        Returns:
            Tuple of (codec_id, remaining_data)

        Raises:
            ValueError: If data is too short
        """
        if len(data) < 4:
            raise ValueError(f"Insufficient data for codec ID: need 4 bytes, got {len(data)}")

        codec_id = struct.unpack('>I', data[:4])[0]
        self._codec_id = codec_id
        return codec_id, data[4:]

    def parse_video_size(self, data: bytes) -> Tuple[int, int, bytes]:
        """
        Parse video dimensions from stream data.

        Args:
            data: Raw bytes containing width and height (at least 8 bytes)

        Returns:
            Tuple of (width, height, remaining_data)

        Raises:
            ValueError: If data is too short
        """
        if len(data) < 8:
            raise ValueError(f"Insufficient data for video size: need 8 bytes, got {len(data)}")

        width, height = struct.unpack('>II', data[:8])
        return width, height, data[8:]

    def parse_packet_header(self, data: bytes) -> Tuple[PacketHeader, bytes]:
        """
        Parse a single packet header from stream data.

        Args:
            data: Raw bytes containing the packet header (at least 12 bytes)

        Returns:
            Tuple of (PacketHeader, remaining_data)

        Raises:
            ValueError: If data is too short
        """
        if len(data) < PACKET_HEADER_SIZE:
            raise ValueError(
                f"Insufficient data for packet header: "
                f"need {PACKET_HEADER_SIZE} bytes, got {len(data)}"
            )

        # Unpack: 8 bytes pts_flags + 4 bytes size
        pts_flags, size = struct.unpack('>QI', data[:PACKET_HEADER_SIZE])

        has_config = is_config_packet(pts_flags)
        has_key_frame = is_key_frame(pts_flags)
        pts = extract_pts(pts_flags)

        header = PacketHeader(
            pts_flags=pts_flags,
            pts=pts,
            size=size,
            is_config=has_config,
            is_key_frame=has_key_frame
        )

        return header, data[PACKET_HEADER_SIZE:]

    def parse_packet(self, data: bytes, codec_id: int) -> Tuple[Optional[VideoPacket], bytes]:
        """
        Parse a complete packet from stream data.

        This method parses the packet header and payload, then applies
        packet merging for H.264/H.265 codecs.

        Args:
            data: Raw bytes containing the packet
            codec_id: The codec ID for this stream

        Returns:
            Tuple of (VideoPacket or None, remaining_data)
            Returns (None, data) if insufficient data for a complete packet

        Note:
            Config packets (SPS/PPS) are buffered and merged with the
            following media packet for H.264/H.265 codecs.
        """
        # Try to parse header first
        try:
            header, remaining_data = self.parse_packet_header(data)
        except ValueError:
            return None, data

        # Check if we have enough data for the payload
        if len(remaining_data) < header.size:
            return None, data

        # Extract payload
        payload = remaining_data[:header.size]
        new_remaining = remaining_data[header.size:]

        # Create video packet
        packet = VideoPacket(
            header=header,
            data=payload,
            codec_id=codec_id
        )

        # Apply config packet merging for H.264/H.265
        if codec_id in (CodecId.H264, CodecId.H265):
            packet = self._packet_merger.merge(packet)

        return packet, new_remaining

    def should_merge_config(self, codec_id: int) -> bool:
        """
        Determine if config packets should be merged for a given codec.

        Args:
            codec_id: The codec ID to check

        Returns:
            True if config packet merging is needed (H.264/H.265)
        """
        return codec_id in (CodecId.H264, CodecId.H265)

    def reset_merger(self) -> None:
        """Reset the packet merger state (clear buffered config)."""
        self._packet_merger.clear()


class DataBuffer:
    """
    Buffer for accumulating stream data.

    This helper class manages a growing buffer of bytes, useful for
    handling TCP streams where data may arrive in chunks.
    """

    def __init__(self, initial_data: bytes = b'') -> None:
        """
        Initialize the data buffer.

        Args:
            initial_data: Optional initial data to store
        """
        self._buffer = bytearray(initial_data)

    def feed(self, data: bytes) -> None:
        """
        Add new data to the buffer.

        Args:
            data: Data to append to the buffer
        """
        self._buffer.extend(data)

    def consume(self, size: int) -> bytes:
        """
        Consume and return the first N bytes from the buffer.

        Args:
            size: Number of bytes to consume

        Returns:
            The consumed bytes

        Raises:
            ValueError: If buffer doesn't contain enough bytes
        """
        if len(self._buffer) < size:
            raise ValueError(
                f"Cannot consume {size} bytes from buffer of size {len(self._buffer)}"
            )

        result = bytes(self._buffer[:size])
        del self._buffer[:size]
        return result

    def peek(self, size: int) -> bytes:
        """
        Peek at the first N bytes without consuming them.

        Args:
            size: Number of bytes to peek

        Returns:
            The peeked bytes

        Raises:
            ValueError: If buffer doesn't contain enough bytes
        """
        if len(self._buffer) < size:
            raise ValueError(
                f"Cannot peek {size} bytes from buffer of size {len(self._buffer)}"
            )

        return bytes(self._buffer[:size])

    @property
    def size(self) -> int:
        """Return the current size of the buffer."""
        return len(self._buffer)

    def __len__(self) -> int:
        """Return the current size of the buffer."""
        return len(self._buffer)

    def clear(self) -> None:
        """Clear all data from the buffer."""
        self._buffer.clear()


def parse_h264_nalu_type(data: bytes) -> int:
    """
    Parse H.264 NALU type from payload data.

    Args:
        data: H.264 NALU data (with or without start code)

    Returns:
        NALU type (1-31 for standard NALUs, or 0 for unknown)
    """
    # Skip start codes (0x00000001 or 0x000001)
    start = 0
    if len(data) >= 4 and data[:4] == b'\x00\x00\x00\x01':
        start = 4
    elif len(data) >= 3 and data[:3] == b'\x00\x00\x01':
        start = 3

    if len(data) > start:
        nalu_header = data[start]
        return nalu_header & 0x1F  # Lower 5 bits are NALU type

    return 0


def parse_h265_nalu_type(data: bytes) -> int:
    """
    Parse H.265 NALU type from payload data.

    Args:
        data: H.265 NALU data (with or without start code)

    Returns:
        NALU type (0-63)
    """
    # Skip start codes (0x00000001 or 0x000001)
    start = 0
    if len(data) >= 4 and data[:4] == b'\x00\x00\x00\x01':
        start = 4
    elif len(data) >= 3 and data[:3] == b'\x00\x00\x01':
        start = 3

    if len(data) > start:
        # H.265 NALU header is 2 bytes, type is in upper 6 bits of first byte
        nalu_header = data[start]
        return (nalu_header >> 1) & 0x3F

    return 0
