"""
Qt push-mode audio player for scrcpy audio streams.

This module uses QAudioSink's push mode where we write directly
to the internal QIODevice returned by QAudioSink.start().

This avoids the pull mode (QIODevice subclass) which is broken in PySide6.
"""

import logging
import threading
from typing import Optional, Any

import numpy as np

# Qt imports
try:
    from PySide6.QtCore import QIODevice, QByteArray, QTimer, QCoreApplication
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QAudioDevice, QMediaDevices
    QT_AUDIO_AVAILABLE = True
except ImportError:
    QT_AUDIO_AVAILABLE = False
    QAudioFormat = None
    QAudioSink = None
    QAudioDevice = None
    QMediaDevices = None
    QTimer = None

# Import FrameSink
try:
    from scrcpy_py_ddlx.core.av_player import FrameSink
except ImportError:
    FrameSink = object

logger = logging.getLogger(__name__)

# Audio configuration
DEFAULT_OUTPUT_BUFFER_MS = 25


class QtPushAudioPlayer(FrameSink):
    """
    Qt audio player using QAudioSink in PUSH mode.

    Unlike pull mode (which uses a QIODevice subclass and is broken in PySide6),
    this uses push mode where we directly write audio data to the internal
    QIODevice returned by QAudioSink.start().

    Push mode works reliably in PySide6.
    """

    def __init__(self, output_buffer_ms: int = DEFAULT_OUTPUT_BUFFER_MS):
        if not QT_AUDIO_AVAILABLE:
            raise RuntimeError("Qt audio not available")

        self._output_buffer_ms = output_buffer_ms
        self._config = None
        self._audio_sink = None
        self._io_device = None  # Internal QIODevice from QAudioSink.start()
        self._sample_buffer = bytearray()
        self._buffer_lock = threading.Lock()
        self._timer = None
        self._running = False
        self._bytes_written = 0

    def _feed_audio(self) -> None:
        """Feed audio data to QAudioSink (called by QTimer)."""
        if not self._running or self._io_device is None:
            return

        try:
            with self._buffer_lock:
                if not self._sample_buffer:
                    return

                # Simple consistent chunk size for smooth playback
                chunk_size = min(len(self._sample_buffer), 9600)
                chunk = bytes(self._sample_buffer[:chunk_size])
                del self._sample_buffer[:chunk_size]

            # Write to internal QIODevice (outside lock)
            written = self._io_device.write(QByteArray(chunk))
            self._bytes_written += written

            # Log occasionally
            if self._bytes_written % 200000 < 9600:
                with self._buffer_lock:
                    logger.info(f"[QT_PUSH] Wrote {written} bytes (buffer: {len(self._sample_buffer)} bytes)")

        except Exception as e:
            logger.error(f"Error feeding audio: {e}")
            import traceback
            traceback.print_exc()

    def open(self, codec_context: Any) -> bool:
        try:
            # Extract parameters
            if isinstance(codec_context, dict):
                sample_rate = codec_context.get("sample_rate", 48000)
                channels = codec_context.get("channels", 2)
            elif hasattr(codec_context, "sample_rate"):
                sample_rate = codec_context.sample_rate
                channels = codec_context.channels if hasattr(codec_context, "channels") else 2
            else:
                sample_rate = 48000
                channels = 2

            self._config = {"sample_rate": sample_rate, "channels": channels}

            # If sink already exists with same config, skip recreation
            if self._audio_sink is not None:
                current_rate = self._config.get("sample_rate")
                current_ch = self._config.get("channels")
                if current_rate == sample_rate and current_ch == channels:
                    logger.debug(f"QtPushAudioPlayer already configured: {sample_rate}Hz, {channels}ch")
                    return True
                else:
                    logger.info(f"Reconfiguring audio: {current_rate}->{sample_rate}Hz, {current_ch}->{channels}ch")
                    # Close existing sink
                    self.close()

            # Get default device
            if QMediaDevices and hasattr(QMediaDevices, 'defaultAudioOutput'):
                device = QMediaDevices.defaultAudioOutput()
            elif QAudioDevice and hasattr(QAudioDevice, 'defaultOutputDevice'):
                device = QAudioDevice.defaultOutputDevice()
            else:
                logger.warning("No audio device")
                return False

            if device.isNull():
                return False

            # Create format (changed to int16)
            fmt = QAudioFormat()
            fmt.setSampleRate(sample_rate)
            fmt.setChannelCount(channels)
            try:
                fmt.setSampleFormat(QAudioFormat.SampleFormat.SignedInt)
            except AttributeError:
                fmt.setSampleSize(16)  # 16-bit int
                fmt.setSampleType(QAudioFormat.SignedInt)

            # Create sink
            self._audio_sink = QAudioSink(device, fmt)
            self._audio_sink.setVolume(1.0)

            # Larger buffer size to prevent underruns (100ms instead of 25ms)
            # 2 bytes per int16 sample (changed from 4)
            buffer_size = int(sample_rate * channels * 2 * 100 / 1000)
            self._audio_sink.setBufferSize(buffer_size)

            logger.info(f"QtPushAudioPlayer: {sample_rate}Hz, {channels}ch, {buffer_size} bytes buffer, int16")
            return True

        except Exception as e:
            logger.error(f"Failed to open: {e}")
            import traceback
            traceback.print_exc()
            return False

    def close(self) -> None:
        self._running = False
        if self._timer:
            self._timer.stop()
            self._timer = None
        if self._audio_sink:
            try:
                self._audio_sink.stop()
            except:
                pass
            self._audio_sink = None
        self._io_device = None
        if self._bytes_written > 0:
            logger.info(f"QtPushAudioPlayer closed (wrote: {self._bytes_written} bytes)")

    def push(self, frame: Any) -> bool:
        if self._config is None or not self._running:
            return False

        try:
            # Convert to bytes
            if hasattr(frame, 'to_ndarray'):
                audio = frame.to_ndarray().astype(np.float32)
                if audio.ndim == 2:
                    audio = audio.T.flatten()
                else:
                    audio = audio.flatten()
                samples = audio.tobytes()
            elif isinstance(frame, np.ndarray):
                if frame.ndim == 2:
                    frame = frame.T.flatten()
                else:
                    frame = frame.flatten()
                samples = frame.astype(np.float32).tobytes()
            elif isinstance(frame, bytes):
                samples = frame
            else:
                return False

            # Add to buffer
            with self._buffer_lock:
                self._sample_buffer.extend(samples)

            return True
        except Exception as e:
            logger.error(f"Error in push: {e}")
            return False

    def start(self) -> None:
        if self._running:
            return

        if self._audio_sink is None:
            logger.warning("Cannot start QtPushAudioPlayer: audio sink not initialized")
            return

        # Start in PUSH MODE - get internal QIODevice
        self._io_device = self._audio_sink.start()
        if self._io_device is None:
            logger.error("Failed to start audio sink")
            return

        self._running = True

        # Setup timer to periodically feed data (5ms = 200Hz)
        if QTimer:
            self._timer = QTimer()
            self._timer.timeout.connect(self._feed_audio)
            self._timer.start(5)  # 5ms = 200Hz

        logger.info("QtPushAudioPlayer started (push mode, 5ms timer)")

    def stop(self) -> None:
        self._running = False
        if self._timer:
            self._timer.stop()
        if self._audio_sink:
            self._audio_sink.reset()
        logger.info("QtPushAudioPlayer stopped")


# Export
__all__ = ["QtPushAudioPlayer"]
