"""
Configuration and state for scrcpy client.

This module contains the configuration and state tracking dataclasses
for the scrcpy client.
"""

import socket
from dataclasses import dataclass, field
from typing import Optional, Callable, Tuple
import numpy as np

from scrcpy_py_ddlx.core.audio.demuxer import AudioDemuxer as OldAudioDemuxer


@dataclass
class ClientConfig:
    """
    Configuration for scrcpy client.

    This configuration matches the scrcpy server parameters.
    """
    # Connection settings
    host: str = "localhost"
    port: int = 27183

    # ADB settings
    server_jar: str = "scrcpy-server"  # Path to scrcpy-server file (will be pushed as .jar on device)
    device_serial: Optional[str] = None  # Specific device serial (None = auto-select)

    # Video settings
    video: bool = True
    bitrate: int = 8000000  # 8 Mbps
    max_fps: int = 60
    codec: str = "h264"  # h264, h265, or av1
    codec_options: str = ""
    crop: str = ""
    lock_video_orientation: int = -1  # -1 = unlocked

    # Display settings
    display_id: int = 0
    control: bool = True

    # Connection timeouts
    connection_timeout: float = 10.0
    socket_timeout: float = 5.0

    # Recording
    record_filename: str = ""
    record_format: str = "mp4"

    # Audio settings
    audio: bool = False
    audio_codec: int = OldAudioDemuxer.OPUS  # RAW, OPUS, AAC, FDK_AAC, FLAC

    # Clipboard
    clipboard_autosync: bool = False

    # Power management
    power_off_on_close: bool = False
    stay_awake: bool = False

    # Frame callbacks
    frame_callback: Optional[Callable[[np.ndarray], None]] = None
    init_callback: Optional[Callable[[int, int], None]] = None

    # Video window (PySide6)
    show_window: bool = False  # Show video window (requires PySide6)

    # Lazy decode: pause video/audio decoding when not needed (saves CPU)
    # When enabled, decoders auto-start for screenshot/recording and auto-pause after
    lazy_decode: bool = True  # Default: True for energy-efficient operation

    # TCP/IP wireless connection settings
    tcpip: bool = False  # Enable TCP/IP wireless mode
    tcpip_ip: Optional[str] = None  # Specific IP address (None = auto-detect)
    tcpip_port: int = 5555  # TCP/IP port (default ADB port)
    tcpip_auto_disconnect: bool = False  # Auto disconnect TCP/IP on close (False = keep connection for next run, like official scrcpy)


@dataclass
class ClientState:
    """Client state tracking."""
    connected: bool = False
    running: bool = False
    device_name: str = ""
    device_size: Tuple[int, int] = (0, 0)
    codec_id: int = 0
    device_serial: str = ""  # Device serial number for ADB operations

    # Socket references (eliminates need for global SocketState)
    video_socket: Optional[socket.socket] = None  # Renamed to avoid conflict
    audio_socket: Optional[socket.socket] = None
    control_socket: Optional[socket.socket] = None

    # ADB tunnel information (needed for socket connections)
    tunnel: Optional["ADBTunnel"] = None  # Stored for creating additional sockets

    # Forward mode flag (Windows uses forward mode due to reverse tunnel issues)
    is_forward_mode: bool = False

    # TCP/IP connection state
    tcpip_connected: bool = False  # Whether connected via TCP/IP
    tcpip_ip: Optional[str] = None  # IP address of TCP/IP connection
    tcpip_port: int = 5555  # Port of TCP/IP connection
    original_device_type: Optional[str] = None  # "usb" or "tcpip" before TCP/IP switch


__all__ = [
    "ClientConfig",
    "ClientState",
]
