"""
Base Socket Class

This module provides the base ScrcpySocket class that handles low-level
socket operations including connection establishment, data transmission,
state management, and error handling.
"""

import socket
import threading
import logging
import time

from .types import (
    SocketConfig,
    SocketState,
    SocketConnectionError,
    SocketReadError,
    SocketWriteError,
)

logger = logging.getLogger(__name__)


class ScrcpySocket:
    """
    Base class for scrcpy socket connections

    Handles low-level socket operations including:
    - Connection establishment
    - Data transmission
    - Connection state management
    - Error handling and recovery

    Supports two connection modes:
    - Client mode (is_server_mode=False): Connects to a remote server using socket.connect()
    - Server mode (is_server_mode=True): Binds, listens, and accepts incoming connection

    Example:
        >>> sock = ScrcpySocket(SocketConfig(socket_type=SocketType.VIDEO))
        >>> sock.connect()
        >>> data = sock.recv(1024)
        >>> sock.send(b"control_data")
        >>> sock.close()
    """

    def __init__(self, config: SocketConfig):
        """
        Initialize socket

        Args:
            config: Socket configuration
        """
        self.config = config
        self._socket: socket.socket | None = None
        self._state = SocketState.DISCONNECTED
        self._lock = threading.RLock()
        self._closed = False

    @property
    def state(self) -> SocketState:
        """Get current socket state"""
        with self._lock:
            return self._state

    @property
    def is_connected(self) -> bool:
        """Check if socket is connected"""
        with self._lock:
            return self._state == SocketState.CONNECTED

    def connect(self, retries: int = 3, retry_delay: float = 0.1) -> bool:
        """
        Establish socket connection

        Supports two connection modes:
        - Client mode (is_server_mode=False): Connects to a remote server using socket.connect()
        - Server mode (is_server_mode=True): Binds, listens, and accepts incoming connection

        Args:
            retries: Number of connection retries (for client mode) or accept attempts (for server mode)
            retry_delay: Delay between retries in seconds

        Returns:
            True if connection successful

        Raises:
            SocketConnectionError: If connection fails after all retries
        """
        with self._lock:
            if self._state == SocketState.CONNECTED:
                logger.warning(
                    f"{self.config.socket_type.value} socket already connected"
                )
                return True

            if self._closed:
                raise SocketConnectionError("Socket is closed")

            self._state = SocketState.CONNECTING

        last_error = None

        if self.config.is_server_mode:
            # Server mode: bind + listen + accept (for adb reverse)
            return self._connect_server_mode(retries, retry_delay)
        else:
            # Client mode: connect (for adb forward)
            return self._connect_client_mode(retries, retry_delay)

    def _connect_client_mode(self, retries: int, retry_delay: float) -> bool:
        """
        Connect using client mode (socket.connect)

        Used for adb forward tunnels where the client connects to the device.
        """
        last_error = None

        for attempt in range(retries):
            try:
                # Create new socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(self.config.timeout)

                # Enable TCP_NODELAY for control socket
                if self.config.tcp_nodelay:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                logger.debug(
                    f"[CLIENT MODE] Connecting {self.config.socket_type} socket to "
                    f"{self.config.host}:{self.config.port} (attempt {attempt + 1}/{retries})"
                )

                sock.connect((self.config.host, self.config.port))

                with self._lock:
                    self._socket = sock
                    self._state = SocketState.CONNECTED

                logger.info(f"{self.config.socket_type} socket connected (client mode)")
                return True

            except socket.timeout:
                last_error = (
                    f"Connection timeout to {self.config.host}:{self.config.port}"
                )
                logger.warning(f"Connection attempt {attempt + 1} timed out")
            except ConnectionRefusedError:
                last_error = (
                    f"Connection refused by {self.config.host}:{self.config.port}"
                )
                logger.warning(f"Connection attempt {attempt + 1} refused")
            except OSError as e:
                last_error = f"Socket error: {e}"
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}")

            if attempt < retries - 1:
                time.sleep(retry_delay)

        with self._lock:
            self._state = SocketState.ERROR

        raise SocketConnectionError(
            f"Failed to connect {self.config.socket_type.value} socket after {retries} attempts: "
            f"{last_error}"
        )

    def _connect_server_mode(self, retries: int, retry_delay: float) -> bool:
        """
        Connect using server mode (bind + listen + accept)

        Used for adb reverse tunnels where the device connects to the client.
        """
        last_error = None

        for attempt in range(retries):
            try:
                # Create server socket
                server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

                # Enable TCP_NODELAY for control socket
                if self.config.tcp_nodelay:
                    server_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                bind_address = (self.config.host, self.config.port)
                logger.debug(
                    f"[SERVER MODE] Binding {self.config.socket_type.value} socket to "
                    f"{bind_address[0]}:{bind_address[1]} (attempt {attempt + 1}/{retries})"
                )

                server_sock.bind(bind_address)
                server_sock.listen(1)
                server_sock.settimeout(self.config.timeout)

                logger.info(
                    f"{self.config.socket_type.value} socket listening on "
                    f"{bind_address[0]}:{bind_address[1]}, waiting for connection..."
                )

                # Wait for incoming connection
                conn, addr = server_sock.accept()

                # Close the server socket and use the connection socket
                server_sock.close()

                with self._lock:
                    self._socket = conn
                    self._state = SocketState.CONNECTED

                logger.info(
                    f"{self.config.socket_type.value} socket connected (server mode) from {addr[0]}:{addr[1]}"
                )
                return True

            except socket.timeout:
                last_error = f"Accept timeout on {self.config.host}:{self.config.port}"
                logger.warning(f"Accept attempt {attempt + 1} timed out")
                # Clean up server socket on timeout
                try:
                    server_sock.close()
                except Exception:
                    pass
            except OSError as e:
                last_error = f"Socket error: {e}"
                logger.warning(f"Accept attempt {attempt + 1} failed: {e}")
                # Clean up server socket on error
                try:
                    server_sock.close()
                except Exception:
                    pass

            if attempt < retries - 1:
                time.sleep(retry_delay)

        with self._lock:
            self._state = SocketState.ERROR

        raise SocketConnectionError(
            f"Failed to accept {self.config.socket_type.value} socket connection after {retries} attempts: "
            f"{last_error}"
        )

    def reconnect(self) -> bool:
        """
        Reconnect socket

        Returns:
            True if reconnection successful
        """
        self.close()
        return self.connect()

    def recv(self, size: int) -> bytes:
        """
        Receive data from socket

        Args:
            size: Number of bytes to receive

        Returns:
            Received data

        Raises:
            SocketReadError: If receive operation fails
        """
        with self._lock:
            if not self._socket or self._state != SocketState.CONNECTED:
                raise SocketReadError(
                    f"{self.config.socket_type.value} socket not connected"
                )

        try:
            data = self._socket.recv(size)
            if not data:
                raise SocketReadError("Connection closed by remote")
            return data

        except socket.timeout:
            raise SocketReadError("Receive timeout")
        except OSError as e:
            with self._lock:
                self._state = SocketState.ERROR
            raise SocketReadError(f"Receive error: {e}")

    def recv_all(self, size: int) -> bytes:
        """
        Receive exact number of bytes from socket

        Args:
            size: Number of bytes to receive

        Returns:
            Received data

        Raises:
            SocketReadError: If receive operation fails or incomplete
        """
        data = bytearray()
        remaining = size

        while remaining > 0:
            chunk = self.recv(min(remaining, self.config.buffer_size))
            data.extend(chunk)
            remaining -= len(chunk)

        return bytes(data)

    def recv_into(self, buffer: bytearray, size: int) -> int:
        """
        Receive data directly into buffer

        Args:
            buffer: Buffer to receive data into
            size: Maximum number of bytes to receive

        Returns:
            Number of bytes received

        Raises:
            SocketReadError: If receive operation fails
        """
        with self._lock:
            if not self._socket or self._state != SocketState.CONNECTED:
                raise SocketReadError(
                    f"{self.config.socket_type.value} socket not connected"
                )

        try:
            nbytes = self._socket.recv_into(buffer, size)
            if nbytes == 0:
                raise SocketReadError("Connection closed by remote")
            return nbytes

        except socket.timeout:
            raise SocketReadError("Receive timeout")
        except OSError as e:
            with self._lock:
                self._state = SocketState.ERROR
            raise SocketReadError(f"Receive error: {e}")

    def send(self, data: bytes) -> int:
        """
        Send data through socket

        Args:
            data: Data to send

        Returns:
            Number of bytes sent

        Raises:
            SocketWriteError: If send operation fails
        """
        with self._lock:
            if not self._socket or self._state != SocketState.CONNECTED:
                raise SocketWriteError(
                    f"{self.config.socket_type.value} socket not connected"
                )

        try:
            sent = self._socket.send(data)
            return sent

        except OSError as e:
            with self._lock:
                self._state = SocketState.ERROR
            raise SocketWriteError(f"Send error: {e}")

    def send_all(self, data: bytes) -> int:
        """
        Send all data through socket

        Args:
            data: Data to send

        Returns:
            Number of bytes sent

        Raises:
            SocketWriteError: If send operation fails or incomplete
        """
        total_sent = 0
        remaining = len(data)

        while remaining > 0:
            sent = self.send(data[total_sent:])
            total_sent += sent
            remaining -= sent

        return total_sent

    def close(self) -> None:
        """Close socket connection"""
        with self._lock:
            if self._closed:
                return

            self._closed = True
            self._state = SocketState.DISCONNECTED

            if self._socket:
                try:
                    # Shutdown both directions
                    self._socket.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass  # Ignore shutdown errors

                try:
                    self._socket.close()
                except Exception:
                    pass  # Ignore close errors

                self._socket = None

                logger.debug(f"{self.config.socket_type} socket closed")

    def interrupt(self) -> None:
        """Interrupt socket operations without closing"""
        with self._lock:
            if self._socket:
                try:
                    self._socket.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass

    def __del__(self):
        """Destructor - ensure socket is closed"""
        self.close()
