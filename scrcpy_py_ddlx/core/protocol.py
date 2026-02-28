"""
scrcpy_py_ddlx/core/protocol.py

Protocol constants and enumerations for scrcpy client.

This module defines all protocol-level constants used for communication
with the scrcpy server, including codec IDs, message types, and packet flags.
"""

from enum import IntEnum
from typing import Final


# ============================================================================
# Codec IDs (4-byte ASCII identifiers)
# ============================================================================

class CodecId(IntEnum):
    """Video and audio codec identifiers used in scrcpy protocol."""
    H264 = 0x68323634  # "h264" in ASCII
    H265 = 0x68323635  # "h265" in ASCII
    AV1 = 0x00617631   # "av1" in ASCII
    OPUS = 0x6f707573  # "opus" in ASCII
    AAC = 0x00616163   # "aac" in ASCII
    FLAC = 0x666c6163  # "flac" in ASCII
    RAW = 0x00726177   # "raw" in ASCII


def codec_id_to_string(codec_id: int) -> str:
    """Convert a numeric codec ID to its string representation."""
    try:
        codec_bytes = bytes([
            (codec_id >> 24) & 0xFF,
            (codec_id >> 16) & 0xFF,
            (codec_id >> 8) & 0xFF,
            codec_id & 0xFF
        ])
        # Strip null bytes from both ends and decode
        return codec_bytes.strip(b'\x00').decode('ascii')
    except UnicodeDecodeError:
        return f"unknown(0x{codec_id:08x})"


def codec_id_from_string(codec_str: str) -> int:
    """Convert a codec string to its numeric ID."""
    # Pad on the left with null bytes (big-endian format)
    padded = codec_str.rjust(4, '\x00')[:4]
    return (ord(padded[0]) << 24) | (ord(padded[1]) << 16) | \
           (ord(padded[2]) << 8) | ord(padded[3])


# ============================================================================
# Packet Flags
# ============================================================================

# Packet header is 12 bytes:
# [8 bytes: PTS + flags][4 bytes: data size]
#
# PTS field (64-bit) contains flags in the most significant bits:
# byte 7   byte 6   byte 5   byte 4   byte 3   byte 2   byte 1   byte 0
# CK...... ........ ........ ........ ........ ........ ........ ........
# ^^<------------------------------------------------------------------->
# ||                                PTS (62 bits)
# | `- key frame (bit 62)
#  `-- config packet (bit 63)

PACKET_FLAG_CONFIG: Final[int] = 1 << 63    # Bit 63: Configuration packet
PACKET_FLAG_KEY_FRAME: Final[int] = 1 << 62 # Bit 62: Key frame
PACKET_PTS_MASK: Final[int] = PACKET_FLAG_KEY_FRAME - 1  # Lower 62 bits for PTS

PACKET_HEADER_SIZE: Final[int] = 12  # Size of packet meta header in bytes


# ============================================================================
# Control Message Types (Client -> Server)
# ============================================================================

class ControlMessageType(IntEnum):
    """Types of control messages sent from client to server."""
    INJECT_KEYCODE = 0
    INJECT_TEXT = 1
    INJECT_TOUCH_EVENT = 2
    INJECT_SCROLL_EVENT = 3
    BACK_OR_SCREEN_ON = 4
    EXPAND_NOTIFICATION_PANEL = 5
    EXPAND_SETTINGS_PANEL = 6
    COLLAPSE_PANELS = 7
    GET_CLIPBOARD = 8
    SET_CLIPBOARD = 9
    SET_DISPLAY_POWER = 10
    ROTATE_DEVICE = 11
    UHID_CREATE = 12
    UHID_INPUT = 13
    UHID_DESTROY = 14
    OPEN_HARD_KEYBOARD_SETTINGS = 15
    START_APP = 16
    RESET_VIDEO = 17
    SCREENSHOT = 18
    GET_APP_LIST = 19  # Request list of installed applications
    # Media stream control (network mode)
    REQUEST_VIDEO_FRAME = 20  # Request single video frame (for screenshot)
    START_VIDEO = 21         # Start video stream
    STOP_VIDEO = 22          # Stop video stream (encoder standby)
    START_AUDIO = 23         # Start audio stream
    STOP_AUDIO = 24          # Stop audio stream (encoder standby)
    # Heartbeat (TCP control channel keepalive)
    PING = 25                # Heartbeat request (client -> server)
    # File transfer (independent TCP file channel)
    OPEN_FILE_CHANNEL = 26   # Request to open file channel


# ============================================================================
# Device Message Types (Server -> Client)
# ============================================================================

class DeviceMessageType(IntEnum):
    """Types of device messages sent from server to client."""
    CLIPBOARD = 0
    ACK_CLIPBOARD = 1
    UHID_OUTPUT = 2
    APP_LIST = 3  # List of installed applications
    SCREENSHOT = 4  # Screenshot image data (JPEG)
    PONG = 5      # Heartbeat response (server -> client)
    FILE_CHANNEL_INFO = 6  # File channel port info (server -> client)


# ============================================================================
# Android Key Event Actions
# ============================================================================

class AndroidKeyEventAction(IntEnum):
    """Android key event action codes."""
    DOWN = 0
    UP = 1
    MULTIPLE = 2


# ============================================================================
# Android Motion Event Actions
# ============================================================================

class AndroidMotionEventAction(IntEnum):
    """Android motion event action codes."""
    DOWN = 0
    UP = 1
    MOVE = 2
    CANCEL = 3
    OUTSIDE = 4
    POINTER_DOWN = 5
    POINTER_UP = 6
    HOVER_MOVE = 7
    SCROLL = 8
    HOVER_ENTER = 9
    HOVER_EXIT = 10
    BUTTON_PRESS = 11
    BUTTON_RELEASE = 12


# ============================================================================
# Android Motion Event Buttons
# ============================================================================

class AndroidMotionEventButtons(IntEnum):
    """Android motion event button flags."""
    PRIMARY = 1 << 0
    SECONDARY = 1 << 1
    TERTIARY = 1 << 2
    BACK = 1 << 3
    FORWARD = 1 << 4
    STYLUS_PRIMARY = 1 << 5
    STYLUS_SECONDARY = 1 << 6


# ============================================================================
# Android Meta State
# ============================================================================

class AndroidMetaState(IntEnum):
    """Android key event meta state modifiers."""
    ALT_LEFT = 0x02
    ALT_RIGHT = 0x04
    SHIFT_LEFT = 0x10
    SHIFT_RIGHT = 0x20
    SYM = 0x40
    FUNCTION = 0x80
    CAPS_LOCK = 0x100
    NUM_LOCK = 0x200
    SCROLL_LOCK = 0x400


# ============================================================================
# Special Pointer IDs
# ============================================================================

POINTER_ID_MOUSE: Final[int] = -1  # Special ID for mouse events
POINTER_ID_GENERIC_FINGER: Final[int] = -2  # Special ID for generic touch
POINTER_ID_VIRTUAL_FINGER: Final[int] = -3  # Special ID for pinch-to-zoom


# ============================================================================
# Message Size Limits
# ============================================================================

CONTROL_MSG_MAX_SIZE: Final[int] = 1 << 18  # 256KB max control message size
CONTROL_MSG_INJECT_TEXT_MAX_LENGTH: Final[int] = 300
CONTROL_MSG_CLIPBOARD_TEXT_MAX_LENGTH: Final[int] = CONTROL_MSG_MAX_SIZE - 14

DEVICE_MSG_MAX_SIZE: Final[int] = 1 << 18  # 256KB max device message size
DEVICE_MSG_TEXT_MAX_LENGTH: Final[int] = DEVICE_MSG_MAX_SIZE - 5

# Device name field length (from scrcpy protocol)
DEVICE_NAME_FIELD_LENGTH: Final[int] = 64  # Device name is 64 bytes


# ============================================================================
# Control Message Encoding Constants (from official scrcpy)
# ============================================================================

# Pressure multiplier: 0.0-1.0 range encoded as 0-65536 (uint16 fixed point)
# CRITICAL: Must be 65536, not 65535 (per official scrcpy spec)
PRESSURE_MULTIPLIER: Final[int] = 65536

# Scroll multiplier: -1.0 to 1.0 range encoded as int16 with multiplier 32768
# CRITICAL: Must be 32768, not 32767 (per official scrcpy spec)
# Result range: [-32768, 32767] (clamped to int16 range)
SCROLL_MULTIPLIER: Final[int] = 32768

# Control queue size: 64 slots (60 droppable + 4 non-droppable)
# CRITICAL: Must be 64 (per official scrcpy spec)
CONTROL_QUEUE_SIZE: Final[int] = 64
DROPPABLE_CONTROL_MESSAGES: Final[int] = 60  # Message types 0-7, 14-17
NON_DROPPABLE_CONTROL_MESSAGES: Final[int] = 4  # Message types 8-13


# ============================================================================
# Copy Key Types
# ============================================================================

class CopyKey(IntEnum):
    """Clipboard copy key types."""
    NONE = 0
    COPY = 1
    CUT = 2


# ============================================================================
# Utilities
# ============================================================================

def is_config_packet(pts_flags: int) -> bool:
    """Check if packet is a configuration packet."""
    return bool(pts_flags & PACKET_FLAG_CONFIG)


def is_key_frame(pts_flags: int) -> bool:
    """Check if packet is a key frame."""
    return bool(pts_flags & PACKET_FLAG_KEY_FRAME)


def extract_pts(pts_flags: int) -> int:
    """Extract PTS from pts_flags field."""
    return pts_flags & PACKET_PTS_MASK


def pts_flags_to_string(pts_flags: int) -> str:
    """Convert pts_flags to human-readable string."""
    flags = []
    if is_config_packet(pts_flags):
        flags.append("CONFIG")
    if is_key_frame(pts_flags):
        flags.append("KEY_FRAME")

    pts = extract_pts(pts_flags)
    flag_str = "|".join(flags) if flags else "NONE"
    return f"PTS={pts}, Flags={flag_str}"


# ============================================================================
# UDP Protocol Constants (for network mode)
# ============================================================================

# UDP Header format: [seq: 4B] [timestamp: 8B] [flags: 4B] [send_time_ns: 8B] = 24 bytes
UDP_HEADER_SIZE: Final[int] = 24

# UDP Header FLAGS bit definitions (must match UdpMediaSender.java)
# These are DIFFERENT from scrcpy packet flags (PACKET_FLAG_*)
UDP_FLAG_KEY_FRAME: Final[int] = 1 << 0      # Bit 0: Key frame
UDP_FLAG_CONFIG: Final[int] = 1 << 1         # Bit 1: Config packet
UDP_FLAG_FEC_DATA: Final[int] = 1 << 2       # Bit 2: FEC data packet (reserved)
UDP_FLAG_FEC_PARITY: Final[int] = 1 << 3     # Bit 3: FEC parity packet (reserved)
UDP_FLAG_FRAGMENTED: Final[int] = 1 << 31    # Bit 31: Fragmented packet

# UDP packet type detection helpers
def get_udp_packet_type(flags: int) -> str:
    """Determine UDP packet type from flags field."""
    if flags & UDP_FLAG_FRAGMENTED:
        return "FRAGMENTED"
    if flags & UDP_FLAG_FEC_PARITY:
        return "FEC_PARITY"
    if flags & UDP_FLAG_FEC_DATA:
        return "FEC_DATA"
    return "NORMAL"


# ============================================================================
# FEC Configuration (reserved for future implementation)
# ============================================================================

# Default FEC parameters
DEFAULT_FEC_GROUP_SIZE: Final[int] = 4       # K: data packets per group
DEFAULT_FEC_PARITY_COUNT: Final[int] = 1     # M: parity packets per group

# FEC Group Header size: [group_id: 2B] [idx: 1B] [total: 1B] = 4 bytes
FEC_GROUP_HEADER_SIZE: Final[int] = 4


# ============================================================================
# PLI (Picture Loss Indication) Configuration
# ============================================================================

# PLI uses existing RESET_VIDEO control message (type 17 = 0x11)
# No new message type needed for basic PLI support

# PLI trigger thresholds
DEFAULT_PLI_THRESHOLD: Final[int] = 10       # Consecutive drops before PLI
DEFAULT_PLI_COOLDOWN: Final[float] = 1.0     # Seconds between PLI requests


# ============================================================================
# UDP Statistics
# ============================================================================

class UdpPacketType(IntEnum):
    """UDP packet type enumeration for statistics."""
    NORMAL = 0
    FRAGMENTED = 1
    FEC_DATA = 2
    FEC_PARITY = 3


# ============================================================================
# Authentication Message Types (v1.4)
# ============================================================================

# Authentication flow:
# 1. Server sends CHALLENGE (32 bytes random) to client
# 2. Client calculates HMAC-SHA256(key, challenge) and sends RESPONSE
# 3. Server verifies and sends AUTH_RESULT (success/failure)

TYPE_CHALLENGE: Final[int] = 0xF0      # Server -> Client: 32-byte random challenge
TYPE_RESPONSE: Final[int] = 0xF1       # Client -> Server: 32-byte HMAC-SHA256 response
TYPE_AUTH_RESULT: Final[int] = 0xF2    # Server -> Client: Authentication result

# Authentication constants
AUTH_KEY_SIZE: Final[int] = 32         # 256 bits
AUTH_CHALLENGE_SIZE: Final[int] = 32   # 256 bits
AUTH_RESPONSE_SIZE: Final[int] = 32    # 256 bits (HMAC-SHA256 output)
