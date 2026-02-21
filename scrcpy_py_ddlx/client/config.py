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


def _auto_detect_codec() -> str:
    """Auto-detect optimal codec based on device and PC capabilities."""
    try:
        from scrcpy_py_ddlx.client.capability_cache import get_optimal_codec
        return get_optimal_codec()
    except Exception:
        return "h264"  # Fallback


def _auto_detect_codec_for_device(device_serial: str) -> str:
    """Auto-detect optimal codec for a specific device."""
    try:
        from scrcpy_py_ddlx.client.capability_cache import get_optimal_codec
        return get_optimal_codec(device_serial)
    except Exception:
        return "h264"  # Fallback


class ConnectionMode:
    """Connection mode constants."""
    ADB_TUNNEL = "adb_tunnel"  # ADB tunnel mode (default)
    NETWORK = "network"  # Direct network mode (TCP control + UDP media)


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
    bitrate: int = 2500000  # 2.5 Mbps (平衡画质和带宽)
    max_fps: int = 60
    codec: str = "auto"  # "auto", "h264", "h265", or "av1" ("auto" = detect optimal)
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

    # Network connection mode (TCP control + UDP media)
    connection_mode: str = "adb_tunnel"  # "adb_tunnel" or "network"
    control_port: int = 27184  # TCP control port (network mode)
    video_port: int = 27185  # UDP video port (network mode)
    audio_port: int = 27186  # UDP audio port (network mode)
    discovery_port: int = 27183  # UDP discovery port

    # FEC (Forward Error Correction) settings for UDP mode
    fec_enabled: bool = False  # Legacy: Enable FEC for both video and audio
    video_fec_enabled: bool = False  # Enable FEC for video stream only
    audio_fec_enabled: bool = False  # Enable FEC for audio stream only
    fec_group_size: int = 4  # K: number of data packets per group
    fec_parity_count: int = 1  # M: number of parity packets per group
    fec_mode: str = "frame"  # FEC mode: "frame" (K frames per group) or "fragment" (K fragments per group)

    # Bitrate mode: "cbr" (constant) or "vbr" (variable, default)
    bitrate_mode: str = "vbr"

    # I-frame interval in seconds (lower = faster recovery but more bandwidth)
    # Supports floating point values (e.g., 0.5 for half a second)
    i_frame_interval: float = 10.0

    # GPU rendering: use GPU YUV→RGB conversion instead of CPU
    # When True: decoder outputs NV12 format, GPU shader does color conversion
    # When False: decoder outputs RGB format, CPU does color conversion (higher GIL contention)
    # Default: True (recommended for low latency)
    gpu_rendering: bool = True

    # Low latency optimization settings (server-side)
    # Enable MediaCodec low latency mode (Android 11+)
    # Note: May not be supported on all devices, disable if connection fails
    low_latency: bool = False
    # Encoder thread priority: 0=normal, 1=urgent, 2=realtime
    encoder_priority: int = 1
    # Encoder internal buffer frames (0=auto, 1=disable B-frames for lower latency)
    encoder_buffer: int = 0
    # Skip buffered frames to reduce latency (default: enabled)
    # When encoder has multiple frames buffered, only send the newest one
    skip_frames: bool = True

    def resolve_codec(self, device_serial: Optional[str] = None) -> str:
        """
        Resolve codec string, auto-detecting if set to "auto".

        Args:
            device_serial: Device serial for capability detection

        Returns:
            Resolved codec string: "h264", "h265", or "av1"
        """
        if self.codec.lower() == "auto":
            return _auto_detect_codec() if device_serial is None else _auto_detect_codec_for_device(device_serial)
        return self.codec.lower()

    def is_auto_codec(self) -> bool:
        """Check if codec is set to auto-detect."""
        return self.codec.lower() == "auto"

    def is_video_fec_enabled(self) -> bool:
        """Check if video FEC is enabled."""
        return self.video_fec_enabled or (self.fec_enabled and not self.video_fec_enabled and not self.audio_fec_enabled)

    def is_audio_fec_enabled(self) -> bool:
        """Check if audio FEC is enabled."""
        return self.audio_fec_enabled or (self.fec_enabled and not self.video_fec_enabled and not self.audio_fec_enabled)


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

    # Network mode state
    network_mode: bool = False  # Whether using network mode
    video_udp_socket: Optional[socket.socket] = None  # Original UDP video socket (for cleanup)
    audio_udp_socket: Optional[socket.socket] = None  # Original UDP audio socket (for cleanup)
    client_ip: Optional[str] = None  # Client IP for UDP responses

    # UdpPacketReader instances (wrappers for UDP sockets)
    video_socket: Optional[object] = None  # Can be socket or UdpPacketReader
    audio_socket: Optional[object] = None  # Can be socket or UdpPacketReader

    # Device capabilities (received during negotiation)
    capabilities: Optional[object] = None  # DeviceCapabilities object
    selected_video_codec: int = 0  # Selected video codec ID
    selected_audio_codec: int = 0  # Selected audio codec ID


__all__ = [
    "ClientConfig",
    "ClientState",
    "ConnectionMode",
]
