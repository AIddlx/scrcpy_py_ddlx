"""
UDP Video Demuxer - Specialized demuxer for UDP network mode.

This demuxer is designed specifically for UDP transport and provides:
1. Direct UDP packet handling (no TCP stream emulation)
2. UDP header parsing (seq, timestamp, flags)
3. Fragment reassembly
4. Packet loss detection
5. PLI (Picture Loss Indication) request generation
6. FEC decoding support (future)

Unlike StreamingVideoDemuxer which uses _recv_exact() for TCP streams,
this demuxer processes complete UDP packets directly.
"""

import logging
import random
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Callable, TYPE_CHECKING
from queue import Queue

from ..protocol import (
    CodecId,
    PACKET_HEADER_SIZE,
    PACKET_FLAG_CONFIG,
    PACKET_FLAG_KEY_FRAME,
    PACKET_PTS_MASK,
    UDP_HEADER_SIZE,
    UDP_FLAG_KEY_FRAME,
    UDP_FLAG_CONFIG,
    UDP_FLAG_FRAGMENTED,
    UDP_FLAG_FEC_DATA,
    UDP_FLAG_FEC_PARITY,
    DEFAULT_PLI_THRESHOLD,
    DEFAULT_PLI_COOLDOWN,
    ControlMessageType,
)
from ..stream import PacketHeader, VideoPacket

if TYPE_CHECKING:
    from .fec import FecDecoder

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class UdpPacketHeader:
    """Parsed UDP packet header."""
    sequence: int
    timestamp: int
    flags: int
    send_time_ns: int = 0  # Device send time in nanoseconds (for E2E latency)

    @property
    def is_key_frame(self) -> bool:
        return bool(self.flags & UDP_FLAG_KEY_FRAME)

    @property
    def is_config(self) -> bool:
        return bool(self.flags & UDP_FLAG_CONFIG)

    @property
    def is_fragmented(self) -> bool:
        return bool(self.flags & UDP_FLAG_FRAGMENTED)

    @property
    def is_fec_data(self) -> bool:
        return bool(self.flags & UDP_FLAG_FEC_DATA)

    @property
    def is_fec_parity(self) -> bool:
        return bool(self.flags & UDP_FLAG_FEC_PARITY)


@dataclass
class FragmentBuffer:
    """Buffer for reassembling fragmented UDP packets."""
    timestamp: int = 0
    flags: int = 0
    fragments: Dict[int, bytes] = field(default_factory=dict)
    expected_size: int = 0  # Expected total size from scrcpy header
    total_size: int = 0     # Total bytes received so far
    first_seq: int = 0      # First sequence number
    created_at: float = field(default_factory=time.time)


@dataclass
class UdpStats:
    """UDP statistics."""
    packets_received: int = 0
    bytes_received: int = 0
    packets_lost: int = 0
    packets_dropped: int = 0  # Dropped for catch-up during fast motion
    fragments_reassembled: int = 0
    pli_requests_sent: int = 0
    fec_recoveries: int = 0
    parse_errors: int = 0


# =============================================================================
# UdpVideoDemuxer
# =============================================================================

class UdpVideoDemuxer:
    """
    UDP-specialized video demuxer.

    Key differences from StreamingVideoDemuxer:
    1. Processes complete UDP packets (no _recv_exact emulation)
    2. Built-in packet loss detection via sequence numbers
    3. Automatic PLI request generation
    4. FEC decoding support (when enabled)
    5. Detailed statistics tracking

    Thread-safe operation with dedicated reader thread.
    """

    # Maximum UDP payload size
    MAX_UDP_PACKET = 65507

    # Maximum scrcpy packet size (prevent memory exhaustion)
    MAX_PACKET_SIZE = 16 * 1024 * 1024  # 16MB

    # Fragment timeout (seconds)
    FRAGMENT_TIMEOUT = 2.0

    # Maximum fragment groups to track
    MAX_FRAGMENT_GROUPS = 100

    # Disconnect detection: 1s timeout * 3 = 3s max wait
    MAX_CONSECUTIVE_TIMEOUTS = 3
    SOCKET_TIMEOUT = 1.0
    # Max time without data before disconnect (seconds)
    MAX_DATA_GAP = 3.0

    def __init__(
        self,
        udp_socket: socket.socket,
        packet_queue: Queue,
        codec_id: int,
        control_channel: Optional[object] = None,
        fec_decoder: Optional['FecDecoder'] = None,
        pli_enabled: bool = True,
        pli_threshold: int = DEFAULT_PLI_THRESHOLD,
        pli_cooldown: float = DEFAULT_PLI_COOLDOWN,
        stats_callback: Optional[Callable[[UdpStats], None]] = None,
        drop_rate: float = 0.0,
    ):
        """
        Initialize UDP video demuxer.

        Args:
            udp_socket: Bound UDP socket to read from
            packet_queue: Queue for VideoPacket objects
            codec_id: Video codec ID (H264/H265/AV1)
            control_channel: Control channel for PLI requests (must have send() method)
            fec_decoder: FEC decoder instance (optional)
            pli_enabled: Whether to send PLI requests on packet loss
            pli_threshold: Consecutive packet losses before PLI
            pli_cooldown: Minimum seconds between PLI requests
            stats_callback: Optional callback for statistics updates
            drop_rate: Simulated packet loss rate (0.0-1.0) for testing
        """
        self._socket = udp_socket
        self._socket.settimeout(self.SOCKET_TIMEOUT)  # Default timeout for disconnect detection
        self._packet_queue = packet_queue
        self._codec_id = codec_id
        self._control_channel = control_channel
        self._fec_decoder = fec_decoder
        self._stats_callback = stats_callback
        self._drop_rate = drop_rate

        # PLI configuration
        self._pli_enabled = pli_enabled
        self._pli_threshold = pli_threshold
        self._pli_cooldown = pli_cooldown
        self._last_pli_time: float = 0
        self._consecutive_drops: int = 0

        # Sequence tracking for loss detection
        self._expected_seq: int = 0
        self._seq_initialized: bool = False

        # PTS tracking for order detection
        self._last_pts: Optional[int] = None
        self._pts_discontinuity_count: int = 0

        # Fragment reassembly buffers
        self._fragment_buffers: Dict[int, FragmentBuffer] = {}

        # Track if we have incomplete key frame fragments (for PLI)
        self._pending_keyframe_ts: Optional[int] = None

        # Config packet buffer (for H.264/H.265 SPS/PPS merging)
        self._config_data: Optional[bytes] = None

        # Frame size change callback (for video header with new dimensions)
        self._frame_size_changed_callback: Optional[Callable[[int, int], None]] = None

        # Server disconnect detection
        self._consecutive_timeouts: int = 0
        self._last_packet_time: float = 0

        # Threading
        self._thread: Optional[threading.Thread] = None
        self._stopped = threading.Event()
        self._lock = threading.Lock()

        # Statistics
        self._stats = UdpStats()

        logger.info(
            f"UdpVideoDemuxer created: codec={codec_id}, "
            f"socket_timeout={self.SOCKET_TIMEOUT}s, max_timeouts={self.MAX_CONSECUTIVE_TIMEOUTS}, "
            f"disconnect_after={self.SOCKET_TIMEOUT * self.MAX_CONSECUTIVE_TIMEOUTS}s"
        )

    # -------------------------------------------------------------------------
    # Public Methods
    # -------------------------------------------------------------------------

    def set_frame_size_changed_callback(self, callback: Optional[Callable[[int, int], None]]) -> None:
        """
        Set callback to be notified when video dimensions change.

        This is called when a video header packet is received with new dimensions.

        Args:
            callback: Function that takes (width, height) arguments, or None to disable
        """
        self._frame_size_changed_callback = callback
        logger.debug(f"Frame size change callback set: {callback is not None}")

    def start(self) -> None:
        """Start the demuxer thread."""
        if self._thread is not None:
            logger.warning("UdpVideoDemuxer already started")
            return

        self._stopped.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=self._get_thread_name(),
            daemon=True
        )
        self._thread.start()
        logger.info(f"{self._get_thread_name()} started")

    def stop(self) -> None:
        """Stop the demuxer and wait for thread completion."""
        if self._thread is None:
            return

        logger.info(f"Stopping {self._get_thread_name()}...")
        self._stopped.set()

        # Close socket to interrupt blocking recv
        try:
            self._socket.close()
        except Exception as e:
            logger.debug(f"Error closing socket: {e}")

        # Wait for thread
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            logger.warning(f"{self._get_thread_name()} did not stop gracefully")

        self._thread = None
        logger.info(f"{self._get_thread_name()} stopped")

    def get_stats(self) -> UdpStats:
        """Get current statistics."""
        with self._lock:
            return UdpStats(
                packets_received=self._stats.packets_received,
                bytes_received=self._stats.bytes_received,
                packets_lost=self._stats.packets_lost,
                fragments_reassembled=self._stats.fragments_reassembled,
                pli_requests_sent=self._stats.pli_requests_sent,
                fec_recoveries=self._stats.fec_recoveries,
                parse_errors=self._stats.parse_errors,
            )

    def settimeout(self, timeout: float) -> None:
        """Set socket timeout."""
        self._socket.settimeout(timeout)

    # -------------------------------------------------------------------------
    # Main Loop
    # -------------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Main demuxer loop - process UDP packets."""
        logger.info(f"{self._get_thread_name()} loop started, watching for disconnect...")
        _gil_contention_count = 0
        _total_process_time = 0.0
        try:
            while not self._stopped.is_set():
                try:
                    # Receive UDP packet (releases GIL during I/O)
                    recv_start = time.perf_counter()
                    packet, addr = self._socket.recvfrom(self.MAX_UDP_PACKET)
                    recv_time = (time.perf_counter() - recv_start) * 1000

                    # CRITICAL: Record actual UDP receive time BEFORE any processing
                    # This is the TRUE start of the pipeline latency
                    actual_udp_recv_time = time.time()

                    # Reset timeout counter on successful receive
                    self._consecutive_timeouts = 0

                    if len(packet) == 0:
                        continue

                    # SIMULATE PACKET LOSS for testing
                    if self._drop_rate > 0 and random.random() < self._drop_rate:
                        self._simulated_drops = getattr(self, '_simulated_drops', 0) + 1
                        if self._simulated_drops <= 5 or self._simulated_drops % 50 == 0:
                            logger.info(f"[SIMULATE_DROP] Dropped packet #{self._simulated_drops} (rate={self._drop_rate:.1%})")
                        continue  # Skip processing this packet

                    # Check data gap (time since last packet)
                    now = time.time()
                    if self._last_packet_time > 0:
                        gap = now - self._last_packet_time
                        if gap > self.MAX_DATA_GAP:
                            logger.error(
                                f"Server disconnect detected: data gap {gap:.1f}s > {self.MAX_DATA_GAP}s"
                            )
                            # Update last packet time before breaking
                            self._last_packet_time = now
                            break

                    # Update last packet time
                    self._last_packet_time = now

                    # Process packet - measure GIL contention
                    process_start = time.perf_counter()
                    self._process_packet(packet, actual_udp_recv_time)
                    process_time = (time.perf_counter() - process_start) * 1000

                    # Track GIL contention (if processing takes > 5ms, something is blocking)
                    if process_time > 5.0:
                        _gil_contention_count += 1
                        _total_process_time += process_time
                        if _gil_contention_count % 50 == 0:
                            avg_time = _total_process_time / _gil_contention_count
                            logger.warning(f"[GIL_CONTENTION] UDP process delay: current={process_time:.1f}ms, avg={avg_time:.1f}ms, count={_gil_contention_count}")

                except socket.timeout:
                    # Check for server disconnect
                    self._consecutive_timeouts += 1
                    logger.debug(
                        f"Socket timeout #{self._consecutive_timeouts}/{self.MAX_CONSECUTIVE_TIMEOUTS}"
                    )
                    if self._consecutive_timeouts >= self.MAX_CONSECUTIVE_TIMEOUTS:
                        # Only treat as disconnect if we've received at least one packet
                        if self._last_packet_time > 0:
                            elapsed = time.time() - self._last_packet_time
                            logger.error(
                                f"Server disconnect detected: no video data for {elapsed:.1f}s "
                                f"({self._consecutive_timeouts} consecutive timeouts)"
                            )
                            break
                    continue
                except OSError as e:
                    if not self._stopped.is_set():
                        logger.error(f"Socket error: {e}")
                    break
                except Exception as e:
                    logger.error(f"Error processing packet: {e}", exc_info=True)

        finally:
            logger.info(f"{self._get_thread_name()} loop ended")

    def _process_packet(self, packet: bytes, actual_udp_recv_time: float = 0.0) -> None:
        """
        Process a single UDP packet.

        Packet format:
        - Normal: [UDP Header: 16B] [Scrcpy Header: 12B] [Payload: NB]
        - Fragment: [UDP Header: 16B] [frag_idx: 4B] [Fragment: NB]

        Args:
            packet: Raw UDP packet data
            actual_udp_recv_time: Time when packet was received from socket (time.time())
        """
        if len(packet) < UDP_HEADER_SIZE:
            logger.warning(f"Packet too short: {len(packet)} bytes")
            return

        # Parse UDP header
        udp_header = self._parse_udp_header(packet[:UDP_HEADER_SIZE])
        payload = packet[UDP_HEADER_SIZE:]

        # Update stats
        with self._lock:
            self._stats.packets_received += 1
            self._stats.bytes_received += len(packet)

        # Initialize expected sequence on first packet
        if not self._seq_initialized:
            self._expected_seq = udp_header.sequence
            self._seq_initialized = True
            logger.info(f"Sequence initialized: starting from seq={udp_header.sequence}")
        else:
            # Only detect loss for non-parity packets
            # Parity packets are sent after K data frames, so seq jump is expected
            if not udp_header.is_fec_parity:
                self._detect_loss(udp_header.sequence)

        # Debug: Log every packet for first 100 packets (extended for analysis)
        if self._stats.packets_received <= 100:
            # Check if this packet was expected to be parity (seq gap)
            is_expected_parity = (udp_header.sequence > self._expected_seq + 1) if self._seq_initialized else False

            # Calculate E2E latency from device send time (if available)
            e2e_device_ms = 0.0
            if udp_header.send_time_ns > 0 and actual_udp_recv_time > 0:
                # Note: This assumes device and PC clocks are roughly synchronized
                # Convert device nanoseconds to seconds and compare with PC time
                recv_time_ns = actual_udp_recv_time * 1e9
                e2e_device_ms = (recv_time_ns - udp_header.send_time_ns) / 1e6

            logger.debug(
                f"[UDP] #{self._stats.packets_received}: seq={udp_header.sequence}, "
                f"ts={udp_header.timestamp}, flags={udp_header.flags:#x}, "
                f"config={udp_header.is_config}, key={udp_header.is_key_frame}, "
                f"fec_data={udp_header.is_fec_data}, fec_parity={udp_header.is_fec_parity}, "
                f"frag={udp_header.is_fragmented}, expected_parity={is_expected_parity}, "
                f"send_ns={udp_header.send_time_ns}, e2e_device={e2e_device_ms:.1f}ms, "
                f"payload={len(payload)} bytes"
            )

        # Dispatch by packet type
        # IMPORTANT: CONFIG packets have highest priority - they must be processed
        # even if other flags (like FEC_DATA) are accidentally set
        if udp_header.is_config:
            # Config packet - handle directly, bypass FEC
            self._handle_normal_packet(udp_header, payload, actual_udp_recv_time)
        elif udp_header.is_fec_parity and udp_header.is_fragmented:
            # FEC parity packet that is fragmented (large parity > 65KB)
            self._handle_fec_parity_fragment(udp_header, payload)
        elif udp_header.is_fec_parity:
            logger.info(f"[FEC-PARITY] Received parity packet: seq={udp_header.sequence}, group in payload")
            self._handle_fec_parity(udp_header, payload)
        elif udp_header.is_fec_data and udp_header.is_fragmented:
            # FEC data packet that is part of a fragmented frame
            self._handle_fec_fragment(udp_header, payload, actual_udp_recv_time)
        elif udp_header.is_fec_data:
            self._handle_fec_data(udp_header, payload)
        elif udp_header.is_fragmented:
            self._handle_fragment(udp_header, payload, actual_udp_recv_time)
        else:
            self._handle_normal_packet(udp_header, payload, actual_udp_recv_time)

        # Update expected sequence (always, including for parity packets)
        self._expected_seq = udp_header.sequence + 1

        # Check for FEC group failures and trigger PLI if needed
        self._check_fec_failures()

    # -------------------------------------------------------------------------
    # Packet Handlers
    # -------------------------------------------------------------------------

    def _handle_normal_packet(self, udp_header: UdpPacketHeader, payload: bytes, actual_udp_recv_time: float = 0.0) -> None:
        """Handle normal (non-fragmented, non-FEC) packet."""
        video_packet = self._parse_scrcpy_packet(payload)
        if video_packet:
            # Latency tracking: record ACTUAL UDP receive time (from socket recv)
            # Also pass PTS for frame verification
            try:
                from scrcpy_py_ddlx.latency_tracker import get_tracker
                video_packet.packet_id = get_tracker().start_packet_with_time(actual_udp_recv_time, video_packet.header.pts)
            except Exception:
                pass
            # Pass device send time for full E2E latency tracking
            video_packet.send_time_ns = udp_header.send_time_ns
            self._queue_packet(video_packet)

    def _handle_fragment(self, udp_header: UdpPacketHeader, payload: bytes, actual_udp_recv_time: float = 0.0) -> None:
        """
        Handle fragmented packet.

        Fragment format: [frag_idx: 4B] [fragment_data: NB]
        """
        if len(payload) < 4:
            logger.warning(f"Fragment too short: {len(payload)} bytes")
            return

        frag_idx = struct.unpack('>I', payload[:4])[0]
        frag_data = payload[4:]

        # Check if this is a key frame fragment
        is_key_frame_fragment = udp_header.is_key_frame

        # Start latency tracking for first fragment (will be passed to reassembled packet)
        # Use the ACTUAL UDP receive time from socket
        if frag_idx == 0:
            try:
                from scrcpy_py_ddlx.latency_tracker import get_tracker
                self._current_fragment_packet_id = get_tracker().start_packet_with_time(actual_udp_recv_time)
            except Exception:
                self._current_fragment_packet_id = -1

        # DEBUG: Log every fragment for analysis
        logger.debug(
            f"[FRAG] ts={udp_header.timestamp}, seq={udp_header.sequence}, "
            f"frag_idx={frag_idx}, frag_size={len(frag_data)}, is_key={is_key_frame_fragment}"
        )

        # Track pending key frame for PLI
        if is_key_frame_fragment and frag_idx == 0:
            self._pending_keyframe_ts = udp_header.timestamp
            logger.debug(f"Key frame fragment #0 received (ts={udp_header.timestamp}), waiting for more fragments")

        # Reassemble
        reassembled = self._reassemble_fragment(udp_header, frag_idx, frag_data)
        if reassembled is not None:
            # Clear pending key frame if this was it
            if udp_header.timestamp == self._pending_keyframe_ts:
                self._pending_keyframe_ts = None
                logger.debug(f"Key frame reassembly complete (ts={udp_header.timestamp})")

            video_packet = self._parse_scrcpy_packet(reassembled)
            if video_packet:
                # Use packet_id from first fragment for latency tracking
                packet_id = getattr(self, '_current_fragment_packet_id', -1)
                video_packet.packet_id = packet_id
                # Pass device send time for full E2E latency tracking
                video_packet.send_time_ns = udp_header.send_time_ns
                self._queue_packet(video_packet)

            # CRITICAL: Register with FEC decoder if this was a FEC fragment
            # Check if we have FEC metadata for this timestamp
            fec_meta = getattr(self, '_fec_fragment_metadata', {}).get(udp_header.timestamp)
            if fec_meta and self._fec_decoder is not None and fec_meta['total_data'] > 0:
                self._fec_decoder.add_data_packet(
                    group_id=fec_meta['group_id'],
                    packet_idx=fec_meta['packet_idx'],
                    total_data=fec_meta['total_data'],
                    total_parity=fec_meta['total_parity'],
                    data=reassembled,  # Use reassembled data (complete scrcpy packet)
                    original_size=fec_meta['original_size'] if fec_meta['original_size'] > 0 else len(reassembled),
                )
                logger.debug(
                    f"[FEC-FRAG-REGISTER] Registered reassembled fragment: group={fec_meta['group_id']}, "
                    f"idx={fec_meta['packet_idx']}/{fec_meta['total_data']}"
                )
                # Clean up metadata
                del self._fec_fragment_metadata[udp_header.timestamp]

    def _handle_fec_data(self, udp_header: UdpPacketHeader, payload: bytes) -> None:
        """
        Handle FEC data packet.

        FEC Data Packet Format:
        [FEC Header: 7B] [Scrcpy Payload: NB]

        FEC Header (7 bytes):
          group_id: 2B (uint16, big-endian)
          frame_idx: 1B (uint8) - frame index in group (0 to K-1) for frame-level FEC
          total_frames: 1B (uint8) - K
          total_parity: 1B (uint8) - M
          original_size: 2B (uint16, big-endian) - original payload size for recovery

        For frame-level FEC: process data directly, use FEC only for recovery.
        """
        if len(payload) < 7:
            logger.warning(f"FEC data packet too short: {len(payload)} bytes")
            return

        # Parse FEC header (7 bytes)
        group_id = struct.unpack('>H', payload[0:2])[0]
        frame_idx = payload[2]
        total_frames = payload[3]
        total_parity = payload[4]
        original_size = struct.unpack('>H', payload[5:7])[0]
        scrcpy_data = payload[7:]

        # DEBUG: Log FEC data with UDP timestamp for correlation
        logger.debug(
            f"[FEC-DATA] group={group_id}, frame_idx={frame_idx}/{total_frames}, "
            f"udp_ts={udp_header.timestamp}, udp_seq={udp_header.sequence}, "
            f"payload={len(scrcpy_data)} bytes"
        )

        # Frame-level FEC: process data directly, don't wait for group completion
        # This ensures video plays normally while FEC is available for recovery
        video_packet = self._parse_scrcpy_packet(scrcpy_data)
        if video_packet:
            # Set up latency tracking for this packet
            try:
                from scrcpy_py_ddlx.latency_tracker import get_tracker
                video_packet.packet_id = get_tracker().start_packet_with_time(
                    time.time(), video_packet.header.pts
                )
            except Exception:
                video_packet.packet_id = -1
            # Pass device send time for full E2E latency tracking
            video_packet.send_time_ns = udp_header.send_time_ns
            self._queue_packet(video_packet)

            # CRITICAL FIX: Register data packet with FEC decoder
            # Without this, FEC decoder doesn't know which packets were received,
            # making recovery impossible when packets are lost.
            if self._fec_decoder is not None and total_frames > 0:
                # Register with complete FEC header including scrcpy data
                # The data includes the 12-byte scrcpy header for proper recovery
                self._fec_decoder.add_data_packet(
                    group_id=group_id,
                    packet_idx=frame_idx,
                    total_data=total_frames,
                    total_parity=total_parity,
                    data=scrcpy_data,
                    original_size=original_size if original_size > 0 else len(scrcpy_data),
                )
                logger.debug(
                    f"[FEC-REGISTER] Registered data packet: group={group_id}, "
                    f"idx={frame_idx}/{total_frames}"
                )

    def _handle_fec_parity_fragment(self, udp_header: UdpPacketHeader, payload: bytes) -> None:
        """
        Handle fragmented FEC parity packet.

        Large parity packets (>65KB) are fragmented like data packets.

        Format: [frag_idx: 4B] [FEC Header: 5B] [Parity Data: NB]
        """
        if len(payload) < 4:
            logger.warning(f"FEC parity fragment too short: {len(payload)} bytes")
            return

        frag_idx = struct.unpack('>I', payload[:4])[0]
        frag_data = payload[4:]

        # Use timestamp as key for reassembly
        ts = udp_header.timestamp

        # Initialize parity fragment buffer if needed
        if not hasattr(self, '_parity_fragment_buffers'):
            self._parity_fragment_buffers: Dict[int, Dict[int, bytes]] = {}

        if ts not in self._parity_fragment_buffers:
            self._parity_fragment_buffers[ts] = {}

        self._parity_fragment_buffers[ts][frag_idx] = frag_data

        logger.debug(
            f"[FEC-PARITY-FRAG] ts={ts}, seq={udp_header.sequence}, "
            f"frag_idx={frag_idx}, frag_size={len(frag_data)}, "
            f"total_frags={len(self._parity_fragment_buffers[ts])}"
        )

        # Check if we have consecutive fragments starting from 0
        # We don't know total count, so check if we have 0,1,2,... without gaps
        buffer = self._parity_fragment_buffers[ts]
        max_idx = max(buffer.keys()) if buffer else -1

        # Simple heuristic: if we received fragment 0 with FEC header, we can check total
        if 0 in buffer and len(buffer[0]) >= 5:
            # First fragment has FEC header - we can determine if reassembly is complete
            # by checking if we have all consecutive fragments
            complete = True
            for i in range(max_idx + 1):
                if i not in buffer:
                    complete = False
                    break

            if complete:
                # Reassemble
                reassembled = b''.join(buffer[i] for i in range(max_idx + 1))
                logger.info(
                    f"[FEC-PARITY] Reassembled {max_idx + 1} fragments, "
                    f"total size={len(reassembled)} bytes"
                )
                # Clean up buffer
                del self._parity_fragment_buffers[ts]
                # Process as normal parity packet
                self._handle_fec_parity(udp_header, reassembled)

        # Cleanup old buffers (keep only last 10)
        if len(self._parity_fragment_buffers) > 10:
            oldest = sorted(self._parity_fragment_buffers.keys())[:-10]
            for old_ts in oldest:
                del self._parity_fragment_buffers[old_ts]

    def _handle_fec_parity(self, udp_header: UdpPacketHeader, payload: bytes) -> None:
        """
        Handle FEC parity packet.

        FEC Parity Packet Format:
        [FEC Header: 5B] [Parity Data: NB]

        FEC Header:
          group_id: 2B (uint16, big-endian)
          parity_idx: 1B (uint8)
          total_data: 1B (uint8) - K (for recovery reference)
          total_parity: 1B (uint8) - M
        """
        if self._fec_decoder is None:
            logger.debug(f"FEC parity packet ignored: FEC disabled")
            return

        if len(payload) < 5:
            logger.warning(f"FEC parity packet too short: {len(payload)} bytes")
            return

        # Parse FEC header (5 bytes)
        group_id = struct.unpack('>H', payload[0:2])[0]
        parity_idx = payload[2]
        total_data = payload[3]
        total_parity = payload[4]
        parity_data = payload[5:]

        logger.debug(
            f"FEC parity: group={group_id}, idx={parity_idx}, "
            f"total_data={total_data}, total_parity={total_parity}, payload={len(parity_data)} bytes"
        )

        # Add to FEC decoder and try recovery
        result = self._fec_decoder.add_parity_packet(
            group_id=group_id,
            parity_idx=parity_idx,
            total_data=total_data,
            total_parity=total_parity,
            parity_data=parity_data,
        )

        # If complete or recovery succeeded, process packets
        if result:
            logger.debug(f"FEC group {group_id} processed: {len(result)} packets")
            self._process_fec_group(result)

    def _handle_fec_fragment(self, udp_header: UdpPacketHeader, payload: bytes, actual_udp_recv_time: float = 0.0) -> None:
        """
        Handle FEC data packet that is part of a fragmented frame.

        Format: [frag_idx: 4B] [FEC Header: 7B] [Data: NB]

        When the frame is reassembled, it will be registered with the FEC decoder.
        """
        if len(payload) < 11:  # 4 (frag_idx) + 7 (FEC header)
            logger.warning(f"FEC fragment too short: {len(payload)} bytes")
            return

        # Parse fragment index
        frag_idx = struct.unpack('>I', payload[0:4])[0]

        # Parse FEC header (7 bytes)
        group_id = struct.unpack('>H', payload[4:6])[0]
        packet_idx = payload[6]
        total_data = payload[7]
        total_parity = payload[8]
        original_size = struct.unpack('>H', payload[9:11])[0]
        fragment_data = payload[11:]

        logger.debug(
            f"FEC fragment: group={group_id}, idx={packet_idx}/{total_data}, "
            f"frag_idx={frag_idx}, payload={len(fragment_data)} bytes"
        )

        # Store FEC metadata for this frame (keyed by timestamp)
        # Will be used when reassembly is complete
        if not hasattr(self, '_fec_fragment_metadata'):
            self._fec_fragment_metadata: Dict[int, dict] = {}

        self._fec_fragment_metadata[udp_header.timestamp] = {
            'group_id': group_id,
            'packet_idx': packet_idx,
            'total_data': total_data,
            'total_parity': total_parity,
            'original_size': original_size,
        }

        # Reconstruct fragment payload: [frag_idx] + [data without FEC header]
        reconstructed = struct.pack('>I', frag_idx) + fragment_data

        # Use normal fragment handling with actual UDP recv time
        self._handle_fragment(udp_header, reconstructed, actual_udp_recv_time)

    def _process_fec_group(self, packets: list) -> None:
        """Process a complete FEC group of packets."""
        # Update FEC recovery stats
        if self._fec_decoder:
            fec_stats = self._fec_decoder.get_stats()
            with self._lock:
                self._stats.fec_recoveries = fec_stats.get('packets_recovered', 0)

        logger.debug(f"[FEC-PROCESS] Processing FEC group: {len(packets)} packets")

        # Collect pts values to check ordering
        pts_list = []

        for i, pkt_data in enumerate(packets):
            # Debug: log packet size and first few bytes
            if len(pkt_data) >= 12:
                pts_flags, payload_size = struct.unpack('>QI', pkt_data[:12])
                is_config = bool(pts_flags & PACKET_FLAG_CONFIG)
                is_key = bool(pts_flags & PACKET_FLAG_KEY_FRAME)
                # Extract pts (lower 62 bits)
                pts = pts_flags & 0x3FFFFFFFFFFFFFFF
                pts_list.append(pts)

                logger.debug(
                    f"[FEC-PKT] idx={i}, len={len(pkt_data)}, payload_size={payload_size}, "
                    f"pts={pts}, is_config={is_config}, is_key={is_key}"
                )

            video_packet = self._parse_scrcpy_packet(pkt_data)
            if video_packet:
                self._queue_packet(video_packet)
            else:
                logger.warning(f"[FEC-PKT] idx={i} parse failed, len={len(pkt_data)}")

        # Check if pts are in order
        if len(pts_list) > 1:
            if pts_list != sorted(pts_list):
                logger.warning(
                    f"[FEC-ORDER] PTS not in order! pts_list={pts_list}"
                )

    # -------------------------------------------------------------------------
    # Parsing
    # -------------------------------------------------------------------------

    def _parse_udp_header(self, data: bytes) -> UdpPacketHeader:
        """Parse 24-byte UDP header."""
        if len(data) >= 24:
            # New format: [seq: 4B] [timestamp: 8B] [flags: 4B] [send_time_ns: 8B]
            seq, ts, flags, send_ns = struct.unpack('>IqIq', data[:24])
            return UdpPacketHeader(sequence=seq, timestamp=ts, flags=flags, send_time_ns=send_ns)
        else:
            # Fallback for old format (16 bytes)
            seq, ts, flags = struct.unpack('>IqI', data[:16])
            return UdpPacketHeader(sequence=seq, timestamp=ts, flags=flags, send_time_ns=0)

    def _parse_scrcpy_packet(self, data: bytes) -> Optional[VideoPacket]:
        """
        Parse scrcpy packet (12-byte header + payload).

        Returns VideoPacket or None if parsing fails.

        Note: After FEC recovery, data may be longer than expected (padded with zeros).
        We truncate to the correct size based on payload_size in the header.
        """
        if len(data) < PACKET_HEADER_SIZE:
            logger.warning(f"Scrcpy packet too short: {len(data)} bytes")
            with self._lock:
                self._stats.parse_errors += 1
            return None

        # Parse header
        pts_flags, payload_size = struct.unpack('>QI', data[:PACKET_HEADER_SIZE])

        # Extract flags and PTS
        is_config = bool(pts_flags & PACKET_FLAG_CONFIG)
        is_key_frame = bool(pts_flags & PACKET_FLAG_KEY_FRAME)
        pts = pts_flags & PACKET_PTS_MASK

        # FALLBACK: If pts=0 and packet is small but not empty, treat as config packet
        # This handles cases where server doesn't set CONFIG flag correctly
        # IMPORTANT: payload_size must be > 0 to avoid treating empty packets as config
        if not is_config and pts == 0 and 0 < payload_size < 100:
            is_config = True
            logger.debug(f"Fallback: treating pts=0 small packet as CONFIG (size={payload_size})")

        # Validate size
        if payload_size > self.MAX_PACKET_SIZE:
            logger.error(f"Payload size {payload_size} exceeds maximum {self.MAX_PACKET_SIZE}")
            with self._lock:
                self._stats.parse_errors += 1
            return None

        # Check if we have complete payload
        expected_size = PACKET_HEADER_SIZE + payload_size
        if len(data) < expected_size:
            logger.warning(f"Incomplete packet: expected {expected_size}, got {len(data)}")
            with self._lock:
                self._stats.parse_errors += 1
            return None

        # FEC recovery may produce data longer than expected (padded with zeros)
        # Truncate to the correct size based on payload_size in header
        if len(data) > expected_size:
            logger.debug(
                f"FEC recovered packet truncated: {len(data)} -> {expected_size} bytes "
                f"(payload_size={payload_size})"
            )
            data = data[:expected_size]

        # Extract payload
        payload = data[PACKET_HEADER_SIZE:expected_size]

        # Create header
        header = PacketHeader(
            pts_flags=pts_flags,
            pts=pts,
            size=payload_size,
            is_config=is_config,
            is_key_frame=is_key_frame,
            is_screenshot=False,
        )

        # Create packet
        packet = VideoPacket(
            header=header,
            data=payload,
            codec_id=self._codec_id,
        )

        # Merge config for H.264/H.265
        if self._codec_id in (CodecId.H264, CodecId.H265):
            packet = self._merge_config(packet)

        return packet

    # -------------------------------------------------------------------------
    # Fragment Reassembly
    # -------------------------------------------------------------------------

    def _reassemble_fragment(
        self,
        udp_header: UdpPacketHeader,
        frag_idx: int,
        frag_data: bytes
    ) -> Optional[bytes]:
        """
        Reassemble fragmented packet.

        Uses timestamp as frame identifier (all fragments of same frame share timestamp).

        Returns complete frame data or None if more fragments needed.
        """
        ts = udp_header.timestamp

        # Get or create buffer
        if ts not in self._fragment_buffers:
            # Cleanup old buffers
            self._cleanup_fragment_buffers()
            self._fragment_buffers[ts] = FragmentBuffer(
                timestamp=ts,
                flags=udp_header.flags,
                first_seq=udp_header.sequence,
            )

        buf = self._fragment_buffers[ts]

        # DEBUG: Check for duplicate fragment
        is_duplicate = frag_idx in buf.fragments
        if is_duplicate:
            logger.warning(
                f"[FRAG-DUP] Duplicate fragment detected! ts={ts}, frag_idx={frag_idx}, "
                f"old_size={len(buf.fragments[frag_idx])}, new_size={len(frag_data)}"
            )

        # Store fragment
        buf.fragments[frag_idx] = frag_data
        buf.total_size += len(frag_data)

        # Extract expected size from first fragment (contains scrcpy header)
        if frag_idx == 0 and len(frag_data) >= PACKET_HEADER_SIZE:
            _, expected_payload = struct.unpack('>QI', frag_data[:PACKET_HEADER_SIZE])
            buf.expected_size = PACKET_HEADER_SIZE + expected_payload

        # DEBUG: Log current state
        logger.debug(
            f"[FRAG-STATE] ts={ts}, frag_idx={frag_idx}, "
            f"fragments={len(buf.fragments)}, total_size={buf.total_size}, "
            f"expected_size={buf.expected_size}, is_dup={is_duplicate}"
        )

        # FAST FRAGMENT GAP DETECTION
        # If we receive a fragment with index > 0 but haven't received fragment 0,
        # or if there's a gap in fragment indices, we may have lost fragments
        if len(buf.fragments) > 1 or frag_idx > 0:
            max_idx = max(buf.fragments.keys())
            expected_count = max_idx + 1
            actual_count = len(buf.fragments)

            if actual_count < expected_count:
                # Find missing fragment indices
                missing = [i for i in range(expected_count) if i not in buf.fragments]

                # If fragment 0 is missing, we can't parse the scrcpy header
                # This is critical - we need to request a new keyframe
                if 0 in missing:
                    logger.warning(
                        f"[FRAG-GAP] Missing fragment 0 for ts={ts}! "
                        f"max_idx={max_idx}, missing={missing}, is_key={bool(buf.flags & UDP_FLAG_KEY_FRAME)}"
                    )
                    # For key frames, immediately request PLI and abandon this frame
                    if buf.flags & UDP_FLAG_KEY_FRAME and self._pli_enabled:
                        logger.warning(
                            f"[FRAG-GAP] Key frame fragment 0 lost, sending PLI immediately"
                        )
                        self._send_pli()
                        # Clear this buffer - we'll get a new keyframe
                        del self._fragment_buffers[ts]
                        if ts == self._pending_keyframe_ts:
                            self._pending_keyframe_ts = None
                        return None

                # If we're missing any fragment of a key frame, and we have most fragments,
                # it's likely we won't get them (network loss). Be aggressive with PLI.
                if (buf.flags & UDP_FLAG_KEY_FRAME) and len(missing) <= 2 and self._pli_enabled:
                    # Only send PLI if we've received a fragment after the gap
                    # (indicates the missing fragment was truly lost, not just delayed)
                    if frag_idx > min(missing):
                        logger.warning(
                            f"[FRAG-GAP] Key frame fragment(s) {missing} likely lost "
                            f"(received frag_idx={frag_idx} after gap), sending PLI"
                        )
                        self._send_pli()
                        # Clear this buffer - we'll get a new keyframe
                        del self._fragment_buffers[ts]
                        if ts == self._pending_keyframe_ts:
                            self._pending_keyframe_ts = None
                        return None

        # SIZE-BASED FAST DETECTION
        # If we know the expected size and we're close to it but have fragment gaps,
        # we may never complete this frame
        if buf.expected_size > 0 and buf.total_size >= buf.expected_size * 0.9:
            max_idx = max(buf.fragments.keys()) if buf.fragments else 0
            expected_frag_count = max_idx + 1
            actual_frag_count = len(buf.fragments)

            if actual_frag_count < expected_frag_count:
                missing = [i for i in range(expected_frag_count) if i not in buf.fragments]
                logger.warning(
                    f"[FRAG-SIZE] ts={ts} reached {buf.total_size}/{buf.expected_size} bytes "
                    f"but missing fragments: {missing}"
                )

                # For key frames, be aggressive - send PLI and abandon
                if (buf.flags & UDP_FLAG_KEY_FRAME) and self._pli_enabled:
                    logger.warning(
                        f"[FRAG-SIZE] Key frame incomplete but size threshold reached, sending PLI"
                    )
                    self._send_pli()
                    del self._fragment_buffers[ts]
                    if ts == self._pending_keyframe_ts:
                        self._pending_keyframe_ts = None
                    return None

        # Check if complete
        if buf.expected_size > 0 and buf.total_size >= buf.expected_size:
            # Calculate expected fragment count
            max_frag_idx = max(buf.fragments.keys())
            expected_frag_count = max_frag_idx + 1
            actual_frag_count = len(buf.fragments)

            # DEBUG: Verify fragment integrity
            if actual_frag_count < expected_frag_count:
                missing_frags = [i for i in range(expected_frag_count) if i not in buf.fragments]
                logger.warning(
                    f"[FRAG-INCOMPLETE] Fragment gaps detected! ts={ts}, "
                    f"expected={expected_frag_count} frags, got={actual_frag_count}, "
                    f"missing={missing_frags}, but total_size({buf.total_size}) >= expected({buf.expected_size})"
                )

            # Reassemble in order
            reassembled = b''
            for i in sorted(buf.fragments.keys()):
                reassembled += buf.fragments[i]

            # Cleanup
            del self._fragment_buffers[ts]

            # Update stats
            with self._lock:
                self._stats.fragments_reassembled += 1

            # DEBUG: Log reassembly result
            logger.info(
                f"[FRAG-DONE] ts={ts}, reassembled={len(reassembled)} bytes, "
                f"frags={actual_frag_count}/{expected_frag_count}, "
                f"expected_size={buf.expected_size}, is_key={bool(buf.flags & UDP_FLAG_KEY_FRAME)}"
            )

            return reassembled[:buf.expected_size]

        return None

    def _cleanup_fragment_buffers(self) -> None:
        """Remove expired fragment buffers."""
        now = time.time()
        expired = [
            ts for ts, buf in self._fragment_buffers.items()
            if now - buf.created_at > self.FRAGMENT_TIMEOUT
        ]
        for ts in expired:
            buf = self._fragment_buffers[ts]
            logger.warning(
                f"Fragment buffer expired: ts={ts}, "
                f"fragments={len(buf.fragments)}, flags={buf.flags:#x}"
            )

            # Check if this was a key frame that failed to reassemble
            if buf.flags & UDP_FLAG_KEY_FRAME:
                logger.warning("Key frame fragment timeout! Sending PLI request")
                if self._pli_enabled:
                    self._send_pli()

            # Clear pending key frame tracking
            if ts == self._pending_keyframe_ts:
                self._pending_keyframe_ts = None

            del self._fragment_buffers[ts]

        # Also limit total count
        if len(self._fragment_buffers) > self.MAX_FRAGMENT_GROUPS:
            # Remove oldest
            oldest = sorted(self._fragment_buffers.keys())[:len(self._fragment_buffers) - self.MAX_FRAGMENT_GROUPS]
            for ts in oldest:
                del self._fragment_buffers[ts]

    # -------------------------------------------------------------------------
    # Config Merging (H.264/H.265)
    # -------------------------------------------------------------------------

    def _is_video_header(self, data: bytes) -> bool:
        """
        Check if config packet is a video header (codec_id + width + height).

        Video Header: 12 bytes, starts with codec_id (0x68323634=h264, 0x68323635=h265)
        SPS/PPS: starts with Annex B start code (0x00 0x00 0x00 0x01 or 0x00 0x00 0x01)

        Args:
            data: Config packet payload

        Returns:
            True if this is a video header, False if it's SPS/PPS
        """
        if len(data) != 12:
            return False

        # Check for Annex B start code (SPS/PPS always starts with this)
        if data[0:4] == b'\x00\x00\x00\x01' or data[0:3] == b'\x00\x00\x01':
            return False

        # Check for valid codec_id (H.264 or H.265)
        import struct
        codec_id = struct.unpack('>I', data[0:4])[0]
        # H.264 codec_id in scrcpy is 0x68323634 ("h264" in ASCII)
        # H.265 codec_id in scrcpy is 0x68323635 ("h265" in ASCII)
        if codec_id in (0x68323634, 0x68323635):
            return True

        return False

    def _parse_video_header(self, data: bytes) -> tuple:
        """
        Parse video header to extract codec_id, width, height.

        Args:
            data: 12-byte video header payload

        Returns:
            Tuple of (codec_id, width, height)
        """
        import struct
        codec_id = struct.unpack('>I', data[0:4])[0]
        width = struct.unpack('>I', data[4:8])[0]
        height = struct.unpack('>I', data[8:12])[0]
        return codec_id, width, height

    def _merge_config(self, packet: VideoPacket) -> Optional[VideoPacket]:
        """
        Merge config packet with media packet for H.264/H.265.

        Config packets (SPS/PPS) must be prepended to key frames for proper decoding.

        IMPORTANT: For H.264/H.265, CONFIG data should ONLY be merged with KEY FRAMES
        if the key frame does not already contain SPS/PPS data.

        Strategy:
        1. CONFIG packet -> check if video header or SPS/PPS
           - Video header: extract new dimensions, notify callback
           - SPS/PPS: store it for merging with key frames
        2. Key frame -> check if it starts with SPS/PPS, if not, prepend stored CONFIG
        3. Non-key frame -> return as-is (CONFIG data not needed)

        H.264 NAL unit types:
        - 7 (0x07): SPS
        - 8 (0x08): PPS
        - 5 (0x05): IDR frame (key frame)
        """
        if packet.header.is_config:
            # Check if this is a video header (codec_id + width + height)
            if self._is_video_header(packet.data):
                codec_id, width, height = self._parse_video_header(packet.data)
                logger.info(f"[VIDEO_HEADER] codec=0x{codec_id:08x}, size={width}x{height}")

                # Check if this is a resolution change (screen rotation)
                old_size = getattr(self, '_last_video_size', None)
                if old_size is not None and old_size != (width, height):
                    logger.info(f"[VIDEO_HEADER] Resolution changed: {old_size} -> {width}x{height}")
                    # Clear buffers on resolution change (screen rotation)
                    self._clear_buffers_on_config_change()

                self._last_video_size = (width, height)

                # Notify frame size change callback if dimensions changed
                if self._frame_size_changed_callback:
                    try:
                        self._frame_size_changed_callback(width, height)
                        logger.info(f"[VIDEO_HEADER] Notified callback of new size: {width}x{height}")
                    except Exception as e:
                        logger.warning(f"[VIDEO_HEADER] Callback error: {e}")

                # Don't pass video header to decoder (it's not SPS/PPS)
                return None

            # Store SPS/PPS config for next key frame
            old_config = self._config_data
            self._config_data = packet.data
            if old_config is not None and old_config != packet.data:
                logger.info(f"[CONFIG_MERGE] Config changed: old={len(old_config)} bytes, new={len(packet.data)} bytes")

                # CRITICAL: Clear old buffers on config change (screen rotation)
                # Old data is invalid for new resolution/orientation
                self._clear_buffers_on_config_change()

            logger.debug(f"[CONFIG_MERGE] Stored config: {len(packet.data)} bytes")
            return packet  # Return CONFIG packet so decoder can set extradata

        # Only merge CONFIG with key frames
        if packet.header.is_key_frame and self._config_data is not None:
            # Check if key frame already starts with SPS/PPS (NAL type 7 or 8)
            # H.264 NAL header: 0x00 0x00 0x00 0x01 [nal_type_byte]
            data = packet.data
            has_sps_pps = False
            first_nal_type = -1

            if len(data) >= 5:
                # Look for start code (4-byte or 3-byte)
                if data[0:4] == b'\x00\x00\x00\x01':
                    first_nal_type = data[4] & 0x1F  # NAL type is lower 5 bits
                    if first_nal_type in (7, 8):  # SPS or PPS
                        has_sps_pps = True
                elif data[0:3] == b'\x00\x00\x01':
                    first_nal_type = data[3] & 0x1F
                    if first_nal_type in (7, 8):
                        has_sps_pps = True

            logger.info(f"[CONFIG_MERGE] Key frame analysis: size={len(data)}, first_nal_type={first_nal_type}, has_sps_pps={has_sps_pps}")

            if not has_sps_pps:
                # Prepend config to key frame
                merged_data = self._config_data + packet.data
                config_len = len(self._config_data)
                logger.info(f"[CONFIG_MERGE] Merged {config_len} bytes config with key frame {len(packet.data)} bytes -> {len(merged_data)} bytes")
            else:
                # Key frame already has SPS/PPS, don't merge
                merged_data = packet.data
                logger.info(f"[CONFIG_MERGE] Key frame already has SPS/PPS (NAL type {first_nal_type}), skipping merge")

            self._config_data = None

            return VideoPacket(
                header=packet.header,
                data=merged_data,
                codec_id=packet.codec_id,
            )

        # Non-key frame or no config data - return as-is
        return packet

    # -------------------------------------------------------------------------
    # Packet Loss Detection & PLI
    # -------------------------------------------------------------------------

    def _detect_loss(self, seq: int) -> None:
        """Detect packet loss via sequence gap."""
        if seq > self._expected_seq:
            loss_count = seq - self._expected_seq
            self._consecutive_drops += loss_count

            with self._lock:
                self._stats.packets_lost += loss_count

            logger.warning(
                f"Packet loss detected: {loss_count} packets "
                f"(seq jumped from {self._expected_seq} to {seq}), "
                f"consecutive drops: {self._consecutive_drops}"
            )

            # Check if we have incomplete key frame fragments - send PLI immediately
            if self._pending_keyframe_ts is not None:
                logger.warning(
                    f"Packet loss while key frame reassembly in progress! "
                    f"Sending PLI immediately (ts={self._pending_keyframe_ts})"
                )
                self._send_pli()
                # Clear the pending key frame - we'll get a new one
                self._pending_keyframe_ts = None
            elif self._fec_decoder is not None:
                # FEC mode: use same threshold as normal mode
                # FEC can recover packets, but if recovery fails we need PLI
                if self._pli_enabled and self._consecutive_drops >= self._pli_threshold:
                    logger.warning(f"FEC mode: packet loss ({self._consecutive_drops}) exceeds threshold, sending PLI")
                    self._send_pli()
            elif self._pli_enabled and self._consecutive_drops >= self._pli_threshold:
                # Normal PLI trigger (non-FEC mode)
                self._send_pli()
        elif seq < self._expected_seq:
            # Out of order packet - reduce consecutive drops but don't reset
            self._consecutive_drops = max(0, self._consecutive_drops - 1)
            logger.debug(f"Out of order packet: seq={seq}, expected={self._expected_seq}")
        else:
            # In order - reduce consecutive drops
            self._consecutive_drops = max(0, self._consecutive_drops - 1)

    def _clear_buffers_on_config_change(self) -> None:
        """
        Clear all buffers when config changes (screen rotation).

        Called when SPS/PPS config changes, indicating a new video configuration.
        All old data is invalid for the new resolution/orientation.
        """
        logger.info("[ROTATION] Clearing buffers for config change (screen rotation)")

        # Clear fragment buffers (incomplete frame reassembly)
        if hasattr(self, '_fragment_buffers'):
            count = len(self._fragment_buffers)
            self._fragment_buffers.clear()
            if count > 0:
                logger.info(f"[ROTATION] Cleared {count} fragment buffers")

        # Clear FEC fragment metadata
        if hasattr(self, '_fec_fragment_metadata'):
            count = len(self._fec_fragment_metadata)
            self._fec_fragment_metadata.clear()
            if count > 0:
                logger.info(f"[ROTATION] Cleared {count} FEC fragment metadata")

        # Clear parity fragment buffers
        if hasattr(self, '_parity_fragment_buffers'):
            count = len(self._parity_fragment_buffers)
            self._parity_fragment_buffers.clear()
            if count > 0:
                logger.info(f"[ROTATION] Cleared {count} parity fragment buffers")

        # Clear FEC decoder groups (old groups are invalid)
        if self._fec_decoder is not None:
            old_stats = self._fec_decoder.clear()
            logger.info(f"[ROTATION] Reset FEC decoder (was: completed={old_stats.get('groups_completed', 0)}, "
                        f"recovered={old_stats.get('groups_recovered', 0)})")

        # Reset pending keyframe tracking
        self._pending_keyframe_ts = None

    def _send_pli(self) -> None:
        """Send PLI (Picture Loss Indication) request."""
        if not self._control_channel:
            logger.warning("Cannot send PLI: no control channel")
            return

        # Check cooldown
        now = time.time()
        if now - self._last_pli_time < self._pli_cooldown:
            logger.debug("PLI request on cooldown")
            return

        try:
            # Send RESET_VIDEO control message (type 17 = 0x11)
            # For TYPE_RESET_VIDEO, server only expects 1 byte (the type)
            # No additional data needed - server calls createEmpty(type)
            msg = struct.pack('>B', ControlMessageType.RESET_VIDEO)
            self._control_channel.sendall(msg)

            self._last_pli_time = now
            self._consecutive_drops = 0  # Reset after PLI

            with self._lock:
                self._stats.pli_requests_sent += 1

            logger.info(f"PLI request sent (total: {self._stats.pli_requests_sent})")

            # Notify stats callback
            if self._stats_callback:
                self._stats_callback(self.get_stats())

        except Exception as e:
            logger.error(f"Failed to send PLI: {e}")

    def _check_fec_failures(self) -> None:
        """
        Check for FEC group failures and trigger PLI if needed.

        This is called after processing each packet to check if any
        FEC groups have expired (failed to recover). If failures exceed
        a threshold, send PLI to request a new key frame.
        """
        if self._fec_decoder is None or not self._pli_enabled:
            return

        # Get recent failure count
        failed_count = self._fec_decoder.get_and_reset_failed_count()

        if failed_count > 0:
            logger.warning(f"FEC groups failed: {failed_count}, checking if PLI needed")

            # If more than 2 groups failed recently, send PLI
            # This indicates sustained packet loss that FEC can't recover from
            if failed_count >= 2:
                logger.warning(
                    f"FEC failure threshold exceeded ({failed_count} groups), sending PLI"
                )
                self._send_pli()

    # -------------------------------------------------------------------------
    # Queue Operations
    # -------------------------------------------------------------------------

    def _queue_packet(self, packet: VideoPacket) -> None:
        """Put packet in queue for decoder."""
        try:
            # PRIORITY: Config packets should never be dropped
            # If queue is full and this is a config packet, clear old packets first
            if packet.header.is_config:
                qsize = self._packet_queue.qsize()
                if qsize >= self._packet_queue.maxsize - 1:
                    # Queue nearly full, clear old packets to make room for config
                    logger.warning(f"[CONFIG-PRIORITY] Queue full ({qsize}), clearing for config packet")
                    cleared = 0
                    while not self._packet_queue.empty() and cleared < qsize:
                        try:
                            self._packet_queue.get_nowait()
                            cleared += 1
                        except:
                            break
                    logger.info(f"[CONFIG-PRIORITY] Cleared {cleared} packets for config")

            # DEBUG: Check PTS order (only for non-config packets)
            if not packet.header.is_config:
                current_pts = packet.header.pts
                if self._last_pts is not None:
                    # Allow small backward jumps (up to 100ms = 100000000 ns)
                    # but warn on large discontinuities
                    if current_pts < self._last_pts - 100000000:
                        self._pts_discontinuity_count += 1
                        logger.warning(
                            f"[PTS-BACK] PTS went backward! last={self._last_pts}, "
                            f"current={current_pts}, diff={(self._last_pts - current_pts) / 1000000:.1f}ms, "
                            f"discontinuity_count={self._pts_discontinuity_count}"
                        )
                self._last_pts = current_pts

            # Latency tracking: record queue put time (before put to get accurate queue size)
            qsize_before = self._packet_queue.qsize()
            if packet.packet_id >= 0:
                try:
                    from scrcpy_py_ddlx.latency_tracker import get_tracker
                    get_tracker().record_queue_put(packet.packet_id, qsize_before)
                except Exception:
                    pass

            # Put packet in queue with short timeout
            self._packet_queue.put(packet, timeout=0.05)

            # Log special packets
            if packet.header.is_config:
                logger.info(f"[CONFIG] Config packet: {packet.header.size} bytes")
            elif packet.header.is_key_frame:
                logger.info(f"[KEY_FRAME] pts={packet.header.pts}, size={packet.header.size}")
            else:
                # Log every 30th frame for debugging
                if self._stats.packets_received % 30 == 0:
                    logger.debug(f"[FRAME] pts={packet.header.pts}, size={packet.header.size}")

            # Track queue backlog (warning only)
            if qsize_before > 2:
                logger.warning(f"[QUEUE] Queue backlog: {qsize_before} packets")

        except Exception as e:
            logger.warning(f"Packet queue full, dropping packet: {e}")

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _get_thread_name(self) -> str:
        """Get thread name for logging."""
        codec_name = {
            CodecId.H264: "H264",
            CodecId.H265: "H265",
            CodecId.AV1: "AV1",
        }.get(self._codec_id, "Unknown")
        return f"UdpVideoDemuxer-{codec_name}"
