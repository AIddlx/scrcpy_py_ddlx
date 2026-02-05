"""
Audio subsystem for scrcpy-py-ddlx.

This package provides audio decoding, demuxing, synchronization, and playback
functionality for scrcpy audio streams.
"""

from scrcpy_py_ddlx.core.audio.decoder import AudioDecoder
from scrcpy_py_ddlx.core.audio.demuxer import AudioDemuxer, StreamingAudioDemuxer
from scrcpy_py_ddlx.core.audio.sync import PTSComparator, AudioDelayAdjuster

# Audio recorder
from scrcpy_py_ddlx.core.audio.recorder import AudioRecorder, TeeAudioRecorder

# Import codec classes
from scrcpy_py_ddlx.core.audio.codecs import (
    AudioCodecBase,
    OpusDecoder,
    AACDecoder,
    FLACDecoder,
    RAWDecoder,
    create_audio_decoder
)

# Import audio player implementations
# Priority: sounddevice (better quality) > QtPushAudioPlayer (no extra deps)

# SoundDevice (best performance, callback-based)
SOUNDDEVICE_AVAILABLE = False
SoundDevicePlayer = None
try:
    from scrcpy_py_ddlx.core.audio.sounddevice_player import SoundDevicePlayer
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    pass

# Qt push mode (fallback - no extra dependencies)
QT_PUSH_AVAILABLE = False
QtPushAudioPlayer = None
try:
    from scrcpy_py_ddlx.core.audio.qt_push_player import QtPushAudioPlayer
    QT_PUSH_AVAILABLE = True
except ImportError:
    pass

# Default player selection:
# 1. SoundDevicePlayer (best quality, callback-based)
# 2. QtPushAudioPlayer (pure Qt, no extra deps)
if SOUNDDEVICE_AVAILABLE:
    AudioPlayer = SoundDevicePlayer
elif QT_PUSH_AVAILABLE:
    AudioPlayer = QtPushAudioPlayer
else:
    AudioPlayer = None

# For backward compatibility, provide QtAudioPlayer alias
QtAudioPlayer = AudioPlayer

__all__ = [
    # Decoder
    'AudioDecoder',

    # Demuxer
    'AudioDemuxer',
    'StreamingAudioDemuxer',

    # Sync
    'PTSComparator',
    'AudioDelayAdjuster',

    # Recorder
    'AudioRecorder',
    'TeeAudioRecorder',

    # Player implementations
    'SoundDevicePlayer',      # Best quality (default)
    'QtPushAudioPlayer',      # Pure Qt fallback
    'AudioPlayer',            # Default (SoundDevicePlayer)
    'QtAudioPlayer',          # Backward compatibility alias

    # Codecs
    'AudioCodecBase',
    'OpusDecoder',
    'AACDecoder',
    'FLACDecoder',
    'RAWDecoder',
    'create_audio_decoder',

    # Availability flags
    'SOUNDDEVICE_AVAILABLE',
    'QT_PUSH_AVAILABLE',
    'QT_AUDIO_AVAILABLE',  # For backward compatibility (alias to QT_PUSH_AVAILABLE)
]

# Audio codec constants (for convenience)
RAW = 0
OPUS = 1
AAC = 2
FDK_AAC = 3
FLAC = 4

__all__.extend(['RAW', 'OPUS', 'AAC', 'FDK_AAC', 'FLAC'])

# Set QT_AUDIO_AVAILABLE for backward compatibility
QT_AUDIO_AVAILABLE = QT_PUSH_AVAILABLE
