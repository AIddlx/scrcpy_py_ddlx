"""
Opus Passthrough Recorder - Direct OPUS packet capture without re-encoding.

This recorder intercepts raw OPUS packets from the demuxer and writes them
directly to an Ogg container, achieving zero-loss recording with minimal CPU.

Audio flow:
    Server OPUS packet → Ogg container → .ogg file

No decoding or re-encoding occurs.
"""

import logging
import struct
import threading
import time
from pathlib import Path
from typing import Optional, Any, Callable

logger = logging.getLogger(__name__)


class OpusPassthroughRecorder:
    """
    Records raw OPUS packets directly to Ogg container.

    This is the most efficient recording method:
    - No decoding (saves CPU)
    - No re-encoding (preserves quality)
    - Minimal latency

    The output is a standard .ogg file playable by most players.
    """

    # Ogg page header constants
    OGG_CAPTURE_PATTERN = b'OggS'
    OGG_VERSION = 0

    def __init__(self, filename: str, max_duration: Optional[float] = None):
        """
        Initialize passthrough recorder.

        Args:
            filename: Output filename (should end with .ogg)
            max_duration: Maximum recording duration in seconds
        """
        self._filename = filename
        self._max_duration = max_duration

        # Ensure .ogg extension
        if not filename.lower().endswith('.ogg'):
            self._filename = str(Path(filename).with_suffix('.ogg'))

        # Audio parameters (will be set from first config packet)
        self._sample_rate = 48000
        self._channels = 2
        self._preskip = 312  # Default Opus preskip

        # Recording state
        self._is_open = False
        self._is_recording = False
        self._granule_position = 0
        self._page_sequence = 0
        self._serial_number = int(time.time() * 1000) & 0xFFFFFFFF

        # Buffer for packets
        self._packet_buffer = []
        self._buffer_lock = threading.Lock()

        # File handle
        self._file_handle = None
        self._start_time = None
        self._bytes_written = 0

        # OpusHead and OpusTags written flag
        self._header_written = False

    def open(self, sample_rate: int = 48000, channels: int = 2,
             preskip: int = 312) -> bool:
        """
        Open the recorder with audio parameters.

        Args:
            sample_rate: Sample rate (typically 48000)
            channels: Number of channels (1 or 2)
            preskip: Opus preskip samples
        """
        if self._is_open:
            logger.warning("Recorder already open")
            return True

        self._sample_rate = sample_rate
        self._channels = channels
        self._preskip = preskip

        try:
            self._file_handle = open(self._filename, 'wb')
            self._is_open = True
            self._is_recording = True
            self._start_time = time.time()

            # Write Ogg Opus header
            self._write_opus_header()

            logger.info(f"OpusPassthroughRecorder opened: {self._filename}")
            logger.info(f"  Sample rate: {sample_rate} Hz")
            logger.info(f"  Channels: {channels}")

            return True

        except Exception as e:
            logger.error(f"Failed to open recorder: {e}")
            return False

    def _write_opus_header(self):
        """Write OpusHead and OpusTags pages."""
        # OpusHead packet
        opus_head = struct.pack(
            '<8sBBHIHH',
            b'OpusHead',  # Magic signature
            1,            # Version
            self._channels,
            self._preskip,
            self._sample_rate,
            0,            # Output gain
            0,            # Channel mapping family
        )

        self._write_ogg_page([opus_head], granule_position=0, header_type=0x02)

        # OpusTags packet
        vendor = b'scrcpy-py-ddlx'
        opus_tags = struct.pack(
            '<8sI',
            b'OpusTags',
            len(vendor),
        ) + vendor + struct.pack('<I', 0)  # 0 user comments

        self._write_ogg_page([opus_tags], granule_position=0, header_type=0x00)

        self._header_written = True

    def push_packet(self, packet_data: bytes, pts: int = 0) -> bool:
        """
        Receive a raw OPUS packet and buffer it.

        Args:
            packet_data: Raw OPUS packet bytes
            pts: Presentation timestamp (in samples)

        Returns:
            True if packet was accepted
        """
        if not self._is_recording:
            return False

        # Check max duration
        if self._max_duration:
            elapsed = time.time() - self._start_time
            if elapsed >= self._max_duration:
                logger.info(f"Max duration ({self._max_duration}s) reached")
                self._is_recording = False
                self.close()
                return False

        with self._buffer_lock:
            self._packet_buffer.append((packet_data, pts))
            self._bytes_written += len(packet_data)

        return True

    def flush(self):
        """Write buffered packets to file."""
        with self._buffer_lock:
            if not self._packet_buffer:
                return

            packets = []
            total_samples = 0

            for data, pts in self._packet_buffer:
                packets.append(data)
                # Estimate frame size (Opus frames are 2.5, 5, 10, 20, 40, or 60 ms)
                # Default to 20ms = 960 samples at 48kHz
                total_samples += 960 * (self._channels if self._channels == 1 else 1)

            self._granule_position += total_samples

            # Write as Ogg page
            header_type = 0x00  # Normal page
            self._write_ogg_page(packets, self._granule_position, header_type)

            self._packet_buffer.clear()

    def _write_ogg_page(self, packets: list, granule_position: int, header_type: int):
        """
        Write an Ogg page containing the given packets.

        Args:
            packets: List of packet data bytes
            granule_position: Granule position for this page
            header_type: Ogg page header type flags
        """
        # Combine packets
        data = b''.join(packets)

        # Calculate segment table
        segment_table = []
        remaining = len(data)
        while remaining > 0:
            segment_size = min(255, remaining)
            segment_table.append(segment_size)
            remaining -= segment_size

        # If last segment is exactly 255, add a 0 to indicate end
        if segment_table and segment_table[-1] == 255:
            segment_table.append(0)

        # Build page header
        header = struct.pack(
            '<4sBBQIIIB',
            self.OGG_CAPTURE_PATTERN,
            self.OGG_VERSION,
            header_type,
            granule_position,
            self._serial_number,
            self._page_sequence,
            0,  # CRC (placeholder, calculated below)
            len(segment_table),
        )

        # Segment table
        segment_table_bytes = bytes(segment_table)

        # Calculate CRC
        page_without_crc = header + segment_table_bytes + data
        crc = self._calculate_crc32(page_without_crc)

        # Insert CRC into header
        header = header[:22] + struct.pack('<I', crc) + header[26:]

        # Write page
        self._file_handle.write(header)
        self._file_handle.write(segment_table_bytes)
        self._file_handle.write(data)

        self._page_sequence += 1

    def _calculate_crc32(self, data: bytes) -> int:
        """Calculate Ogg CRC32."""
        # Ogg uses a polynomial of 0x04C11DB7
        crc_table = []
        for i in range(256):
            crc = i << 24
            for _ in range(8):
                if crc & 0x80000000:
                    crc = (crc << 1) ^ 0x04C11DB7
                else:
                    crc <<= 1
                crc &= 0xFFFFFFFF
            crc_table.append(crc)

        crc = 0
        for byte in data:
            crc = ((crc << 8) ^ crc_table[((crc >> 24) ^ byte) & 0xFF]) & 0xFFFFFFFF

        return crc

    def close(self):
        """Close the recorder and finalize the file."""
        if not self._is_open:
            return

        self._is_recording = False

        # Flush remaining packets
        self.flush()

        # Write end-of-stream page
        if self._file_handle:
            # Empty page with eos flag
            header = struct.pack(
                '<4sBBQIIIB',
                self.OGG_CAPTURE_PATTERN,
                self.OGG_VERSION,
                0x04,  # End of stream
                self._granule_position,
                self._serial_number,
                self._page_sequence,
                0,
                0,
            )
            crc = self._calculate_crc32(header + b'')
            header = header[:22] + struct.pack('<I', crc) + header[26:]
            self._file_handle.write(header)

            self._file_handle.close()
            self._file_handle = None

        self._is_open = False

        duration = self._granule_position / self._sample_rate
        file_size = Path(self._filename).stat().st_size / 1024

        logger.info(f"OpusPassthroughRecorder closed: {self._filename}")
        logger.info(f"  Duration: {duration:.2f} sec")
        logger.info(f"  File size: {file_size:.1f} KB")

    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self._is_recording

    def get_duration(self) -> float:
        """Get current recording duration in seconds."""
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    def get_file_size(self) -> int:
        """Get estimated file size in bytes."""
        return self._bytes_written


class TeePassthroughRecorder:
    """
    Tee recorder that passes raw packets to passthrough recorder
    while also forwarding to a decoder for playback.

    This allows recording raw OPUS while still hearing the audio.
    """

    def __init__(self, recorder: OpusPassthroughRecorder, decoder_or_player):
        """
        Initialize tee recorder.

        Args:
            recorder: OpusPassthroughRecorder for recording
            decoder_or_player: Audio decoder or player for playback
        """
        self._recorder = recorder
        self._decoder = decoder_or_player
        self._packet_count = 0

    def open(self, sample_rate: int = 48000, channels: int = 2) -> bool:
        """Open both recorder and player."""
        self._recorder.open(sample_rate=sample_rate, channels=channels)
        return True

    def push_packet(self, packet_data: bytes, pts: int = 0) -> bool:
        """
        Receive raw OPUS packet.

        Note: This receives packets BEFORE decoding.
        The decoder will decode them separately.
        """
        if self._recorder.is_recording():
            self._recorder.push_packet(packet_data, pts)
            self._packet_count += 1
        return True

    def close(self):
        """Close recorder."""
        self._recorder.close()
        logger.info(f"TeePassthroughRecorder closed, packets recorded: {self._packet_count}")
