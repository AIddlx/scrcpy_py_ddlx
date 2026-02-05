"""
Audio demuxer implementations.

This module has been moved to scrcpy_py_ddlx.core.audio.demuxer.
This file is kept for backward compatibility.

Deprecated:
    Import AudioDemuxer and StreamingAudioDemuxer from
    scrcpy_py_ddlx.core.audio.demuxer instead.
"""

import warnings


def __getattr__(name):
    """Lazy import to avoid circular dependency."""
    if name in ('AudioDemuxer', 'StreamingAudioDemuxer'):
        from scrcpy_py_ddlx.core.audio.demuxer import (
            AudioDemuxer,
            StreamingAudioDemuxer
        )
        # Emit deprecation warning only when actually accessing the classes
        warnings.warn(
            "Importing AudioDemuxer/StreamingAudioDemuxer from "
            "scrcpy_py_ddlx.core.demuxer.audio is deprecated. "
            "Please import from scrcpy_py_ddlx.core.audio.demuxer instead.",
            DeprecationWarning,
            stacklevel=2
        )
        if name == 'AudioDemuxer':
            return AudioDemuxer
        elif name == 'StreamingAudioDemuxer':
            return StreamingAudioDemuxer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ['AudioDemuxer', 'StreamingAudioDemuxer']
