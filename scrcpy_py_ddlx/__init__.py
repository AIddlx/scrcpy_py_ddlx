"""
scrcpy-py-ddlx - Python Screen Copy Client

A Python implementation of scrcpy (Screen Copy) client for Android device
mirroring and control.

This package provides functionality to:
- Connect to Android devices via ADB
- Mirror device screen (video stream)
- Stream device audio
- Control device via touch/keyboard/mouse

Based on scrcpy from Genymobile:
https://github.com/Genymobile/scrcpy

Example:
    >>> from scrcpy_py_ddlx import ScrcpyClient, ClientConfig
    >>>
    >>> config = ClientConfig(host="localhost", port=27183)
    >>> client = ScrcpyClient(config)
    >>> if client.connect():
    ...     client.run_with_qt()
"""

from .core import (
    ADBManager,
    SocketManager,
    ADBDevice,
    ADBDeviceType,
    ADBTunnel,
    VideoSocket,
    AudioSocket,
    ControlSocket,
    # Protocol types
    ControlMessageType,
    DeviceMessageType,
    # Stream and decoder
    VideoDecoder,
    StreamParser,
    # Control messages
    ControlMessage,
    ControlMessageQueue,
)
from .client import (
    ScrcpyClient,
    ClientConfig,
    ClientState,
)
from .mcp_server import (
    ScrcpyMCPServer,
    create_mcp_server,
)
try:
    from .mcp_http_server import ScrcpyHTTPServer, create_http_server
    HTTP_SERVER_AVAILABLE = True
except ImportError:
    HTTP_SERVER_AVAILABLE = False
    ScrcpyHTTPServer = None
    create_http_server = None

# Video window (optional, requires PySide6)
try:
    from .core.player.video import VideoWindow, create_video_window
    VIDEO_WINDOW_AVAILABLE = True
except ImportError:
    VIDEO_WINDOW_AVAILABLE = False
    VideoWindow = None
    create_video_window = None

__version__ = "0.1.0"
__all__ = [
    # ADB and Socket
    "ADBManager",
    "SocketManager",
    "ADBDevice",
    "ADBTunnel",
    "VideoSocket",
    "AudioSocket",
    "ControlSocket",
    # Client
    "ScrcpyClient",
    "ClientConfig",
    "ClientState",
    # MCP Server (for AI Agents)
    "ScrcpyMCPServer",
    "create_mcp_server",
    "ScrcpyHTTPServer",
    "create_http_server",
    "HTTP_SERVER_AVAILABLE",
    # Protocol
    "ControlMessageType",
    "DeviceMessageType",
    # Stream and decoder
    "VideoDecoder",
    "StreamParser",
    # Control
    "ControlMessage",
    "ControlMessageQueue",
    # Video window (optional)
    "VideoWindow",
    "create_video_window",
    "VIDEO_WINDOW_AVAILABLE",
]
