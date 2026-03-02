"""
Video demuxer implementations.

This module contains both buffer-based and streaming video demuxers
for parsing H.264/H.265/AV1 video streams from scrcpy.
"""

import logging
import socket
from typing import Optional
from queue import Queue

from .base import BaseDemuxer, DEFAULT_DEMUXER_BUFFER_SIZE
from ..protocol import CodecId
from ..stream import StreamParser, VideoPacket, PacketHeader

logger = logging.getLogger(__name__)


class VideoDemuxer(BaseDemuxer):
    """
    Video stream demuxer for scrcpy.

    This demuxer reads H.264/H.265/AV1 video packets from the video socket,
    parses them, and passes them to the video decoder.

    Based on official scrcpy video demuxer (app/src/demuxer.c).

    Example:
        >>> packet_queue = Queue(maxsize=30)
        >>> demuxer = VideoDemuxer(video_socket, packet_queue, codec_id=CodecId.H264)
        >>> demuxer.start()
        >>> # Packets will appear in packet_queue for decoder
        >>> demuxer.stop()
    """

    def __init__(
        self,
        sock: socket.socket,
        packet_queue: Queue,
        codec_id: int,
        buffer_size: int = DEFAULT_DEMUXER_BUFFER_SIZE
    ):
        """
        Initialize the video demuxer.

        Args:
            sock: Video socket to read from
            packet_queue: Queue for parsed video packets
            codec_id: Video codec ID (H264, H265, or AV1)
            buffer_size: Receive buffer size (default: 256KB)
        """
        super().__init__(sock, packet_queue, buffer_size)
        self._codec_id = codec_id
        self._parser = StreamParser()

    def _parse_buffer(self, buffer: bytearray, size: int) -> int:
        """
        Parse video packets from buffer.

        Args:
            buffer: Data buffer
            size: Number of bytes in buffer

        Returns:
            Number of bytes consumed (0 if packet incomplete)
        """
        try:
            packet, remaining = self._parser.parse_packet(
                bytes(buffer[:size]), self._codec_id
            )

            if packet is None:
                # Incomplete packet
                return 0

            # Put packet in queue for decoder (BLOCKING to prevent frame drops)
            try:
                self._packet_queue.put(packet, timeout=1.0)
                self._packets_parsed += 1
            except:
                # Queue timeout - decoder is too slow, log and skip
                logger.warning("Video packet queue timeout, skipping packet")

            # Return number of bytes consumed
            return size - len(remaining)

        except Exception as e:
            self._parse_errors += 1
            logger.error(f"Error parsing video packet: {e}")
            # Try to recover by consuming all data
            return size

    def _parse_buffer_with_offset(self, view: memoryview, size: int) -> int:
        """
        Parse video packets from buffer using memoryview (optimized).

        This avoids creating a temporary bytes object, reducing memory allocations.

        Args:
            view: Memoryview of data buffer
            size: Number of bytes in buffer

        Returns:
            Number of bytes consumed (0 if packet incomplete)
        """
        try:
            # Convert memoryview to bytes only for the parser
            # This is still more efficient than slicing a bytearray
            packet, remaining = self._parser.parse_packet(
                bytes(view[:size]), self._codec_id
            )

            if packet is None:
                # Incomplete packet
                return 0

            # CRITICAL: Log config packets and key frames for diagnosis
            # Config packets indicate codec parameter changes (SPS/PPS for H.264)
            # Key frames indicate refresh points (could help diagnose video issues)
            if packet.header.is_config:
                logger.info(f"[CONFIG] Config packet received: {packet.header.size} bytes")
            elif packet.header.is_key_frame:
                logger.info(f"[KEY_FRAME] Key frame: pts={packet.header.pts}, size={packet.header.size} bytes")

            # Log large packets that might cause issues (DEBUG level only)
            if packet.header.size > 100000:
                logger.debug(f"[LARGE PACKET] Packet size: {packet.header.size} bytes")

            # Put packet in queue for decoder (BLOCKING to prevent frame drops)
            # Blocking ensures frame sequence continuity for H.264/H.265 inter-frame coding
            # If queue is full, demuxer waits - this maintains decoder sync
            try:
                self._packet_queue.put(packet, timeout=1.0)
                self._packets_parsed += 1
            except:
                # Queue timeout - decoder is too slow, log and skip
                logger.warning("Video packet queue timeout, skipping packet")

            # Return number of bytes consumed
            return size - len(remaining)

        except Exception as e:
            self._parse_errors += 1
            logger.error(f"Error parsing video packet: {e}")
            # Try to recover by consuming all data
            return size

    def _get_thread_name(self) -> str:
        """Get the thread name for this demuxer."""
        codec_name = {
            CodecId.H264: "H264",
            CodecId.H265: "H265",
            CodecId.AV1: "AV1",
        }.get(self._codec_id, "Unknown")
        return f"VideoDemuxer-{codec_name}"


import struct
from typing import Callable
from .base import StreamingDemuxerBase, IncompleteReadError


class StreamingVideoDemuxer(StreamingDemuxerBase):
    """
    Streaming video demuxer.

    Reads video packets using header-first strategy:
    1. Read exactly 12 bytes (packet header)
    2. Parse header to get payload size
    3. Read exactly payload_size bytes
    4. Parse and queue complete packet

    Based on official scrcpy video demuxer (app/src/demuxer.c).
    """

    VIDEO_HEADER_SIZE = 12  # bytes

    def __init__(
        self,
        sock: socket.socket,
        packet_queue: Queue,
        codec_id: int,
        stats_callback: Optional[Callable] = None
    ):
        """
        Initialize video demuxer.

        Args:
            sock: Video socket
            packet_queue: Queue for VideoPacket objects
            codec_id: Video codec ID (H264/H265/AV1)
            stats_callback: Optional statistics callback
        """
        super().__init__(sock, packet_queue, stats_callback)
        self._codec_id = codec_id
        self._config_data: Optional[bytes] = None  # Buffer for config merging
        self._screenshot_queue: Optional[Queue] = None  # Queue for screenshot requests
        self._frame_size_changed_callback: Optional[Callable[[int, int], None]] = None  # For rotation

    def set_frame_size_changed_callback(self, callback: Callable[[int, int], None]) -> None:
        """
        Set callback for frame size changes (screen rotation).

        Args:
            callback: Function(width, height) called when frame size changes
        """
        self._frame_size_changed_callback = callback

    def set_screenshot_queue(self, queue: Queue) -> None:
        """
        Set queue to receive screenshot frames.

        When a screenshot packet (with VIDEO_PACKET_SCREENSHOT_FLAG) is received,
        it will be placed in this queue instead of the normal video queue.

        Args:
            queue: Queue to receive screenshot VideoPacket objects
        """
        self._screenshot_queue = queue

    # Known codec IDs for rotation detection
    _KNOWN_CODEC_IDS = {
        0x68323634: 'H264',  # 'h264'
        0x68323635: 'H265',  # 'h265'
        0x61763031: 'AV1',   # 'av01'
    }

    def _recv_packet(self) -> Optional[VideoPacket]:
        """
        Receive a complete video packet using streaming approach.

        Returns:
            VideoPacket or None if connection closed

        Raises:
            IncompleteReadError: Connection closed mid-packet
            ValueError: Invalid header or size
        """
        try:
            # Step 1: Read exactly 12 bytes for header
            header_data = self._recv_exact(self.VIDEO_HEADER_SIZE)

            # Debug: Log raw header bytes for diagnosis
            if self._packets_parsed < 5:
                logger.debug(f"Raw header bytes: {header_data.hex()}")

            # Step 2: Parse header
            pts_flags, payload_size = struct.unpack('>QI', header_data)
            is_config = bool(pts_flags & (1 << 63))
            is_key_frame = bool(pts_flags & (1 << 62))
            pts = pts_flags & 0x3FFFFFFFFFFFFFFF

            # Step 2.5: Detect codec header sent during rotation (ADB mode)
            # When server rotates, it sends: codec_id(4) + width(4) + height(4) = 12 bytes
            # If we parse this as pts_flags(8) + payload_size(4), we get garbage values
            # Check ANY time, not just when payload_size is invalid
            potential_codec_id = (pts_flags >> 32) & 0xFFFFFFFF

            # Check for known codec IDs (with or without high bits set)
            # When parsed as pts_flags, the high bits (62, 63) might be set incorrectly
            # So we check the lower 30 bits of the potential codec_id
            potential_codec_id_lower = potential_codec_id & 0x3FFFFFFF  # Mask off bits 30-31
            known_codec_ids_lower = {cid & 0x3FFFFFFF: name for cid, name in self._KNOWN_CODEC_IDS.items()}

            if potential_codec_id_lower in known_codec_ids_lower:
                # This could be a codec header from rotation!
                # Re-parse as: codec_id(4) + width(4) + height(4)
                codec_id = potential_codec_id
                width = (pts_flags >> 0) & 0xFFFFFFFF
                height = payload_size

                # Validate dimensions (reasonable screen sizes)
                if 100 <= width <= 4096 and 100 <= height <= 4096:
                    # Additional check: if this were a real video packet, pts would be much smaller
                    # Real pts values are typically < 10^15, but when parsing codec header as pts,
                    # the value is extremely large (codec_id shifted into high bits)
                    if pts > 10**15:  # Suspiciously large PTS indicates codec header
                        codec_name = known_codec_ids_lower[potential_codec_id_lower]
                        logger.warning(
                            f"[ROTATION-ADB] Detected codec header during rotation: "
                            f"codec=0x{codec_id:08x} ({codec_name}), "
                            f"size={width}x{height}"
                        )
                        # Update codec_id and notify callback
                        if self._codec_id != codec_id:
                            logger.info(f"[ROTATION-ADB] Codec changed: 0x{self._codec_id:08x} -> 0x{codec_id:08x}")
                            self._codec_id = codec_id

                        # Notify frame size change callback
                        if self._frame_size_changed_callback:
                            try:
                                self._frame_size_changed_callback(width, height)
                            except Exception as e:
                                logger.warning(f"[ROTATION-ADB] Callback error: {e}")

                        # Return None to skip this "packet" (it's actually a header)
                        return None

            # Step 3: Validate payload size
            if payload_size > self.MAX_PACKET_SIZE:
                self._parse_errors += 1
                raise ValueError(
                    f"Payload size {payload_size} exceeds maximum "
                    f"{self.MAX_PACKET_SIZE}"
                )

            # Step 4: Read exactly payload_size bytes for payload
            payload = self._recv_exact(payload_size)

            # Step 5: Create VideoPacket directly (no need for StreamParser)
            # We already parsed the header and know the exact structure
            header = PacketHeader(
                pts_flags=pts_flags,
                pts=pts,
                size=payload_size,
                is_config=is_config,
                is_key_frame=is_key_frame
            )

            packet = VideoPacket(
                header=header,
                data=payload,
                codec_id=self._codec_id
            )

            # Latency tracking: record receive time for ADB/TCP mode
            # In TCP mode, this tracks when we received the packet from socket
            try:
                from scrcpy_py_ddlx.latency_tracker import get_tracker
                import time
                recv_time = time.time()
                packet.packet_id = get_tracker().start_packet_with_time(recv_time, pts)
            except Exception:
                pass

            # Step 6: Handle config merging for H.264/H.265
            if self._codec_id in (CodecId.H264, CodecId.H265):
                packet = self._merge_config(packet)
                if packet is None:
                    # Config merging failed
                    return None

            # Step 7: Logging for special packets
            if packet.header.is_config:
                logger.info(f"[CONFIG] Config packet received: {packet.header.size} bytes")
            elif packet.header.is_key_frame:
                logger.debug(f"[KEY_FRAME] Key frame: pts={packet.header.pts}, size={packet.header.size} bytes")

            if packet.header.size > 100000:
                logger.debug(f"[LARGE PACKET] Size: {packet.header.size} bytes")

            return packet

        except struct.error as e:
            self._parse_errors += 1
            logger.error(f"Failed to parse video header: {e}")
            raise

        except Exception as e:
            self._parse_errors += 1
            # Socket timeout is normal when screen is static (no video packets)
            if "timed out" in str(e):
                logger.debug(f"Video packet receive timeout (screen may be static)")
            else:
                logger.error(f"Error receiving video packet: {e}")
            raise

    def _merge_config(self, packet: VideoPacket) -> Optional[VideoPacket]:
        """
        Handle config packets for H.264/H.265.

        Config is cached and will be sent to sinks (for recorder to use as extradata).
        Keyframes are sent WITHOUT config prepended - the recorder handles extradata.

        For the decoder, config will be prepended separately in the decoder thread.

        Returns:
            VideoPacket (including config packets for recorder to process)
        """
        if packet.header.is_config:
            # Store config for future reference
            self._config_data = packet.data
            logger.info(f"[CONFIG] Config packet received: {len(packet.data)} bytes")
            # Return config packet so recorder can set it as extradata
            return packet

        # Keyframes are sent as-is (without config prepended)
        # The recorder will use extradata, decoder may need separate handling
        return packet

    def _get_thread_name(self) -> str:
        """Get thread name for logging."""
        codec_name = {
            CodecId.H264: "H264",
            CodecId.H265: "H265",
            CodecId.AV1: "AV1",
        }.get(self._codec_id, "Unknown")
        return f"StreamingVideoDemuxer-{codec_name}"
