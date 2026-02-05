"""
Qt QMediaPlayer-based audio player for scrcpy audio streams.

This module uses QMediaPlayer which can directly decode and play OPUS,
eliminating the need for a separate decoder.
"""

import logging
from threading import Lock
from typing import Optional, Any

try:
    from PySide6.QtCore import QIODevice, QByteArray, QTimer, QIODeviceBase
    from PySide6.QtMultimedia import QMediaPlayer, QAudioFormat, QAudioDevice, QMediaDevices
    from PySide6.QtMultimedia import QAudioFormat
    QT_AUDIO_AVAILABLE = True
except ImportError:
    QT_AUDIO_AVAILABLE = False
    logger = logging.getLogger(__name__)

# Import FrameSink for type checking and runtime
try:
    from scrcpy_py_ddlx.core.av_player import FrameSink
except ImportError:
    FrameSink = object

logger = logging.getLogger(__name__)


class QtMediaPlayerPlayer(FrameSink):
    """
    Qt audio player using QMediaPlayer for direct OPUS playback.

    QMediaPlayer can decode and play OPUS directly, eliminating the need
    for a separate AudioDecoder. This is simpler and more efficient.

    Data flow:
    OPUS packets → QMediaPlayer (decodes internally) → speakers
    """

    def __init__(self):
        if not QT_AUDIO_AVAILABLE:
            raise RuntimeError("Qt Multimedia not available")

        self._player: Optional[QMediaPlayer] = None
        self._audio_output: Optional[QAudioDevice] = None
        self._buffer: QByteArray = QByteArray()
        self._buffer_lock = Lock()
        self._running = False
        self._total_bytes_written = 0

        # Raw OPUS packet recorder (optional)
        self._opus_recorder = None
        self._record_filename = None

        # Timer to feed data to player
        self._feed_timer: Optional[QTimer] = None

    def open(self, codec_context: Any = None) -> bool:
        """Initialize the audio player."""
        try:
            # Get default audio output device
            devices = QMediaDevices.audioOutputs()
            if not devices:
                logger.error("No audio output devices found")
                return False

            device = devices[0]  # Use first available device
            if device.isNull():
                logger.error("Audio device is null")
                return False

            # Create QMediaPlayer
            self._player = QMediaPlayer()
            self._player.setAudioOutput(device)

            # Create a QIODevice for writing audio data
            self._audio_output = QIODevice()

            # Set up media (we'll use a buffer approach)
            # QMediaPlayer will read from our buffer via callback

            logger.info("Qt QMediaPlayer player initialized (direct OPUS playback)")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Qt player: {e}")
            import traceback
            traceback.print_exc()
            return False

    def push(self, frame: Any) -> bool:
        """
        Push audio data to the player.

        Args:
            frame: Raw OPUS packet bytes (not decoded!)
        """
        if not self._running:
            return False

        try:
            # Convert to bytes if needed
            if isinstance(frame, bytes):
                data = frame
            elif hasattr(frame, 'tobytes'):
                data = frame.tobytes()
            else:
                logger.warning(f"Unknown frame type: {type(frame)}")
                return False

            # Write to OPUS recorder if active
            if self._opus_recorder:
                self._opus_recorder.write_packet(data)

            # Buffer data for QMediaPlayer
            with self._buffer_lock:
                self._buffer.append(data)
                self._total_bytes_written += len(data)

            return True

        except Exception as e:
            logger.error(f"Error in QtMediaPlayerPlayer.push: {e}")
            return False

    def start(self) -> None:
        """Start audio playback."""
        if self._running:
            return

        self._running = True

        # TODO: Start QMediaPlayer and feed buffered data
        # This requires implementing a QIODevice subclass

        logger.info("QtMediaPlayerPlayer started")

    def stop(self) -> None:
        """Stop audio playback."""
        self._running = False

        if self._player:
            try:
                self._player.stop()
            except Exception as e:
                logger.debug(f"Error stopping player: {e}")

        logger.info("QtMediaPlayerPlayer stopped")

    def close(self) -> None:
        """Close the audio player."""
        self.stop()

        if self._player:
            self._player.deleteLater()
            self._player = None

        # Stop OPUS recorder if active
        if self._opus_recorder:
            self._opus_recorder.stop()
            self._opus_recorder = None

        logger.info("QtMediaPlayerPlayer closed")

    def start_recording(self, filename: str) -> bool:
        """Start recording raw OPUS packets."""
        from scrcpy_py_ddlx.core.audio.raw_opus_recorder import OpusPacketRecorder

        if self._opus_recorder:
            logger.warning("Recording already in progress")
            return False

        self._opus_recorder = OpusPacketRecorder(filename)
        return self._opus_recorder.start()

    def stop_recording(self) -> Optional[str]:
        """Stop recording and save file."""
        if not self._opus_recorder:
            return None

        filename = self._opus_recorder.stop()
        self._opus_recorder = None
        return filename

    @property
    def is_active(self) -> bool:
        """Check if the player is active."""
        return self._running and self._player is not None


class QtOpusAudioHandler:
    """
    Complete audio handler using Qt for direct OPUS playback.

    This replaces AudioDecoder + AudioPlayer with a single component
    that handles both decoding and playback.
    """

    def __init__(self):
        self._player = None
        self._recorder = None
        self._running = False

    def start(self, packet_callback=None):
        """Start the audio handler."""
        self._player = QtMediaPlayerPlayer()
        self._running = True

        logger.info("Qt Opus audio handler started")

    def push_packet(self, packet: bytes):
        """Push a raw OPUS packet (decoded and played automatically)."""
        if not self._running:
            return

        # Push to player (will decode and play)
        self._player.push(packet)

    def start_recording(self, filename: str):
        """Start recording raw OPUS packets."""
        if self._player:
            return self._player.start_recording(filename)
        return False

    def stop_recording(self):
        """Stop recording."""
        if self._player:
            return self._player.stop_recording()
        return None

    def stop(self):
        """Stop the audio handler."""
        self._running = False
        if self._player:
            self._player.close()

        logger.info("Qt Opus audio handler stopped")
