"""
Socket Types and Configuration

This module defines socket types, states, configurations, and exceptions
for scrcpy socket communication.
"""

from dataclasses import dataclass
from enum import Enum


class SocketState(Enum):
    """Socket connection state"""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class SocketType(Enum):
    """Socket connection type"""

    VIDEO = "video"
    AUDIO = "audio"
    CONTROL = "control"


@dataclass
class SocketConfig:
    """
    Socket configuration

    Attributes:
        host: Target host address
        port: Target port number
        socket_type: Type of socket (video/audio/control)
        timeout: Socket timeout in seconds
        buffer_size: Receive buffer size
        tcp_nodelay: Enable TCP_NODELAY
        is_server_mode: If True, use server mode (bind+listen); if False, use client mode (connect)
    """

    host: str = "127.0.0.1"
    port: int = 27183
    socket_type: SocketType = SocketType.VIDEO
    timeout: float = 5.0
    buffer_size: int = 64 * 1024  # 64KB default buffer
    tcp_nodelay: bool = True
    is_server_mode: bool = False  # Client mode by default


class SocketError(Exception):
    """Base exception for socket operations"""

    pass


class SocketConnectionError(SocketError):
    """Exception raised when connection fails"""

    pass


class SocketReadError(SocketError):
    """Exception raised when read operation fails"""

    pass


class SocketWriteError(SocketError):
    """Exception raised when write operation fails"""

    pass
