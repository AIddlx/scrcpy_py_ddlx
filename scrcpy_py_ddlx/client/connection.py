"""
Connection management for scrcpy client.

Supports both ADB tunnel and direct network modes.
"""

import socket
import logging
from typing import Optional, Tuple
from dataclasses import dataclass

# Import authentication module
from ..core.auth import (
    calculate_hmac,
    AuthError,
    AUTH_CHALLENGE_SIZE,
    AUTH_RESPONSE_SIZE,
)
from ..core.device_msg import parse_challenge, parse_auth_result, ProtocolError
from ..core.protocol import TYPE_RESPONSE

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
    file_socket: Optional[socket.socket] = None  # TCP file transfer socket
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
    def perform_auth(socket_: socket.socket, auth_key: bytes, timeout: float = 5.0) -> bool:
        """
        Execute HMAC-SHA256 Challenge-Response authentication.

        Flow:
        1. Receive CHALLENGE (33 bytes: type + 32-byte challenge)
        2. Calculate HMAC-SHA256(key, challenge)
        3. Send RESPONSE (33 bytes: type + 32-byte response)
        4. Receive AUTH_RESULT

        Args:
            socket_: Control socket
            auth_key: 32-byte authentication key
            timeout: Socket timeout for auth operations

        Returns:
            True if authentication successful

        Raises:
            AuthError: If authentication fails
            socket.timeout: If timeout occurs
        """
        original_timeout = socket_.gettimeout()
        socket_.settimeout(timeout)

        try:
            # 1. Receive CHALLENGE
            challenge_data = ConnectionManager._recv_exact(socket_, 33)
            challenge = parse_challenge(challenge_data)
            logger.debug(f"Received authentication challenge ({len(challenge)} bytes)")

            # 2. Calculate RESPONSE
            response = calculate_hmac(auth_key, challenge)
            if len(response) != AUTH_RESPONSE_SIZE:
                raise AuthError(f"Invalid response size: {len(response)}")

            # 3. Send RESPONSE
            response_msg = bytes([TYPE_RESPONSE]) + response
            socket_.sendall(response_msg)
            logger.debug("Sent authentication response")

            # 4. Receive AUTH_RESULT
            # Result format: [type:1][result:1][error_len:2][error:N]
            # Read at least 4 bytes first
            result_header = ConnectionManager._recv_exact(socket_, 4)
            success, error_msg = parse_auth_result(result_header)

            # If there's an error message, it's already parsed from header
            # (for simplicity, we assume error messages fit in initial read)

            if not success:
                raise AuthError(f"Authentication failed: {error_msg}")

            return True

        except ProtocolError as e:
            raise AuthError(f"Protocol error during authentication: {e}")
        finally:
            socket_.settimeout(original_timeout)

    @staticmethod
    def _recv_exact(socket_: socket.socket, n: int) -> bytes:
        """
        Receive exactly n bytes from socket.

        Args:
            socket_: Socket to read from
            n: Number of bytes to receive

        Returns:
            Exactly n bytes

        Raises:
            ConnectionError: If connection closed
            socket.timeout: If timeout occurs
        """
        data = bytearray()
        while len(data) < n:
            chunk = socket_.recv(n - len(data))
            if not chunk:
                raise ConnectionError(f"Connection closed after receiving {len(data)} of {n} bytes")
            data.extend(chunk)
        return bytes(data)

    @staticmethod
    def setup_network_mode(host: str, control_port: int, video_port: int,
                           audio_port: int, file_port: int = 0,
                           send_dummy_byte: bool = True,
                           auth_key: Optional[bytes] = None) -> NetworkConnection:
        """
        Setup network mode connection.

        Args:
            host: Device IP address
            control_port: TCP control port
            video_port: UDP video port
            audio_port: UDP audio port
            file_port: TCP file transfer port (0 = disabled)
            send_dummy_byte: Whether to wait for dummy byte
            auth_key: Optional 32-byte authentication key (enables auth if provided)

        Returns:
            NetworkConnection with all sockets configured

        Raises:
            ConnectionError: If connection fails
            AuthError: If authentication fails
        """
        conn = NetworkConnection()

        # Create UDP receivers first (before sending wake)
        # Use different buffer sizes for video vs audio
        if video_port > 0:
            conn.video_socket = ConnectionManager.create_udp_receiver(
                video_port, buffer_size=VIDEO_SOCKET_BUFFER
            )
        if audio_port > 0:
            conn.audio_socket = ConnectionManager.create_udp_receiver(
                audio_port, buffer_size=AUDIO_SOCKET_BUFFER
            )

        # Connect TCP control
        conn.control_socket = ConnectionManager.connect_tcp_control(host, control_port)

        # Perform authentication BEFORE dummy byte (if auth_key provided)
        if auth_key is not None:
            logger.info("Performing authentication...")
            ConnectionManager.perform_auth(conn.control_socket, auth_key)
            logger.info("Authentication successful")

        # Wait for dummy byte
        if send_dummy_byte:
            dummy = conn.control_socket.recv(1)
            if not dummy:
                raise ConnectionError("No dummy byte received")
            logger.debug("Received dummy byte from control channel")

        # Create TCP file server socket (client listens, server connects)
        if file_port > 0:
            conn.file_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.file_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            conn.file_socket.bind(('0.0.0.0', file_port))
            conn.file_socket.listen(1)
            conn.file_socket.settimeout(30.0)  # 30 second timeout for server to connect
            logger.info(f"File socket listening on 0.0.0.0:{file_port}")

        conn.client_address = host
        logger.info(f"Network mode connected: control={host}:{control_port}, "
                   f"video=0.0.0.0:{video_port}, audio=0.0.0.0:{audio_port}, file=0.0.0.0:{file_port}")

        return conn

    @staticmethod
    def accept_file_connection(conn: NetworkConnection, timeout: float = 30.0) -> Optional[socket.socket]:
        """
        Accept file socket connection from server.

        Args:
            conn: NetworkConnection with file_socket listening
            timeout: Accept timeout in seconds

        Returns:
            Accepted file socket or None if failed
        """
        if conn.file_socket is None:
            return None

        try:
            conn.file_socket.settimeout(timeout)
            client_sock, addr = conn.file_socket.accept()
            conn.file_socket.close()  # Close the listening socket
            conn.file_socket = client_sock  # Replace with connected socket
            logger.info(f"File socket connected from {addr}")
            return client_sock
        except socket.timeout:
            logger.warning(f"File socket accept timeout after {timeout}s")
            return None
        except Exception as e:
            logger.error(f"File socket accept error: {e}")
            return None
