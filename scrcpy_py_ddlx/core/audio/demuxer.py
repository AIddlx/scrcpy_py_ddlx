"""
Audio demuxer implementations.

This module contains both buffer-based and streaming audio demuxers
for parsing audio streams from scrcpy.
"""

import logging
import socket
from typing import Optional
from queue import Queue

from scrcpy_py_ddlx.core.demuxer.base import BaseDemuxer, DEFAULT_DEMUXER_BUFFER_SIZE

logger = logging.getLogger(__name__)


class AudioDemuxer(BaseDemuxer):
    """
    Audio stream demuxer for scrcpy.

    This demuxer reads audio packets from the audio socket and passes them
    to the audio decoder or player.

    Based on official scrcpy audio demuxer (app/src/demuxer.c).

    Note: Audio support in scrcpy requires the server to be started with
    audio enabled. Not all scrcpy servers support audio.

    Example:
        >>> packet_queue = Queue(maxsize=30)
        >>> demuxer = AudioDemuxer(audio_socket, packet_queue, codec_id=AudioCodec.OPUS)
        >>> demuxer.start()
        >>> # Audio packets will appear in packet_queue
        >>> demuxer.stop()
    """

    # Audio codec types (scrcpy uses raw audio or opus)
    RAW = 0
    OPUS = 1
    AAC = 2
    FDK_AAC = 3

    def __init__(
        self,
        sock: socket.socket,
        packet_queue: Queue,
        audio_codec: int = OPUS,
        buffer_size: int = DEFAULT_DEMUXER_BUFFER_SIZE
    ):
        """
        Initialize the audio demuxer.

        Args:
            sock: Audio socket to read from
            packet_queue: Queue for parsed audio packets
            audio_codec: Audio codec type (RAW, OPUS, AAC, or FDK_AAC)
            buffer_size: Receive buffer size (default: 256KB)
        """
        super().__init__(sock, packet_queue, buffer_size)
        self._audio_codec = audio_codec

    def _parse_buffer(self, buffer: bytearray, size: int) -> int:
        """
        Parse audio packets from buffer.

        Audio packet format is simpler than video:
        - No 12-byte header
        - Raw audio data or codec frames

        Args:
            buffer: Data buffer
            size: Number of bytes in buffer

        Returns:
            Number of bytes consumed
        """
        # For audio, we typically pass the entire buffer as one "packet"
        # since audio frames are smaller and more predictable
        if size == 0:
            return 0

        # Create a simple audio packet wrapper
        audio_packet = {
            "codec": self._audio_codec,
            "data": bytes(buffer[:size]),
            "size": size,
        }

        # Put packet in queue (BLOCKING to prevent drops)
        try:
            self._packet_queue.put(audio_packet, timeout=1.0)
            self._packets_parsed += 1
        except:
            logger.warning("Audio packet queue timeout, skipping packet")

        return size

    def _parse_buffer_with_offset(self, view: memoryview, size: int) -> int:
        """
        Parse audio packets from buffer using memoryview (optimized).

        This avoids creating a temporary bytes object for the audio data.

        Args:
            view: Memoryview of data buffer
            size: Number of bytes in buffer

        Returns:
            Number of bytes consumed
        """
        if size == 0:
            return 0

        # Create audio packet using memoryview (no extra copy)
        audio_packet = {
            "codec": self._audio_codec,
            "data": bytes(view[:size]),  # One copy for the packet dict
            "size": size,
        }

        # Put packet in queue (BLOCKING to prevent drops)
        try:
            self._packet_queue.put(audio_packet, timeout=1.0)
            self._packets_parsed += 1
        except:
            logger.warning("Audio packet queue timeout, skipping packet")

        return size

    def _get_thread_name(self) -> str:
        """Get the thread name for this demuxer."""
        codec_names = {
            self.RAW: "RAW",
            self.OPUS: "OPUS",
            self.AAC: "AAC",
            self.FDK_AAC: "FDK-AAC",
        }
        codec_name = codec_names.get(self._audio_codec, "Unknown")
        return f"AudioDemuxer-{codec_name}"


import struct
from typing import Callable
from scrcpy_py_ddlx.core.demuxer.base import StreamingDemuxerBase, IncompleteReadError


class StreamingAudioDemuxer(StreamingDemuxerBase):
    """
    Streaming audio demuxer.

    Reads audio packets using header-first strategy:
    1. Read 4 bytes codec ID (ONLY on first packet - "opus", "aac", etc)
    2. Read 12 bytes scrcpy packet header (same as video)
    3. Parse header to get pts_flags and payload size
    4. Read exactly payload_size bytes
    5. Queue complete packet

    Based on official scrcpy audio demuxer (app/src/demuxer.c).

    Audio packet format:
        [codec_id: 4 bytes] [scrcpy header: 12 bytes] [audio data: N bytes]

    The codec_id is sent ONLY ONCE at the start of the stream.
    """

    # Scrcpy packet header size (same as video)
    SC_PACKET_HEADER_SIZE = 12  # bytes (8 + 4)

    # Codec ID size (sent once at start)
    CODEC_ID_SIZE = 4  # bytes

    def __init__(
        self,
        sock: socket.socket,
        packet_queue: Queue,
        audio_codec: int = 1,  # OPUS
        stats_callback: Optional[Callable] = None
    ):
        """
        Initialize audio demuxer.

        Args:
            sock: Audio socket
            packet_queue: Queue for audio packet dictionaries
            audio_codec: Audio codec type (RAW/OPUS/AAC/FDK-AAC)
            stats_callback: Optional statistics callback
        """
        super().__init__(sock, packet_queue, stats_callback)
        self._audio_codec = audio_codec
        self._codec_id_read = False  # Track if we've read the initial codec ID

    def _recv_packet(self) -> dict:
        """
        Receive a complete audio packet.

        Returns:
            Audio packet dictionary with keys: codec, data, size, pts

        Raises:
            IncompleteReadError: Connection closed mid-packet
            ValueError: Invalid header or size
        """
        try:
            # Step 1: Read codec ID on FIRST packet only
            if not self._codec_id_read:
                logger.info("[AUDIO] Waiting for codec ID...")
                codec_id_bytes = self._recv_exact(self.CODEC_ID_SIZE)
                codec_id = struct.unpack('>I', codec_id_bytes)[0]
                logger.info(f"Audio codec ID: 0x{codec_id:08x} ({codec_id_bytes.decode('ascii', errors='ignore')})")
                self._codec_id_read = True

            # Step 2: Read exactly 12 bytes for scrcpy packet header
            logger.debug("[AUDIO] Waiting for packet header...")
            header_data = self._recv_exact(self.SC_PACKET_HEADER_SIZE)
            logger.debug(f"[AUDIO] Received header: {len(header_data)} bytes")

            # Parse header fields (same format as video packet)
            # Format: >QI = big-endian uint64, uint32
            pts_flags, payload_size = struct.unpack('>QI', header_data)

            # Extract flags
            is_config = bool(pts_flags & (1 << 63))
            is_key_frame = bool(pts_flags & (1 << 62))
            pts = pts_flags & 0x3FFFFFFFFFFFFFFF

            # Step 3: Validate and read payload
            if payload_size > self.MAX_PACKET_SIZE:
                self._parse_errors += 1
                raise ValueError(
                    f"Audio packet size {payload_size} exceeds maximum "
                    f"{self.MAX_PACKET_SIZE}"
                )

            payload = self._recv_exact(payload_size)

            # Return raw audio data (not dict) for AudioDecoder
            # Audio decoder expects raw bytes, not a dictionary
            logger.info(
                f"[AUDIO] Received packet: size={payload_size}, pts={pts}, config={is_config}"
            )

            return payload

        except struct.error as e:
            self._parse_errors += 1
            logger.error(f"Failed to parse audio header: {e}")
            raise

        except socket.timeout:
            # Timeout is expected when device is not playing audio
            # Just re-raise to let the demuxer loop continue
            raise

        except Exception as e:
            self._parse_errors += 1
            logger.error(f"Error receiving audio packet: {e}")
            raise

    def _get_thread_name(self) -> str:
        """Get thread name for logging."""
        codec_names = {
            0: "RAW",
            1: "OPUS",
            2: "AAC",
            3: "FDK-AAC",
        }
        codec_name = codec_names.get(self._audio_codec, "Unknown")
        return f"StreamingAudioDemuxer-{codec_name}"
