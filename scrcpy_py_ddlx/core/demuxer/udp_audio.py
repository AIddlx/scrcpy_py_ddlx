"""
UDP Audio Demuxer - Specialized demuxer for UDP network mode audio.

This demuxer is designed specifically for UDP audio transport and provides:
1. Direct UDP packet handling (no TCP stream emulation)
2. UDP header parsing (seq, timestamp, flags)
3. Audio packet extraction and queue delivery
4. Optional FEC support for packet loss recovery

Unlike UdpVideoDemuxer, this is simpler because:
- Audio packets are typically small (no fragmentation)
- No PLI mechanism needed (audio decoders handle gaps better)
- Lower latency requirements
"""

import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, TYPE_CHECKING
from queue import Queue

from ..protocol import (
    PACKET_HEADER_SIZE,
    PACKET_FLAG_CONFIG,
    PACKET_PTS_MASK,
    UDP_HEADER_SIZE,
    UDP_FLAG_CONFIG,
    UDP_FLAG_FEC_DATA,
    UDP_FLAG_FEC_PARITY,
)

if TYPE_CHECKING:
    from .fec import FecDecoder

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class UdpAudioPacketHeader:
    """Parsed UDP audio packet header."""
    sequence: int
    timestamp: int
    flags: int
    send_time_ns: int = 0  # v1.2: sender timestamp for E2E latency

    @property
    def is_config(self) -> bool:
        return bool(self.flags & UDP_FLAG_CONFIG)

    @property
    def is_fec_data(self) -> bool:
        return bool(self.flags & UDP_FLAG_FEC_DATA)

    @property
    def is_fec_parity(self) -> bool:
        return bool(self.flags & UDP_FLAG_FEC_PARITY)


@dataclass
class UdpAudioStats:
    """UDP audio statistics."""
    packets_received: int = 0
    bytes_received: int = 0
    packets_lost: int = 0
    config_packets: int = 0
    audio_packets: int = 0
    fec_recoveries: int = 0
    parse_errors: int = 0


# =============================================================================
# UdpAudioDemuxer
# =============================================================================

class UdpAudioDemuxer:
    """
    UDP Audio Demuxer for network mode.

    Receives UDP audio packets, parses headers, and delivers audio data
    to the packet queue for the audio decoder.

    Packet format:
        [UDP Header: 16B] [Scrcpy Header: 12B] [Audio Payload: NB]

    UDP Header (16 bytes):
        - sequence: 4 bytes (uint32, big-endian)
        - timestamp: 8 bytes (int64, big-endian)
        - flags: 4 bytes (uint32, big-endian)

    Scrcpy Header (12 bytes):
        - pts_flags: 8 bytes (PTS + flags)
        - size: 4 bytes (payload size)
    """

    MAX_UDP_PACKET = 65507  # Max UDP packet size

    # Disconnect detection: 1.5s timeout * 3 = 4.5s max wait
    MAX_CONSECUTIVE_TIMEOUTS = 3
    SOCKET_TIMEOUT = 1.5

    def __init__(
        self,
        udp_socket: socket.socket,
        packet_queue: Queue,
        codec_id: int = 0x4f505553,  # 'OPUS' in ASCII
        fec_decoder: Optional['FecDecoder'] = None,
        stats_callback: Optional[Callable[[UdpAudioStats], None]] = None,
    ):
        """
        Initialize UDP audio demuxer.

        Args:
            udp_socket: UDP socket bound to receive audio packets
            packet_queue: Queue for audio packets (bytes)
            codec_id: Expected audio codec ID (default: OPUS)
            fec_decoder: Optional FEC decoder for packet recovery
            stats_callback: Optional callback for statistics updates
        """
        self._socket = udp_socket
        self._socket.settimeout(self.SOCKET_TIMEOUT)  # Default timeout for disconnect detection
        self._packet_queue = packet_queue
        self._codec_id = codec_id
        self._fec_decoder = fec_decoder
        self._stats_callback = stats_callback

        # Thread control
        self._thread: Optional[threading.Thread] = None
        self._stopped = threading.Event()

        # Sequence tracking
        self._expected_seq: int = 0
        self._seq_initialized: bool = False

        # Statistics
        self._stats = UdpAudioStats()
        self._lock = threading.Lock()

        # Codec config received flag
        self._config_received: bool = False

        # Server disconnect detection
        self._consecutive_timeouts: int = 0
        self._last_packet_time: float = 0

        logger.debug(f"UdpAudioDemuxer created: codec=0x{codec_id:08x}")

    # -------------------------------------------------------------------------
    # Public Methods
    # -------------------------------------------------------------------------

    def start(self) -> None:
        """Start the demuxer thread."""
        if self._thread is not None:
            logger.warning("UdpAudioDemuxer already started")
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
        self._thread.join(timeout=3.0)
        if self._thread.is_alive():
            logger.warning(f"{self._get_thread_name()} did not stop gracefully")

        self._thread = None
        logger.info(f"{self._get_thread_name()} stopped")

    def get_stats(self) -> UdpAudioStats:
        """Get current statistics."""
        with self._lock:
            return UdpAudioStats(
                packets_received=self._stats.packets_received,
                bytes_received=self._stats.bytes_received,
                packets_lost=self._stats.packets_lost,
                config_packets=self._stats.config_packets,
                audio_packets=self._stats.audio_packets,
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
        try:
            while not self._stopped.is_set():
                try:
                    # Receive UDP packet
                    packet, addr = self._socket.recvfrom(self.MAX_UDP_PACKET)

                    # Reset timeout counter on successful receive
                    self._consecutive_timeouts = 0

                    if len(packet) == 0:
                        continue

                    # Update last packet time
                    self._last_packet_time = time.time()

                    # Process packet
                    self._process_packet(packet)

                except socket.timeout:
                    # Check for server disconnect
                    self._consecutive_timeouts += 1
                    if self._consecutive_timeouts >= self.MAX_CONSECUTIVE_TIMEOUTS:
                        # Only treat as disconnect if we've received at least one packet
                        if self._last_packet_time > 0:
                            elapsed = time.time() - self._last_packet_time
                            logger.error(
                                f"Server disconnect detected: no audio data for {elapsed:.1f}s "
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

    def _process_packet(self, packet: bytes) -> None:
        """
        Process a single UDP audio packet.

        Packet format:
        - Normal: [UDP Header: 16B] [Scrcpy Header: 12B] [Payload: NB]
        """
        if len(packet) < UDP_HEADER_SIZE:
            logger.warning(f"Audio packet too short: {len(packet)} bytes")
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
            logger.info(f"Audio sequence initialized: starting from seq={udp_header.sequence}")
        else:
            # Detect packet loss
            self._detect_loss(udp_header.sequence)

        # Debug: Log first few packets and periodically after
        if self._stats.packets_received <= 100 or self._stats.packets_received % 100 == 0:
            logger.debug(
                f"[UDP-AUDIO] #{self._stats.packets_received}: seq={udp_header.sequence}, "
                f"ts={udp_header.timestamp}, flags={udp_header.flags:#x}, "
                f"config={udp_header.is_config}, payload={len(payload)} bytes"
            )

        # Dispatch by packet type
        if udp_header.is_fec_parity:
            self._handle_fec_parity(udp_header, payload)
        elif udp_header.is_fec_data and self._fec_decoder is not None:
            self._handle_fec_data(udp_header, payload)
        elif udp_header.is_config:
            self._handle_config_packet(udp_header, payload)
        else:
            self._handle_normal_packet(udp_header, payload)

        # Update expected sequence
        self._expected_seq = udp_header.sequence + 1

    # -------------------------------------------------------------------------
    # Packet Handlers
    # -------------------------------------------------------------------------

    def _handle_config_packet(self, udp_header: UdpAudioPacketHeader, payload: bytes) -> None:
        """
        Handle audio config packet (codec information).

        Audio config packets can be in two formats:
        1. First config (from writeAudioHeader): just 4 bytes codec_id (no scrcpy header)
        2. Subsequent config (from writePacket): scrcpy header + codec config data

        Note: OPUS decoder doesn't need config packets - they're just codec identification.
        We only log the config info but don't send to decoder queue.
        """
        with self._lock:
            self._stats.config_packets += 1

        # Case 1: First config packet - just 4 bytes codec_id (no scrcpy header)
        if len(payload) == 4:
            codec_id = struct.unpack('>I', payload)[0]
            codec_name = payload.decode('ascii', errors='ignore')

            with self._lock:
                self._config_received = True

            logger.info(f"Audio config (simple): codec=0x{codec_id:08x} ('{codec_name}')")
            # OPUS decoder doesn't need config packets - don't queue
            return

        # Case 2: Full scrcpy packet format
        if len(payload) < PACKET_HEADER_SIZE:
            logger.warning(f"Config packet too short: {len(payload)} bytes")
            return

        # Parse scrcpy header
        pts_flags, payload_size = struct.unpack('>QI', payload[:PACKET_HEADER_SIZE])
        config_data = payload[PACKET_HEADER_SIZE:PACKET_HEADER_SIZE + payload_size]

        if len(config_data) < 4:
            logger.warning(f"Config data too short: {len(config_data)} bytes")
            return

        # Extract codec ID
        codec_id = struct.unpack('>I', config_data[:4])[0]
        codec_name = config_data[:4].decode('ascii', errors='ignore')

        with self._lock:
            self._config_received = True

        logger.info(f"Audio config (full): codec=0x{codec_id:08x} ('{codec_name}'), size={payload_size}")
        # OPUS decoder doesn't need config packets - don't queue

    def _handle_normal_packet(self, udp_header: UdpAudioPacketHeader, payload: bytes) -> None:
        """Handle normal audio packet."""
        if len(payload) < PACKET_HEADER_SIZE:
            logger.warning(f"Audio payload too short: {len(payload)} bytes")
            return

        # Parse scrcpy header
        pts_flags, payload_size = struct.unpack('>QI', payload[:PACKET_HEADER_SIZE])

        # Validate size
        if payload_size > len(payload) - PACKET_HEADER_SIZE:
            logger.warning(f"Audio payload size mismatch: expected {payload_size}, got {len(payload) - PACKET_HEADER_SIZE}")
            return

        # Extract PTS (for logging/debugging)
        pts = pts_flags & PACKET_PTS_MASK

        # Extract pure OPUS data (skip scrcpy header - decoder expects raw audio data)
        # Scrcpy header format: [pts_flags: 8B] [payload_size: 4B] [audio_data: NB]
        opus_data = payload[PACKET_HEADER_SIZE:PACKET_HEADER_SIZE + payload_size]

        try:
            self._packet_queue.put(opus_data, timeout=0.1)
            with self._lock:
                self._stats.audio_packets += 1
        except:
            logger.debug("Audio packet queue full, dropping packet")

        # Debug log periodically
        if self._stats.audio_packets % 100 == 0:
            logger.debug(f"Audio packets: {self._stats.audio_packets}, pts={pts}")

    def _handle_fec_data(self, udp_header: UdpAudioPacketHeader, payload: bytes) -> None:
        """Handle FEC-protected audio data packet."""
        if self._fec_decoder is None:
            # No FEC decoder, treat as normal packet
            self._handle_normal_packet(udp_header, payload)
            return

        # Parse FEC header (7 bytes) - same format as video
        # group_id(2) + packet_idx(1) + total_data(1) + total_parity(1) + original_size(2)
        if len(payload) < 7:
            logger.warning(f"FEC data packet too short: {len(payload)} bytes")
            return

        group_id = struct.unpack('>H', payload[0:2])[0]
        packet_idx = payload[2]
        total_data = payload[3]
        total_parity = payload[4]
        original_size = struct.unpack('>H', payload[5:7])[0]
        scrcpy_data = payload[7:]

        logger.debug(
            f"[FEC-AUDIO-DATA] group={group_id}, idx={packet_idx}/{total_data}, "
            f"parity={total_parity}, orig_size={original_size}, payload={len(scrcpy_data)} bytes"
        )

        # Add to FEC decoder
        result = self._fec_decoder.add_data_packet(
            group_id=group_id,
            packet_idx=packet_idx,
            total_data=total_data,
            total_parity=total_parity,
            data=scrcpy_data,
            original_size=original_size,
        )

        if result:
            self._process_recovered_packets(result)

    def _handle_fec_parity(self, udp_header: UdpAudioPacketHeader, payload: bytes) -> None:
        """Handle FEC parity packet.

        FEC Parity Packet Format:
        [FEC Header: 5B] [Parity Data: NB]

        FEC Header:
          group_id: 2B (uint16, big-endian)
          parity_idx: 1B (uint8)
          total_data: 1B (uint8) - K (for recovery reference)
          total_parity: 1B (uint8) - M
        """
        if self._fec_decoder is None:
            return

        # Parse FEC header (5 bytes) - parity packets don't have original_size
        if len(payload) < 5:
            logger.warning(f"FEC parity packet too short: {len(payload)} bytes")
            return

        group_id = struct.unpack('>H', payload[0:2])[0]
        parity_idx = payload[2]
        total_data = payload[3]
        total_parity = payload[4]
        parity_data = payload[5:]

        logger.debug(
            f"[FEC-AUDIO-PARITY] group={group_id}, idx={parity_idx}/{total_parity}, "
            f"data={total_data}, payload={len(parity_data)} bytes"
        )

        # Add parity to decoder
        result = self._fec_decoder.add_parity_packet(
            group_id=group_id,
            parity_idx=parity_idx,
            total_data=total_data,
            total_parity=total_parity,
            parity_data=parity_data,
        )

        if result:
            self._process_recovered_packets(result)

    def _process_recovered_packets(self, packets: list) -> None:
        """Process recovered packets from FEC decoder.

        FEC decoder returns packets with scrcpy header (12 bytes), but
        audio decoder expects pure OPUS data without header.
        """
        for packet_data in packets:
            try:
                # Check if packet has scrcpy header
                if len(packet_data) < PACKET_HEADER_SIZE:
                    logger.warning(f"Recovered packet too short: {len(packet_data)} bytes")
                    continue

                # Parse scrcpy header to get payload size
                pts_flags, payload_size = struct.unpack('>QI', packet_data[:PACKET_HEADER_SIZE])

                # Validate payload size
                if payload_size > len(packet_data) - PACKET_HEADER_SIZE:
                    logger.warning(f"Recovered packet size mismatch: expected {payload_size}, got {len(packet_data) - PACKET_HEADER_SIZE}")
                    continue

                # Extract pure OPUS data (skip scrcpy header)
                opus_data = packet_data[PACKET_HEADER_SIZE:PACKET_HEADER_SIZE + payload_size]

                self._packet_queue.put(opus_data, timeout=0.1)
                with self._lock:
                    self._stats.fec_recoveries += 1
                logger.debug(f"FEC recovered audio packet: {len(opus_data)} bytes OPUS data")
            except:
                logger.debug("Audio packet queue full, dropping recovered packet")

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _parse_udp_header(self, data: bytes) -> UdpAudioPacketHeader:
        """Parse UDP packet header (24 bytes v1.2 format, with 16-byte fallback)."""
        if len(data) >= 24:
            # New format: [seq: 4B] [timestamp: 8B] [flags: 4B] [send_time_ns: 8B]
            sequence, timestamp, flags, send_ns = struct.unpack('>IqIq', data[:24])
            return UdpAudioPacketHeader(
                sequence=sequence,
                timestamp=timestamp,
                flags=flags,
                send_time_ns=send_ns,
            )
        else:
            # Fallback for old format (16 bytes)
            sequence, timestamp, flags = struct.unpack('>IqI', data[:16])
            return UdpAudioPacketHeader(
                sequence=sequence,
                timestamp=timestamp,
                flags=flags,
                send_time_ns=0,
            )

    def _detect_loss(self, seq: int) -> None:
        """Detect packet loss from sequence number gap."""
        if seq < self._expected_seq:
            # Out of order packet
            return

        gap = seq - self._expected_seq
        if gap > 0:
            with self._lock:
                self._stats.packets_lost += gap
            logger.debug(f"Audio packet loss detected: {gap} packets (expected {self._expected_seq}, got {seq})")

    def _get_thread_name(self) -> str:
        """Get thread name for logging."""
        return "UdpAudioDemuxer"

    # -------------------------------------------------------------------------
    # Pause/Resume for Lazy Decode
    # -------------------------------------------------------------------------

    def pause(self) -> None:
        """Pause the demuxer (for lazy decode mode)."""
        logger.debug("UdpAudioDemuxer pause requested (no-op for UDP)")

    def resume(self) -> None:
        """Resume the demuxer (for lazy decode mode)."""
        logger.debug("UdpAudioDemuxer resume requested (no-op for UDP)")


# =============================================================================
# Factory Function
# =============================================================================

def create_udp_audio_demuxer(
    udp_socket: socket.socket,
    audio_codec: int = 0x4f505553,  # OPUS
    queue_size: int = 30,
    fec_decoder: Optional['FecDecoder'] = None,
) -> tuple:
    """
    Create a UDP audio demuxer with packet queue.

    Args:
        udp_socket: UDP socket for receiving audio
        audio_codec: Audio codec ID
        queue_size: Packet queue size
        fec_decoder: Optional FEC decoder

    Returns:
        Tuple of (UdpAudioDemuxer, Queue)
    """
    packet_queue = Queue(maxsize=queue_size)
    demuxer = UdpAudioDemuxer(
        udp_socket,
        packet_queue,
        codec_id=audio_codec,
        fec_decoder=fec_decoder,
    )
    return demuxer, packet_queue
