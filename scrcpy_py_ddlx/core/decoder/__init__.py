"""
scrcpy_py_ddlx/core/decoder

Video and audio decoder package for scrcpy-py-ddlx.

This package contains the decoder modules split from the original decoder.py:
- delay_buffer: Single-frame delay buffer for minimal latency
- exceptions: Decoder exception hierarchy
- video: Video decoder for H.264, H.265, and AV1 codecs
- audio: Audio decoder for OPUS, AAC, FLAC, and RAW codecs
"""

from .delay_buffer import DelayBuffer
from .exceptions import (
    DecoderError,
    CodecNotSupportedError,
    DecoderInitializationError,
    DecodeError
)
from .video import VideoDecoder, SimpleDecoder, decode_packet

# Import AudioDecoder from its actual location (not the deprecated shim)
from scrcpy_py_ddlx.core.audio.decoder import AudioDecoder


__all__ = [
    # Delay buffer
    'DelayBuffer',

    # Exceptions
    'DecoderError',
    'CodecNotSupportedError',
    'DecoderInitializationError',
    'DecodeError',

    # Video decoder
    'VideoDecoder',
    'SimpleDecoder',
    'decode_packet',

    # Audio decoder
    'AudioDecoder',
]
