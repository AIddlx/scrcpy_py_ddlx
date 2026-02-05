"""
scrcpy Server Parameter Builder

This module provides a safe and convenient way to construct scrcpy server parameters
with proper formatting validation.

Based on scrcpy server source code:
- server/src/main/java/com/genymobile/scrcpy/Options.java
- app/src/server.c
"""

import sys
import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

# 设置 UTF-8 编码输出（Windows 兼容）
if sys.platform == "win32":
    import io
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    except:
        pass  # 已经设置过了

logger = logging.getLogger(__name__)


class LogLevel(Enum):
    """scrcpy log levels"""
    VERBOSE = "verbose"
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class VideoCodec(Enum):
    """Video codec options"""
    H264 = "h264"
    H265 = "h265"


class AudioCodec(Enum):
    """Audio codec options"""
    OPUS = "opus"
    RAW = "raw"
    AAC = "aac"


class AudioSource(Enum):
    """Audio source options"""
    OUTPUT = "output"
    MIC = "mic"


class VideoSource(Enum):
    """Video source options"""
    DISPLAY = "display"
    CAMERA = "camera"


@dataclass
class ServerOptions:
    """
    scrcpy Server Options

    Attributes:
        scid: Session client ID (31-bit non-negative integer)
        log_level: Logging level
        video: Enable video streaming
        audio: Enable audio streaming
        control: Enable control channel
        video_codec: Video codec to use
        audio_codec: Audio codec to use
        video_source: Video capture source
        audio_source: Audio capture source
        max_size: Maximum video resolution width (multiple of 8)
        video_bit_rate: Video bitrate in bps
        audio_bit_rate: Audio bitrate in bps
        max_fps: Maximum framerate
        tunnel_forward: Use adb forward instead of reverse
        crop: Crop region (format: "x:y:w:h")
        display_id: Display ID to mirror
        show_touches: Show touch visualizer
        stay_awake: Keep device awake
    """
    scid: int
    log_level: LogLevel = LogLevel.INFO
    video: bool = True
    audio: bool = False
    control: bool = True
    video_codec: VideoCodec = VideoCodec.H264
    audio_codec: AudioCodec = AudioCodec.OPUS
    video_source: VideoSource = VideoSource.DISPLAY
    audio_source: AudioSource = AudioSource.OUTPUT
    max_size: Optional[int] = None
    video_bit_rate: Optional[int] = None
    audio_bit_rate: Optional[int] = None
    max_fps: Optional[float] = None
    tunnel_forward: bool = False
    crop: Optional[str] = None
    display_id: Optional[int] = None
    show_touches: bool = False
    stay_awake: bool = False

    def __post_init__(self):
        """Validate options after initialization"""
        # Validate SCID range
        if self.scid < 0 or self.scid > 0x7FFFFFFF:
            raise ValueError(
                f"scid must be 31-bit non-negative integer (0-0x7FFFFFFF), got: {self.scid}"
            )

        # Validate max_size is multiple of 8
        if self.max_size is not None and self.max_size % 8 != 0:
            logger.warning(
                f"max_size should be multiple of 8, got {self.max_size}. "
                f"Will be adjusted to {self.max_size & ~7}"
            )

    def build_params(self) -> List[str]:
        """
        Build parameter list for scrcpy server

        Returns:
            List of formatted "key=value" parameter strings

        Raises:
            ValueError: If parameter validation fails
        """
        params = []

        # SCID is REQUIRED and MUST be 8-digit hex lowercase
        params.append(f"scid={self.scid:08x}")

        # Log level
        params.append(f"log_level={self.log_level.value}")

        # Boolean parameters - always add control=true for scrcpy 2.0+
        # This ensures the server sends device metadata correctly
        if not self.video:
            params.append("video=false")
        if self.audio:
            params.append("audio=true")
        # Always add control explicitly (required for some server versions)
        if self.control:
            params.append("control=true")

        # Video options
        if self.video_codec != VideoCodec.H264:
            params.append(f"video_codec={self.video_codec.value}")

        if self.video_source != VideoSource.DISPLAY:
            params.append(f"video_source={self.video_source.value}")

        if self.max_size is not None:
            # Server expects multiple of 8
            size = self.max_size & ~7
            params.append(f"max_size={size}")

        if self.video_bit_rate is not None:
            params.append(f"video_bit_rate={self.video_bit_rate}")

        if self.max_fps is not None:
            params.append(f"max_fps={self.max_fps}")

        # Audio options
        if self.audio:
            if self.audio_codec != AudioCodec.OPUS:
                params.append(f"audio_codec={self.audio_codec.value}")

            if self.audio_source != AudioSource.OUTPUT:
                params.append(f"audio_source={self.audio_source.value}")

            if self.audio_bit_rate is not None:
                params.append(f"audio_bit_rate={self.audio_bit_rate}")

        # Tunnel direction
        if self.tunnel_forward:
            params.append("tunnel_forward=true")

        # Crop
        if self.crop:
            params.append(f"crop={self.crop}")

        # Display ID
        if self.display_id is not None:
            params.append(f"display_id={self.display_id}")

        # Other options
        if self.show_touches:
            params.append("show_touches=true")

        if self.stay_awake:
            params.append("stay_awake=true")

        logger.debug(f"Built {len(params)} server parameters")
        return params

    def validate(self) -> bool:
        """
        Validate all parameters

        Returns:
            True if all parameters are valid

        Raises:
            ValueError: If any parameter is invalid
        """
        # Test building params to catch any errors
        try:
            params = self.build_params()

            # Validate each parameter
            for param in params:
                key, value = param.split("=", 1)

                # Special validation for scid
                if key == "scid":
                    if len(value) != 8:
                        raise ValueError(f"scid must be 8 hex digits, got {len(value)}: {value}")
                    try:
                        int(value, 16)
                    except ValueError:
                        raise ValueError(f"scid must be hexadecimal: {value}")

            return True
        except Exception as e:
            raise ValueError(f"Parameter validation failed: {e}")


def create_default_params(scid: int) -> List[str]:
    """
    Create default scrcpy server parameters

    Args:
        scid: Session client ID (31-bit non-negative integer)

    Returns:
        List of formatted parameter strings
    """
    options = ServerOptions(scid=scid)
    return options.build_params()


def create_minimal_params(scid: int) -> List[str]:
    """
    Create minimal scrcpy server parameters (for testing)

    Args:
        scid: Session client ID (31-bit non-negative integer)

    Returns:
        List of formatted parameter strings
    """
    return [
        f"scid={scid:08x}",
        "log_level=info",
    ]


def validate_scid_format(scid_param: str) -> bool:
    """
    Validate scid parameter format

    Args:
        scid_param: scid parameter string (e.g., "scid=12345678")

    Returns:
        True if format is valid

    Raises:
        ValueError: If format is invalid
    """
    if not scid_param.startswith("scid="):
        raise ValueError(f"Invalid scid parameter (must start with 'scid='): {scid_param}")

    value = scid_param.split("=", 1)[1]

    if len(value) != 8:
        raise ValueError(
            f"Invalid scid format (must be 8 hex digits): {value} "
            f"(got {len(value)} digits)"
        )

    try:
        int(value, 16)
    except ValueError:
        raise ValueError(f"Invalid scid format (must be hexadecimal): {value}")

    return True


# Convenience functions for common scenarios
def create_video_only_params(scid: int, max_size: int = 1920, bitrate: int = 8000000) -> List[str]:
    """Create parameters for video-only streaming"""
    options = ServerOptions(
        scid=scid,
        video=True,
        audio=False,
        control=False,
        max_size=max_size,
        video_bit_rate=bitrate
    )
    return options.build_params()


def create_full_params(
    scid: int,
    max_size: int = 1920,
    video_bitrate: int = 8000000,
    audio_bitrate: int = 128000
) -> List[str]:
    """Create parameters for full video+audio+control streaming"""
    options = ServerOptions(
        scid=scid,
        video=True,
        audio=True,
        control=True,
        max_size=max_size,
        video_bit_rate=video_bitrate,
        audio_bit_rate=audio_bitrate
    )
    return options.build_params()


if __name__ == "__main__":
    # Test parameter generation
    import random

    # Generate test SCID
    test_scid = random.randint(0, 0x7FFFFFFF)

    print("=" * 60)
    print("scrcpy Server Parameter Builder - Test")
    print("=" * 60)

    # Test 1: Default parameters
    print("\n1. Default Parameters:")
    options = ServerOptions(scid=test_scid)
    params = options.build_params()
    for p in params:
        print(f"   {p}")

    # Test 2: Minimal parameters
    print("\n2. Minimal Parameters:")
    params = create_minimal_params(test_scid)
    for p in params:
        print(f"   {p}")

    # Test 3: Video only
    print("\n3. Video Only Parameters:")
    params = create_video_only_params(test_scid)
    for p in params:
        print(f"   {p}")

    # Test 4: Full parameters
    print("\n4. Full Parameters:")
    params = create_full_params(test_scid)
    for p in params:
        print(f"   {p}")

    # Test 5: Validation
    print("\n5. Validation Test:")
    try:
        validate_scid_format(f"scid={test_scid:08x}")
        print(f"   ✓ Valid scid format: scid={test_scid:08x}")
    except ValueError as e:
        print(f"   ✗ Validation failed: {e}")

    print("\n" + "=" * 60)
