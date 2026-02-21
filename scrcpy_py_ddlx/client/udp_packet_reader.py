"""
UDP Packet Reader - Wraps UDP socket to strip custom header.

Provides a stream-like interface for UDP packets, compatible with
StreamingVideoDemuxer and StreamingAudioDemuxer.

Protocol format (from server UdpMediaSender):
    Normal packet:
        [seq: 4 bytes] [timestamp: 8 bytes] [flags: 4 bytes] [payload: N bytes]

    Fragmented packet (flags bit 31 = 1):
        [seq: 4 bytes] [timestamp: 8 bytes] [flags: 4 bytes] [frag_idx: 4 bytes] [fragment data: N bytes]

    For fragmented packets, the client must reassemble all fragments to get the complete scrcpy packet.
"""

import socket
import struct
import logging
import threading
from typing import Optional, Tuple, Dict, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class UdpPacketHeader:
    """Parsed UDP packet header."""
    sequence: int
    timestamp: int
    flags: int

    # Flag bits (from server UdpMediaSender)
    FLAG_KEY_FRAME = 1 << 0
    FLAG_CONFIG = 1 << 1
    FLAG_FRAGMENTED = 1 << 31

    @property
    def is_key_frame(self) -> bool:
        return bool(self.flags & self.FLAG_KEY_FRAME)

    @property
    def is_config(self) -> bool:
        return bool(self.flags & self.FLAG_CONFIG)

    @property
    def is_fragmented(self) -> bool:
        return bool(self.flags & self.FLAG_FRAGMENTED)


@dataclass
class FragmentBuffer:
    """Buffer for reassembling fragmented packets."""
    timestamp: int = 0
    flags: int = 0
    fragments: Dict[int, bytes] = field(default_factory=dict)
    expected_size: int = 0  # Expected total size from scrcpy header
    total_size: int = 0  # Total bytes received so far


class UdpPacketReader:
    """
    Wraps UDP socket to provide stream-like read interface.

    Strips the custom 16-byte header from each UDP packet and buffers
    the payload for streaming reads.

    Usage:
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.bind(("0.0.0.0", 27185))

        reader = UdpPacketReader(udp_sock)
        header = reader.recv(12)  # Read scrcpy header
        payload = reader.recv(size)  # Read payload

    Thread-safe for concurrent recv() calls.
    """

    # UDP header size: seq(4) + timestamp(8) + flags(4)
    HEADER_SIZE = 16

    # Maximum UDP payload
    MAX_UDP_PACKET = 65507

    def __init__(
        self,
        udp_socket: socket.socket,
        timeout: float = 5.0,
        strip_header: bool = True
    ):
        """
        Initialize UDP packet reader.

        Args:
            udp_socket: Bound UDP socket to read from
            timeout: Socket timeout in seconds
            strip_header: Whether to strip custom UDP header (default: True)
        """
        self._socket = udp_socket
        self._socket.settimeout(timeout)
        self._strip_header = strip_header

        # Buffer for incomplete reads
        self._buffer = b''
        self._lock = threading.Lock()

        # Fragment reassembly buffer (keyed by timestamp)
        self._fragment_buffers: Dict[int, FragmentBuffer] = {}

        # Statistics
        self._packets_received = 0
        self._bytes_received = 0
        self._header_bytes_dropped = 0
        self._empty_packets = 0
        self._fragments_reassembled = 0

        # Last packet info (for debugging)
        self._last_header: Optional[UdpPacketHeader] = None
        self._last_source: Optional[Tuple[str, int]] = None

        logger.debug(f"UdpPacketReader created, strip_header={strip_header}")

    def recv(self, size: int) -> bytes:
        """
        Read exactly 'size' bytes from the stream.

        This method buffers data from UDP packets and returns
        the requested amount, similar to TCP socket.recv().

        Args:
            size: Number of bytes to read

        Returns:
            Exactly 'size' bytes of data

        Raises:
            socket.timeout: If timeout occurs before reading enough data
            ConnectionError: If connection is closed
        """
        with self._lock:
            buffer_before = len(self._buffer)
            while len(self._buffer) < size:
                self._receive_packet()

            data = self._buffer[:size]
            self._buffer = self._buffer[size:]

            # Debug log for first few reads
            if self._packets_received <= 10:
                logger.debug(
                    f"UdpPacketReader.recv({size}): "
                    f"buffer_before={buffer_before}, buffer_after={len(self._buffer)}, "
                    f"returned={len(data)} bytes"
                )

            return data

    def recv_exact(self, size: int) -> bytes:
        """Alias for recv() - compatibility with demuxer interface."""
        return self.recv(size)

    def _receive_packet(self) -> None:
        """
        Receive one UDP packet and add payload to buffer.

        Called internally when buffer needs more data.

        Handles both normal packets and fragmented packets:
        - Normal: [UDP header: 16] [scrcpy payload: N]
        - Fragment: [UDP header: 16] [frag_idx: 4] [fragment data: N]

        For fragmented packets, reassembles all fragments before adding to buffer.
        """
        try:
            packet, addr = self._socket.recvfrom(self.MAX_UDP_PACKET)
            self._packets_received += 1
            self._last_source = addr

            if len(packet) == 0:
                self._empty_packets += 1
                logger.warning(f"Empty UDP packet from {addr}")
                return

            if self._strip_header:
                if len(packet) < self.HEADER_SIZE:
                    logger.warning(
                        f"UDP packet too short ({len(packet)} bytes) from {addr}"
                    )
                    return

                # Parse UDP header
                seq, ts, flags = struct.unpack('>IqI', packet[:self.HEADER_SIZE])
                self._last_header = UdpPacketHeader(seq, ts, flags)
                self._header_bytes_dropped += self.HEADER_SIZE

                # Check if fragmented (bit 31 of flags)
                is_fragmented = bool(flags & (1 << 31))

                if is_fragmented:
                    # Fragmented packet: [UDP header: 16] [frag_idx: 4] [fragment data]
                    if len(packet) < self.HEADER_SIZE + 4:
                        logger.warning(f"Fragmented packet too short: {len(packet)} bytes")
                        return

                    frag_idx = struct.unpack('>I', packet[self.HEADER_SIZE:self.HEADER_SIZE+4])[0]
                    fragment_data = packet[self.HEADER_SIZE+4:]

                    # Debug log for fragmented packets
                    if self._packets_received <= 10:
                        logger.debug(
                            f"UDP packet #{self._packets_received}: "
                            f"seq={seq}, ts={ts}, flags={flags:#x} (FRAGMENTED, idx={frag_idx}), "
                            f"fragment={len(fragment_data)} bytes"
                        )

                    # Reassemble fragments
                    reassembled = self._reassemble_fragment(ts, flags, frag_idx, fragment_data)
                    if reassembled is not None:
                        # Complete frame reassembled
                        self._buffer += reassembled
                        self._bytes_received += len(reassembled)
                        self._fragments_reassembled += 1
                        logger.debug(f"Reassembled complete frame: {len(reassembled)} bytes")
                else:
                    # Normal packet: strip header, keep payload
                    payload = packet[self.HEADER_SIZE:]

                    # Debug log for first few packets
                    if self._packets_received <= 10:
                        h = self._last_header
                        if h:
                            hex_preview = payload[:32].hex() if len(payload) >= 32 else payload.hex()
                            logger.debug(
                                f"UDP packet #{self._packets_received}: "
                                f"seq={h.sequence}, ts={h.timestamp}, "
                                f"flags={h.flags:#x} (key={h.is_key_frame}, cfg={h.is_config}), "
                                f"payload={len(payload)} bytes from {addr}"
                            )
                            logger.debug(f"  Payload hex (first 32 bytes): {hex_preview}")

                    self._buffer += payload
                    self._bytes_received += len(payload)
            else:
                # No header stripping
                payload = packet
                self._last_header = None
                self._buffer += payload
                self._bytes_received += len(payload)

            # Debug log buffer size
            if self._packets_received <= 10:
                logger.debug(f"  Buffer size after: {len(self._buffer)}")

        except socket.timeout:
            raise
        except Exception as e:
            logger.error(f"UDP receive error: {e}")
            raise

    def _reassemble_fragment(self, timestamp: int, flags: int, frag_idx: int, data: bytes) -> Optional[bytes]:
        """
        Reassemble fragmented packets.

        Args:
            timestamp: Packet timestamp (used as frame identifier)
            flags: Packet flags
            frag_idx: Fragment index (0-based)
            data: Fragment data

        Returns:
            Complete reassembled data when all fragments received, None otherwise
        """
        # Get or create fragment buffer for this timestamp
        if timestamp not in self._fragment_buffers:
            self._fragment_buffers[timestamp] = FragmentBuffer(
                timestamp=timestamp,
                flags=flags
            )

        frag_buf = self._fragment_buffers[timestamp]

        # Store fragment
        frag_buf.fragments[frag_idx] = data
        frag_buf.total_size += len(data)

        # For first fragment, extract expected size from scrcpy header
        if frag_idx == 0 and len(data) >= 12:
            # scrcpy header: [pts_flags: 8] [size: 4]
            _, expected_payload_size = struct.unpack('>QI', data[:12])
            frag_buf.expected_size = 12 + expected_payload_size
            logger.debug(f"First fragment: expected total size = {frag_buf.expected_size}")

        # Check if we have all fragments
        if frag_buf.expected_size > 0 and frag_buf.total_size >= frag_buf.expected_size:
            # Reassemble in order
            reassembled = b''
            for i in sorted(frag_buf.fragments.keys()):
                reassembled += frag_buf.fragments[i]

            # Clean up
            del self._fragment_buffers[timestamp]

            # Trim to expected size (in case of extra data)
            return reassembled[:frag_buf.expected_size]

        # Also check if we have consecutive fragments starting from 0
        # without knowing expected size (heuristic)
        if frag_buf.expected_size == 0:
            # Check if fragments are consecutive
            max_idx = max(frag_buf.fragments.keys())
            if all(i in frag_buf.fragments for i in range(max_idx + 1)):
                # Check if last fragment is small (likely the end)
                last_frag_size = len(frag_buf.fragments[max_idx])
                max_frag_size = 65507 - 16 - 4  # Max fragment data size
                if last_frag_size < max_frag_size:
                    # Likely complete - reassemble
                    reassembled = b''
                    for i in range(max_idx + 1):
                        reassembled += frag_buf.fragments[i]

                    # Clean up
                    del self._fragment_buffers[timestamp]
                    return reassembled

        return None

    def peek(self, size: int) -> bytes:
        """
        Peek at the next 'size' bytes without consuming them.

        Args:
            size: Number of bytes to peek

        Returns:
            Up to 'size' bytes from buffer
        """
        with self._lock:
            while len(self._buffer) < size:
                self._receive_packet()
            return self._buffer[:size]

    def get_stats(self) -> dict:
        """Get reader statistics."""
        with self._lock:
            return {
                'packets_received': self._packets_received,
                'bytes_received': self._bytes_received,
                'header_bytes_dropped': self._header_bytes_dropped,
                'empty_packets': self._empty_packets,
                'buffer_size': len(self._buffer),
                'last_source': self._last_source,
            }

    def get_last_header(self) -> Optional[UdpPacketHeader]:
        """Get the last parsed UDP header (for debugging)."""
        return self._last_header

    def close(self) -> None:
        """Close the underlying socket."""
        try:
            self._socket.close()
        except Exception:
            pass

    # Socket-like interface for compatibility
    def fileno(self) -> int:
        """Return socket file descriptor."""
        return self._socket.fileno()

    def settimeout(self, timeout: float) -> None:
        """Set socket timeout."""
        self._socket.settimeout(timeout)

    def gettimeout(self) -> Optional[float]:
        """Get socket timeout."""
        return self._socket.gettimeout()


class UdpFragmentReassembler:
    """
    Reassembles fragmented UDP packets.

    When a frame is larger than MAX_UDP_PACKET, the server fragments it.
    This class reassembles the fragments back into complete frames.

    Fragment format:
        [seq: 4] [ts: 8] [flags: 4 with FRAGMENTED bit] [frag_index: 4] [data]

    Note: This is a placeholder for future implementation.
    Current implementation assumes frames fit in single packets
    or uses the simple buffering in UdpPacketReader.
    """

    def __init__(self):
        self._fragments = {}  # sequence -> list of (index, data)
        self._expected_index = 0

    def add_fragment(self, header: UdpPacketHeader, data: bytes) -> Optional[bytes]:
        """
        Add a fragment and return complete frame if ready.

        Args:
            header: Parsed UDP header
            data: Fragment payload

        Returns:
            Complete frame data if all fragments received, None otherwise
        """
        # TODO: Implement proper fragment reassembly
        # For now, assume no fragmentation (frames < 65KB)
        if not header.is_fragmented:
            return data

        # Fragmented packet - would need to reassemble
        logger.warning(f"Fragmented UDP packet not yet supported: seq={header.sequence}")
        return None
