"""
scrcpy_py_ddlx/core/decoder/audio.py

Audio decoder for scrcpy audio streams using PyAV.

This module has been moved to scrcpy_py_ddlx.core.audio.decoder.
This file is kept for backward compatibility.

Deprecated:
    Import AudioDecoder from scrcpy_py_ddlx.core.audio.decoder instead.
"""

import warnings


def __getattr__(name):
    """Lazy import to avoid circular dependency."""
    if name == 'AudioDecoder':
        from scrcpy_py_ddlx.core.audio.decoder import AudioDecoder
        # Emit deprecation warning only when actually accessing the class
        warnings.warn(
            "Importing AudioDecoder from scrcpy_py_ddlx.core.decoder.audio is deprecated. "
            "Please import from scrcpy_py_ddlx.core.audio.decoder instead.",
            DeprecationWarning,
            stacklevel=2
        )
        return AudioDecoder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ['AudioDecoder']
