"""
Socket Communication Package

This package provides socket communication layer for scrcpy client.
Manages three independent socket connections:
1. Video stream socket
2. Audio stream socket
3. Control message socket

Based on scrcpy's socket implementation in app/src/util/net.c and app/src/server.c
"""

# Export types and exceptions
from .types import (
    SocketState,
    SocketType,
    SocketConfig,
    SocketError,
    SocketConnectionError,
    SocketReadError,
    SocketWriteError,
)

# Export base socket class
from .base import ScrcpySocket

# Export specialized socket classes
from .video import VideoSocket
from .audio import AudioSocket
from .control import ControlSocket

# Export socket manager
from .manager import SocketManager

__all__ = [
    # Types
    "SocketState",
    "SocketType",
    "SocketConfig",
    # Exceptions
    "SocketError",
    "SocketConnectionError",
    "SocketReadError",
    "SocketWriteError",
    # Base class
    "ScrcpySocket",
    # Specialized sockets
    "VideoSocket",
    "AudioSocket",
    "ControlSocket",
    # Manager
    "SocketManager",
]
