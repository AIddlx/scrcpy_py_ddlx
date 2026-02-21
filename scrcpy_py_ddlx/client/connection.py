"""
Connection management for scrcpy client.

Supports both ADB tunnel and direct network modes.
"""

import socket
import logging
from typing import Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# =============================================================================
# Buffer Configuration
# =============================================================================
# Based on: 8Mbps video @ 60fps, 128Kbps audio @ 50fps
#
# Video calculation:
#   8 Mbps = 1 MB/s, 60 fps = 16.7 KB/frame, ~12 packets/frame
#   500ms burst = 500 KB, so 4 MB buffer handles ~2 seconds of burst
#
# Audio calculation:
#   128 Kbps = 16 KB/s, 50 fps = ~320 bytes/frame
#   256 KB buffer handles ~16 seconds of audio (way more than needed)

VIDEO_SOCKET_BUFFER = 4 * 1024 * 1024    # 4 MB - handle video bursts
AUDIO_SOCKET_BUFFER = 256 * 1024         # 256 KB - audio bandwidth is tiny


@dataclass
class NetworkConnection:
    """Holds network mode connection state."""
    control_socket: Optional[socket.socket] = None
    video_socket: Optional[socket.socket] = None
    audio_socket: Optional[socket.socket] = None
    client_address: Optional[str] = None


class ConnectionManager:
    """Manages connection to scrcpy server."""

    @staticmethod
    def create_udp_receiver(port: int, timeout: float = 5.0, buffer_size: int = None) -> socket.socket:
        """
        Create UDP socket bound to receive data.

        Args:
            port: Port to bind to
            timeout: Socket timeout in seconds
            buffer_size: Receive buffer size (default: VIDEO_SOCKET_BUFFER)

        Note:
            Buffer size rationale:
            - Video (4 MB): Handles ~500ms of 8Mbps video burst
            - Audio (256 KB): Handles >>1 second of 128Kbps audio
            - Actual size may be limited by OS (net.core.rmem_max on Linux)
        """
        if buffer_size is None:
            buffer_size = VIDEO_SOCKET_BUFFER

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Set receive buffer - actual size may be capped by OS
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, buffer_size)
        actual_buffer = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)

        if actual_buffer < buffer_size:
            logger.warning(
                f"UDP buffer limited by OS: requested={buffer_size}, "
                f"actual={actual_buffer}. Consider increasing net.core.rmem_max"
            )
        else:
            logger.info(f"UDP receive buffer: {actual_buffer} bytes ({actual_buffer // 1024} KB)")

        sock.settimeout(timeout)
        sock.bind(("0.0.0.0", port))
        logger.debug(f"UDP receiver bound to 0.0.0.0:{port}")
        return sock

    @staticmethod
    def connect_tcp_control(host: str, port: int, timeout: float = 10.0) -> socket.socket:
        """Connect to TCP control port."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        logger.debug(f"TCP control connected to {host}:{port}")
        return sock

    @staticmethod
    def setup_network_mode(host: str, control_port: int, video_port: int,
                           audio_port: int, send_dummy_byte: bool = True) -> NetworkConnection:
        """
        Setup network mode connection.

        Args:
            host: Device IP address
            control_port: TCP control port
            video_port: UDP video port
            audio_port: UDP audio port
            send_dummy_byte: Whether to wait for dummy byte

        Returns:
            NetworkConnection with all sockets configured
        """
        conn = NetworkConnection()

        # Create UDP receivers first (before sending wake)
        # Use different buffer sizes for video vs audio
        conn.video_socket = ConnectionManager.create_udp_receiver(
            video_port, buffer_size=VIDEO_SOCKET_BUFFER
        )
        if audio_port > 0:
            conn.audio_socket = ConnectionManager.create_udp_receiver(
                audio_port, buffer_size=AUDIO_SOCKET_BUFFER
            )

        # Connect TCP control
        conn.control_socket = ConnectionManager.connect_tcp_control(host, control_port)

        # Wait for dummy byte
        if send_dummy_byte:
            dummy = conn.control_socket.recv(1)
            if not dummy:
                raise ConnectionError("No dummy byte received")
            logger.debug("Received dummy byte from control channel")

        conn.client_address = host
        logger.info(f"Network mode connected: control={host}:{control_port}, "
                   f"video=0.0.0.0:{video_port}, audio=0.0.0.0:{audio_port}")

        return conn
