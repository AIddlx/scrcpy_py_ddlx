"""
scrcpy_py_ddlx/core/hw_decoder.py

Hardware-accelerated video decoder for scrcpy using PyAV (FFmpeg).

This module provides GPU-accelerated video decoding capabilities using
platform-specific hardware decoders (NVDEC, QSV, VideoToolbox, D3D11VA, VAAPI).

Hardware decoders provide significantly better performance for high-resolution
or high-framerate video streams compared to software decoding.
"""

import logging
import platform
import sys
from enum import Enum
from typing import Optional, Tuple

import av

from .protocol import CodecId, codec_id_to_string


logger = logging.getLogger(__name__)


class HWDeviceType(Enum):
    """Hardware device types for video decoding."""
    NVIDIA = "cuda"          # NVIDIA NVDEC (Windows, Linux)
    INTEL_QSV = "qsv"        # Intel Quick Sync Video (Windows, Linux)
    APPLE = "videotoolbox"   # Apple VideoToolbox (macOS)
    D3D11VA = "d3d11va"      # D3D11VA (Windows)
    VAAPI = "vaapi"          # VAAPI (Linux)
    VDPAU = "vdpau"          # VDPAU (Linux, legacy)
    NONE = "none"            # Software decoding


class HWAccelConfig:
    """Configuration for hardware acceleration."""

    def __init__(
        self,
        device_type: HWDeviceType = HWDeviceType.NONE,
        device_index: int = 0,
        enable_fallback: bool = True
    ):
        """
        Initialize hardware acceleration configuration.

        Args:
            device_type: Hardware device type to use
            device_index: GPU device index (for multi-GPU systems)
            enable_fallback: Automatically fallback to software decoding if HW fails
        """
        self.device_type = device_type
        self.device_index = device_index
        self.enable_fallback = enable_fallback

    @classmethod
    def auto_detect(cls) -> 'HWAccelConfig':
        """
        Auto-detect the best available hardware decoder for the current platform.

        Returns:
            HWAccelConfig with the detected device type
        """
        system = platform.system()
        machine = platform.machine()

        # macOS - always use VideoToolbox for hardware decoding
        if system == "Darwin":
            if cls._is_codec_available("h264_videotoolbox"):
                logger.info("Auto-detected Apple VideoToolbox for hardware decoding")
                return cls(HWDeviceType.APPLE)
            else:
                logger.warning("VideoToolbox not available, will use software decoding")
                return cls(HWDeviceType.NONE)

        # Windows - try D3D11VA first, then NVDEC, then QSV
        elif system == "Windows":
            # Try D3D11VA (most reliable on Windows, works on all GPUs)
            if cls._is_codec_available("h264_d3d11va"):
                logger.info("Auto-detected D3D11VA for hardware decoding")
                return cls(HWDeviceType.D3D11VA)
            # Try NVDEC for NVIDIA GPUs (best performance if available)
            if cls._is_codec_available("h264_nvdec"):
                logger.info("Auto-detected NVIDIA NVDEC for hardware decoding")
                return cls(HWDeviceType.NVIDIA)
            # QSV is last resort due to driver compatibility issues
            if cls._is_codec_available("h264_qsv"):
                logger.info("Auto-detected Intel QSV for hardware decoding (may have compatibility issues)")
                return cls(HWDeviceType.INTEL_QSV)
            logger.warning("No hardware decoder available on Windows, will use software decoding")
            return cls(HWDeviceType.NONE)

        # Linux - try VAAPI first, then NVDEC, then VDPAU
        elif system == "Linux":
            # Try VAAPI (works on Intel/AMD GPUs)
            if cls._is_codec_available("h264_vaapi"):
                logger.info("Auto-detected VAAPI for hardware decoding")
                return cls(HWDeviceType.VAAPI)
            # Try NVDEC for NVIDIA GPUs
            if cls._is_codec_available("h264_nvdec"):
                logger.info("Auto-detected NVIDIA NVDEC for hardware decoding")
                return cls(HWDeviceType.NVIDIA)
            # Try Intel QSV
            if cls._is_codec_available("h264_qsv"):
                logger.info("Auto-detected Intel QSV for hardware decoding")
                return cls(HWDeviceType.INTEL_QSV)
            # Try VDPAU (legacy)
            if cls._is_codec_available("h264_vdpau"):
                logger.info("Auto-detected VDPAU for hardware decoding")
                return cls(HWDeviceType.VDPAU)
            logger.warning("No hardware decoder available on Linux, will use software decoding")
            return cls(HWDeviceType.NONE)

        # Unknown platform
        else:
            logger.warning(f"Unknown platform {system}, will use software decoding")
            return cls(HWDeviceType.NONE)

    @staticmethod
    def _is_codec_available(codec_name: str) -> bool:
        """
        Check if a codec is available in FFmpeg.

        Args:
            codec_name: Name of the codec to check

        Returns:
            True if codec is available, False otherwise
        """
        try:
            av.CodecContext.create(codec_name, 'r')
            return True
        except (ValueError, av.error.DecoderNotFoundError, av.error.FFmpegError):
            return False


def get_hw_decoder_name(codec_id: int, device_type: HWDeviceType) -> Optional[str]:
    """
    Get the hardware decoder name for a given codec and device type.

    Args:
        codec_id: Codec ID (CodecId.H264, CodecId.H265, or CodecId.AV1)
        device_type: Hardware device type

    Returns:
        Decoder name string, or None if not supported
    """
    # Map codec ID to base codec name
    if codec_id == CodecId.H264:
        base = "h264"
    elif codec_id == CodecId.H265:
        base = "hevc"
    elif codec_id == CodecId.AV1:
        base = "av1"
    else:
        return None

    # Map device type to decoder suffix
    hw_suffixes = {
        HWDeviceType.NVIDIA: "nvdec",
        HWDeviceType.INTEL_QSV: "qsv",
        HWDeviceType.APPLE: "videotoolbox",
        HWDeviceType.D3D11VA: "d3d11va",
        HWDeviceType.VAAPI: "vaapi",
        HWDeviceType.VDPAU: "vdpau",
    }

    suffix = hw_suffixes.get(device_type)
    if suffix is None:
        return None

    return f"{base}_{suffix}"


def get_hw_pixel_format(device_type: HWDeviceType) -> Optional[str]:
    """
    Get the pixel format for hardware decoding.

    Hardware decoders output GPU-specific pixel formats that need to be
    transferred back to CPU for processing.

    Args:
        device_type: Hardware device type

    Returns:
        Pixel format string, or None if software decoding
    """
    hw_formats = {
        HWDeviceType.NVIDIA: "cuda",
        HWDeviceType.INTEL_QSV: "qsv",
        HWDeviceType.APPLE: "videotoolbox",
        HWDeviceType.D3D11VA: "d3d11",
        HWDeviceType.VAAPI: "vaapi",
        HWDeviceType.VDPAU: "vdpau",
    }
    return hw_formats.get(device_type)


class HWDecoderNotFoundError(Exception):
    """Raised when hardware decoder is not available."""
    pass


class HWDecoderInitializationError(Exception):
    """Raised when hardware decoder initialization fails."""
    pass


def av_hwdevice_ctx_create(device_type: str, device_index: int = 0) -> Optional[object]:
    """
    Create a hardware device context for FFmpeg.

    This is needed for some hardware decoders like QSV to work properly.

    Args:
        device_type: Device type ('qsv', 'cuda', 'd3d11va', etc.)
        device_index: Device index (for multi-GPU systems)

    Returns:
        Hardware device context object, or None if failed
    """
    try:
        # Try to use PyAV's hardware device context creation
        # PyAV 13+ has support for hardware device contexts
        import ctypes

        # Get the FFmpeg library
        try:
            av_lib = av.SDK
        except AttributeError:
            # Older PyAV versions
            av_lib = av.ffmpeg

        # Try to create device context through av.hwdevice_ctx_create
        # This is a low-level FFmpeg function
        device = None
        if hasattr(av_lib, 'hwdevice_ctx_create'):
            device = av_lib.hwdevice_ctx_create(device_type, device_index)

        return device

    except Exception as e:
        logger.debug(f"Hardware device context creation failed: {e}")
        return None


def create_hw_codec_context(
    width: int,
    height: int,
    codec_id: int,
    hw_config: HWAccelConfig
) -> Optional[av.CodecContext]:
    """
    Create a hardware-accelerated codec context.

    Args:
        width: Video frame width in pixels
        height: Video frame height in pixels
        codec_id: Codec ID (CodecId.H264, CodecId.H265, or CodecId.AV1)
        hw_config: Hardware acceleration configuration

    Returns:
        av.CodecContext configured for hardware decoding, or None if failed

    Raises:
        HWDecoderNotFoundError: If hardware decoder is not available
        HWDecoderInitializationError: If initialization fails
    """
    if hw_config.device_type == HWDeviceType.NONE:
        return None

    # Get the hardware decoder name
    decoder_name = get_hw_decoder_name(codec_id, hw_config.device_type)
    if decoder_name is None:
        raise HWDecoderNotFoundError(
            f"No hardware decoder for {codec_id_to_string(codec_id)} "
            f"with {hw_config.device_type.value}"
        )

    try:
        # Create the decoder context
        codec_context = av.CodecContext.create(decoder_name, 'r')

        # Set basic parameters
        codec_context.width = width
        codec_context.height = height

        # Enable low-delay mode for real-time streaming
        codec_context.options = {
            'threads': '1',
        }

        # Hardware decoder-specific configuration
        if hw_config.device_type == HWDeviceType.INTEL_QSV:
            # QSV requires explicit device context creation on some systems
            # Create a QSV device context
            try:
                device = av_hwdevice_ctx_create('qsv', device_index=hw_config.device_index)
                if device is not None:
                    codec_context.hw_device_ctx = device
                    logger.debug("QSV device context created successfully")
            except Exception as e:
                logger.warning(f"QSV device context creation failed (continuing anyway): {e}")

        elif hw_config.device_type == HWDeviceType.VAAPI:
            # VAAPI requires explicit device selection
            codec_context.options['device'] = f'/dev/dri/renderD128'

        elif hw_config.device_type == HWDeviceType.D3D11VA:
            # D3D11VA may need device index specification
            codec_context.options['device'] = f'{hw_config.device_index}'

        logger.info(
            f"Created hardware decoder '{decoder_name}' for "
            f"{width}x{height} {codec_id_to_string(codec_id)}"
        )

        return codec_context

    except ValueError as e:
        raise HWDecoderNotFoundError(
            f"Hardware decoder '{decoder_name}' not available: {e}"
        )
    except Exception as e:
        raise HWDecoderInitializationError(
            f"Failed to initialize hardware decoder '{decoder_name}': {e}"
        )


def transfer_hw_frame(frame: av.VideoFrame, target_format: str = 'rgb24') -> av.VideoFrame:
    """
    Transfer a hardware frame to CPU memory.

    Hardware decoders output frames in GPU memory. This function transfers
    the frame to CPU memory and converts it to the desired format.

    Args:
        frame: Video frame from hardware decoder
        target_format: Target pixel format (default: rgb24)

    Returns:
        Video frame in CPU memory with target format

    Raises:
        RuntimeError: If frame transfer fails
    """
    try:
        # Check if frame is in hardware format
        if frame.format.name in ['cuda', 'qsv', 'videotoolbox', 'd3d11', 'vaapi', 'vdpau']:
            # Reformat to target format (this implicitly transfers to CPU)
            cpu_frame = frame.reformat(format=target_format)
            return cpu_frame
        else:
            # Already in CPU format, just convert if needed
            if frame.format.name != target_format:
                return frame.reformat(format=target_format)
            return frame

    except Exception as e:
        raise RuntimeError(f"Failed to transfer hardware frame to CPU: {e}")


def list_available_hw_decoders() -> dict:
    """
    List all available hardware decoders on the current system.

    Returns:
        Dictionary mapping codec names to availability status
    """
    # Common hardware decoder names to check
    hw_decoders = [
        # NVIDIA NVDEC
        "h264_nvdec", "hevc_nvdec", "av1_nvdec",
        # Intel QSV
        "h264_qsv", "hevc_qsv", "av1_qsv",
        # Apple VideoToolbox
        "h264_videotoolbox", "hevc_videotoolbox",
        # D3D11VA (Windows)
        "h264_d3d11va", "hevc_d3d11va", "av1_d3d11va",
        # VAAPI (Linux)
        "h264_vaapi", "hevc_vaapi", "av1_vaapi",
        # VDPAU (Linux, legacy)
        "h264_vdpau", "hevc_vdpau",
    ]

    available = {}
    for decoder in hw_decoders:
        available[decoder] = HWAccelConfig._is_codec_available(decoder)

    return available


def print_hw_decoder_info():
    """Print information about available hardware decoders."""
    logger.info("=== Hardware Decoder Information ===")
    logger.info(f"Platform: {platform.system()} {platform.machine()}")
    logger.info(f"Python: {sys.version}")
    logger.info(f"PyAV: {av.__version__}")

    decoders = list_available_hw_decoders()

    available_count = sum(1 for v in decoders.values() if v)
    total_count = len(decoders)

    logger.info(f"Available decoders: {available_count}/{total_count}")

    for decoder, available in decoders.items():
        status = "✓" if available else "✗"
        logger.info(f"  {status} {decoder}")

    # Auto-detect best decoder
    config = HWAccelConfig.auto_detect()
    logger.info(f"Auto-detected decoder: {config.device_type.value}")

    logger.info("=====================================")
