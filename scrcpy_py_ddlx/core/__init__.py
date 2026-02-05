"""
scrcpy_py_ddlx Core Module

This module provides core functionality for the scrcpy client implementation:
- ADB connection management
- Socket communication layer
- Device connection handling
- Protocol definitions and constants
- Stream parsing and packet handling
- Video decoding with H.264/H.265/AV1 support
- Control message serialization

Based on scrcpy (Screen Copy) from Genymobile
https://github.com/Genymobile/scrcpy
"""

# ============================================================================
# ADB Module (Agent 1)
# ============================================================================
from .adb import (
    ADBManager,
    ADBDevice,
    ADBDeviceType,
    ADBDeviceState,
    ADBTunnel,
    ADBError,
    ADBCommandError,
    ADBDeviceNotFoundError,
    ADBConnectionError,
)

# ============================================================================
# Socket Module (Agent 1)
# ============================================================================
from .socket import (
    ScrcpySocket,
    VideoSocket,
    AudioSocket,
    ControlSocket,
    SocketManager,
    SocketConfig,
    SocketState,
    SocketType,
    SocketError,
    SocketConnectionError,
    SocketReadError,
    SocketWriteError,
)

# ============================================================================
# Protocol Module (Agent 2)
# ============================================================================
from .protocol import (
    # Codec IDs
    CodecId,
    codec_id_to_string,
    codec_id_from_string,
    # Packet flags
    PACKET_FLAG_CONFIG,
    PACKET_FLAG_KEY_FRAME,
    PACKET_PTS_MASK,
    PACKET_HEADER_SIZE,
    # Message types
    ControlMessageType,
    DeviceMessageType,
    # Event types
    AndroidKeyEventAction,
    AndroidMotionEventAction,
    AndroidMotionEventButtons,
    AndroidMetaState,
    # Special values
    POINTER_ID_MOUSE,
    POINTER_ID_GENERIC_FINGER,
    POINTER_ID_VIRTUAL_FINGER,
    # Utilities
    is_config_packet,
    is_key_frame,
    extract_pts,
    pts_flags_to_string,
    CopyKey,
)

# ============================================================================
# Stream Module (Agent 2)
# ============================================================================
from .stream import (
    PacketHeader,
    VideoPacket,
    PacketMerger,
    StreamParser,
    DataBuffer,
    parse_h264_nalu_type,
    parse_h265_nalu_type,
)

# ============================================================================
# Decoder Module (Agent 2)
# ============================================================================
from .decoder import (
    VideoDecoder,
    SimpleDecoder,
    DelayBuffer,
    DecoderError,
    CodecNotSupportedError,
    DecoderInitializationError,
    DecodeError,
    decode_packet,
)

# ============================================================================
# Hardware Decoder Module (GPU Acceleration)
# ============================================================================
try:
    from .hw_decoder import (
        HWAccelConfig,
        HWDeviceType,
        create_hw_codec_context,
        transfer_hw_frame,
        list_available_hw_decoders,
        print_hw_decoder_info,
        HWDecoderNotFoundError,
        HWDecoderInitializationError,
    )
    _hw_accel_available = True
except ImportError:
    _hw_accel_available = False

# ============================================================================
# Control Module (Agent 3)
# ============================================================================
from .control import (
    ControlMessage,
    ControlMessageQueue,
)

# ============================================================================
# Streaming Demuxer Module (NEW - replaces buffer-based approach)
# ============================================================================
try:
    from .demuxer import (
        StreamingDemuxerBase,
        StreamingVideoDemuxer,
        StreamingDemuxerError,
        IncompleteReadError,
        create_streaming_video_demuxer,
        # Keep old classes for backward compatibility
        VideoDemuxer,
        BaseDemuxer,
        DemuxerStoppedError,
        create_video_demuxer,
    )
    _streaming_demuxer_available = True
except ImportError:
    _streaming_demuxer_available = False

# ============================================================================
# Audio Module (NEW - reorganized for extensibility)
# ============================================================================
try:
    from .audio import (
        AudioDecoder,
        AudioDemuxer,
        StreamingAudioDemuxer,
        create_streaming_audio_demuxer,
        PTSComparator,
        AudioDelayAdjuster,
    )
    _audio_available = True
except ImportError:
    _audio_available = False

__all__ = [
    # ADB Module
    "ADBManager",
    "ADBDevice",
    "ADBDeviceType",
    "ADBDeviceState",
    "ADBTunnel",
    "ADBError",
    "ADBCommandError",
    "ADBDeviceNotFoundError",
    "ADBConnectionError",
    # Socket Module
    "ScrcpySocket",
    "VideoSocket",
    "AudioSocket",
    "ControlSocket",
    "SocketManager",
    "SocketConfig",
    "SocketState",
    "SocketType",
    "SocketError",
    "SocketConnectionError",
    "SocketReadError",
    "SocketWriteError",
    # Protocol Module
    "CodecId",
    "codec_id_to_string",
    "codec_id_from_string",
    "PACKET_FLAG_CONFIG",
    "PACKET_FLAG_KEY_FRAME",
    "PACKET_PTS_MASK",
    "PACKET_HEADER_SIZE",
    "ControlMessageType",
    "DeviceMessageType",
    "AndroidKeyEventAction",
    "AndroidMotionEventAction",
    "AndroidMotionEventButtons",
    "AndroidMetaState",
    "POINTER_ID_MOUSE",
    "POINTER_ID_GENERIC_FINGER",
    "POINTER_ID_VIRTUAL_FINGER",
    "is_config_packet",
    "is_key_frame",
    "extract_pts",
    "pts_flags_to_string",
    "CopyKey",
    # Stream Module
    "PacketHeader",
    "VideoPacket",
    "PacketMerger",
    "StreamParser",
    "DataBuffer",
    "parse_h264_nalu_type",
    "parse_h265_nalu_type",
    # Decoder Module
    "VideoDecoder",
    "SimpleDecoder",
    "DelayBuffer",
    "DecoderError",
    "CodecNotSupportedError",
    "DecoderInitializationError",
    "DecodeError",
    "decode_packet",
    # Hardware Decoder Module (conditionally exported)
    "HWAccelConfig",
    "HWDeviceType",
    "create_hw_codec_context",
    "transfer_hw_frame",
    "list_available_hw_decoders",
    "print_hw_decoder_info",
    "HWDecoderNotFoundError",
    "HWDecoderInitializationError",
    # Control Module
    "ControlMessage",
    "ControlMessageQueue",
    # Streaming Demuxer Module (NEW)
    "StreamingDemuxerBase",
    "StreamingVideoDemuxer",
    "StreamingDemuxerError",
    "IncompleteReadError",
    "create_streaming_video_demuxer",
    # Old Demuxer (kept for backward compatibility)
    "VideoDemuxer",
    "BaseDemuxer",
    "DemuxerStoppedError",
    "create_video_demuxer",
    # Audio Module (NEW)
    "AudioDecoder",
    "AudioDemuxer",
    "StreamingAudioDemuxer",
    "create_streaming_audio_demuxer",
    "PTSComparator",
    "AudioDelayAdjuster",
]

__version__ = "0.2.0"
__author__ = "AutoGLM"
