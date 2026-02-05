"""
scrcpy_py_ddlx/core/decoder/exceptions.py

Exception classes for decoder errors.

This module defines the exception hierarchy used by the video and audio
decoders in the scrcpy-py-ddlx implementation.
"""


__all__ = [
    'DecoderError',
    'CodecNotSupportedError',
    'DecoderInitializationError',
    'DecodeError'
]


class DecoderError(Exception):
    """Base exception for decoder errors."""
    pass


class CodecNotSupportedError(DecoderError):
    """Raised when a codec is not supported."""
    pass


class DecoderInitializationError(DecoderError):
    """Raised when decoder initialization fails."""
    pass


class DecodeError(DecoderError):
    """Raised when packet decoding fails."""
    pass
