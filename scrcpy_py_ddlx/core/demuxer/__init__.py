"""
Demuxer module for scrcpy video and audio streams.

This module provides demuxer implementations for parsing scrcpy streams.
It maintains backward compatibility with the original single-file design.

Usage:
    >>> from scrcpy_py_ddlx.core.demuxer import VideoDemuxer
    >>> # For audio demuxers, use the new location:
    >>> from scrcpy_py_ddlx.core.audio.demuxer import AudioDemuxer, StreamingAudioDemuxer
    >>> # Or use factory functions:
    >>> from scrcpy_py_ddlx.core.demuxer import create_video_demuxer, create_streaming_video_demuxer
"""

# =============================================================================
# Exceptions
# =============================================================================
from .base import (
    DemuxerError,
    DemuxerStoppedError,
    StreamingDemuxerError,
    IncompleteReadError
)

# =============================================================================
# Base Classes
# =============================================================================
from .base import (
    BaseDemuxer,
    StreamingDemuxerBase,
    DEFAULT_DEMUXER_BUFFER_SIZE,
    DEFAULT_PACKET_QUEUE_SIZE
)

# =============================================================================
# Video Demuxers
# =============================================================================
from .video import VideoDemuxer, StreamingVideoDemuxer

# =============================================================================
# Audio Demuxers - Moved to scrcpy_py_ddlx.core.audio.demuxer
# =============================================================================
# Note: AudioDemuxer and StreamingAudioDemuxer have been moved to
# scrcpy_py_ddlx.core.audio.demuxer to better organize the audio subsystem.
# For backward compatibility, they are re-exported here via lazy import.

def __getattr__(name):
    """Lazy import for backward compatibility with old import paths."""
    if name in ('AudioDemuxer', 'StreamingAudioDemuxer'):
        import warnings
        from scrcpy_py_ddlx.core.audio.demuxer import (
            AudioDemuxer,
            StreamingAudioDemuxer
        )
        warnings.warn(
            f"Importing {name} from scrcpy_py_ddlx.core.demuxer is deprecated. "
            f"Please import from scrcpy_py_ddlx.core.audio.demuxer instead.",
            DeprecationWarning,
            stacklevel=2
        )
        if name == 'AudioDemuxer':
            return AudioDemuxer
        elif name == 'StreamingAudioDemuxer':
            return StreamingAudioDemuxer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# =============================================================================
# Factory Functions
# =============================================================================
from .factory import (
    create_video_demuxer,
    create_audio_demuxer,
    create_streaming_video_demuxer,
    create_streaming_audio_demuxer
)

# =============================================================================
# Public API
# =============================================================================
__all__ = [
    # Exceptions
    'DemuxerError',
    'DemuxerStoppedError',
    'StreamingDemuxerError',
    'IncompleteReadError',

    # Base Classes
    'BaseDemuxer',
    'StreamingDemuxerBase',

    # Constants
    'DEFAULT_DEMUXER_BUFFER_SIZE',
    'DEFAULT_PACKET_QUEUE_SIZE',

    # Video Demuxers
    'VideoDemuxer',
    'StreamingVideoDemuxer',

    # Audio Demuxers (lazy imported for backward compatibility)
    'AudioDemuxer',
    'StreamingAudioDemuxer',

    # Factory Functions
    'create_video_demuxer',
    'create_audio_demuxer',
    'create_streaming_video_demuxer',
    'create_streaming_audio_demuxer',
]
