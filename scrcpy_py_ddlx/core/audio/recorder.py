"""
Audio recorder for capturing device audio.

This module provides audio recording functionality by intercepting
decoded audio data from AudioDecoder and writing it to audio files.

Supported formats:
- WAV: Float32 IEEE format (manual header write, no extra dependencies)
- OPUS: WAV format + auto-convert to Opus after recording (uses PyAV)
- MP3: WAV format + auto-convert to MP3 after recording (uses PyAV)

Recording performance:
- WAV: Zero performance impact (copies data already being decoded)
- OPUS/MP3: Same as WAV, with conversion after recording

All conversions use PyAV (built-in FFmpeg), no external FFmpeg required.
"""

import logging
import struct
import threading
import time
from pathlib import Path
from typing import Optional, Any

import av

logger = logging.getLogger(__name__)


class AudioRecorder:
    """
    Audio recorder that captures decoded audio data.

    Implements the FrameSink interface to intercept audio data
    from AudioDecoder and write it to WAV files.

    Audio format:
    - Sample rate: 48000 Hz (default)
    - Channels: 2 (stereo)
    - Sample format: float32 IEEE (native OPUS decoder output)
    - Bytes per second: 48000 * 2 * 4 = 384,000 bytes/sec
    """

    def __init__(self, filename: str, max_duration: Optional[float] = None, auto_convert_to: Optional[str] = None):
        """
        Initialize audio recorder.

        Args:
            filename: Output filename (e.g., "recording.wav" or "recording.opus")
            max_duration: Maximum recording duration in seconds (None for unlimited)
            auto_convert_to: Target format for auto-conversion ('opus', 'mp3'). Default None.
                           If filename ends with .opus or .mp3, auto-converts to that format.
        """
        self._filename = filename
        self._max_duration = max_duration
        self._auto_convert_to = auto_convert_to

        # Determine if we need to auto-convert
        if auto_convert_to:
            self._convert_format = auto_convert_to.lower()
        else:
            ext = Path(filename).suffix.lower()
            if ext == '.opus':
                self._convert_format = 'opus'
            elif ext == '.mp3':
                self._convert_format = 'mp3'
            else:
                self._convert_format = None

        # If converting, we'll use a temp WAV file
        if self._convert_format:
            self._wav_filename = str(Path(filename).with_suffix('.tmp.wav'))
        else:
            self._wav_filename = filename

        # Audio parameters
        self._sample_rate = 48000
        self._channels = 2
        self._sample_width = 4  # float32 = 4 bytes (native OPUS decoder output)

        # Recording state
        self._is_open = False
        self._is_recording = False
        self._frames_written = 0
        self._bytes_written = 0
        self._start_time = None

        # Audio buffer (thread-safe)
        self._buffer = bytearray()
        self._buffer_lock = threading.Lock()

        # File handle (direct file I/O for WAV)
        self._file_handle = None

    def open(self, codec_context: Any = None, sample_rate: int = None, channels: int = None) -> bool:
        """
        Open the recorder and prepare for recording.

        IMPORTANT: If already open, closes the old file first to prevent append issues.

        Args:
            codec_context: Codec context containing audio parameters (optional)
            sample_rate: Audio sample rate (optional, overrides codec_context)
            channels: Number of audio channels (optional, overrides codec_context)

        Returns:
            True if opened successfully
        """
        # Close any existing file before opening a new one
        # This prevents file append issues when reusing the recorder
        if self._is_open:
            logger.info("Recorder already open, closing old file before reopening")
            self.close()

        try:
            # Extract audio parameters
            if sample_rate is not None:
                self._sample_rate = sample_rate
            elif codec_context is not None and hasattr(codec_context, '_sample_rate'):
                self._sample_rate = codec_context._sample_rate

            if channels is not None:
                self._channels = channels
            elif codec_context is not None and hasattr(codec_context, '_channels'):
                self._channels = codec_context._channels

            target_format = f" -> {self._convert_format.upper()}" if self._convert_format else ""
            logger.info(f"Opening audio recorder: {self._filename}{target_format}")
            logger.info(f"  Sample rate: {self._sample_rate} Hz")
            logger.info(f"  Channels: {self._channels}")
            logger.info(f"  Max duration: {self._max_duration if self._max_duration else 'unlimited'} sec")

            # Create parent directories if needed
            Path(self._wav_filename).parent.mkdir(parents=True, exist_ok=True)

            # Direct file writing for WAV
            self._file_handle = open(self._wav_filename, 'wb')
            self._write_wav_header()

            self._is_open = True
            self._is_recording = True
            self._start_time = time.time()

            logger.info(f"Audio recorder opened: {self._wav_filename} (WAV float32)")
            return True

        except Exception as e:
            logger.error(f"Failed to open audio recorder: {e}")
            return False

    def _write_wav_header(self):
        """Write WAV file header for IEEE float format."""
        if self._file_handle is None:
            return

        # RIFF header
        self._file_handle.write(b'RIFF')
        self._file_handle.write(struct.pack('<I', 0))  # File size - 8 (placeholder)
        self._file_handle.write(b'WAVE')

        # fmt chunk (format chunk)
        self._file_handle.write(b'fmt ')
        self._file_handle.write(struct.pack('<I', 16))  # Chunk size
        self._file_handle.write(struct.pack('<H', 3))   # Audio format: 3 = IEEE float
        self._file_handle.write(struct.pack('<H', self._channels))
        self._file_handle.write(struct.pack('<I', self._sample_rate))

        # Byte rate = sample_rate * channels * bits_per_sample / 8
        byte_rate = self._sample_rate * self._channels * self._sample_width
        self._file_handle.write(struct.pack('<I', byte_rate))

        # Block align = channels * bits_per_sample / 8
        block_align = self._channels * self._sample_width
        self._file_handle.write(struct.pack('<H', block_align))

        # Bits per sample
        self._file_handle.write(struct.pack('<H', 32))  # float32 = 32 bits

        # data chunk
        self._file_handle.write(b'data')
        self._file_handle.write(struct.pack('<I', 0))  # Data size (placeholder)

    def push(self, frame: bytes) -> bool:
        """
        Receive decoded audio data and add to recording buffer.

        This method is called by AudioDecoder for each decoded audio packet.

        Args:
            frame: Decoded audio data (float32 PCM bytes)

        Returns:
            True if data was accepted
        """
        if not self._is_recording:
            return False

        # Check max duration
        if self._max_duration:
            elapsed = time.time() - self._start_time
            if elapsed >= self._max_duration:
                logger.info(f"Max duration ({self._max_duration}s) reached, closing recorder")
                self._is_recording = False
                self.close()  # Auto-close when max duration reached
                return False

        # Add frame to buffer
        with self._buffer_lock:
            self._buffer.extend(frame)
            self._bytes_written += len(frame)
            self._frames_written += len(frame) // (self._channels * self._sample_width)

        return True

    def _convert_to_opus(self) -> bool:
        """Convert WAV to Opus format using PyAV."""
        try:
            logger.info(f"Converting {self._wav_filename} to Opus: {self._filename}")

            # Open input WAV file
            input_ = av.open(self._wav_filename, 'r')

            # Create output Opus file
            output = av.open(self._filename, 'w')

            # Add Opus stream - specify codec parameters directly
            output_stream = output.add_stream('libopus', rate=48000)

            # Convert frames
            for frame in input_.decode(audio=0):
                for packet in output_stream.encode(frame):
                    output.mux(packet)

            # Flush encoder
            for packet in output_stream.encode():
                output.mux(packet)

            input_.close()
            output.close()

            wav_size = Path(self._wav_filename).stat().st_size / 1024
            opus_size = Path(self._filename).stat().st_size / 1024
            ratio = (opus_size / wav_size) * 100
            logger.info(f"Opus conversion successful:")
            logger.info(f"  WAV:  {wav_size:.1f} KB")
            logger.info(f"  Opus: {opus_size:.1f} KB ({ratio:.1f}% of original)")

            # Remove temp WAV file
            Path(self._wav_filename).unlink()
            return True

        except Exception as e:
            logger.error(f"PyAV Opus conversion failed: {e}")
            logger.info(f"WAV file saved at: {self._wav_filename}")
            return False

    def _convert_to_mp3(self) -> bool:
        """Convert WAV to MP3 format using PyAV."""
        try:
            logger.info(f"Converting {self._wav_filename} to MP3: {self._filename}")

            # Open input WAV file
            input_ = av.open(self._wav_filename, 'r')

            # Create output MP3 file
            output = av.open(self._filename, 'w')

            # Add MP3 stream
            output_stream = output.add_stream('mp3', rate=48000)

            # Convert frames
            for frame in input_.decode(audio=0):
                for packet in output_stream.encode(frame):
                    output.mux(packet)

            # Flush encoder
            for packet in output_stream.encode():
                output.mux(packet)

            input_.close()
            output.close()

            wav_size = Path(self._wav_filename).stat().st_size / 1024
            mp3_size = Path(self._filename).stat().st_size / 1024
            ratio = (mp3_size / wav_size) * 100
            logger.info(f"MP3 conversion successful:")
            logger.info(f"  WAV: {wav_size:.1f} KB")
            logger.info(f"  MP3: {mp3_size:.1f} KB ({ratio:.1f}% of original)")

            # Remove temp WAV file
            Path(self._wav_filename).unlink()
            return True

        except Exception as e:
            logger.error(f"PyAV MP3 conversion failed: {e}")
            logger.info(f"WAV file saved at: {self._wav_filename}")
            return False

    def close(self) -> None:
        """
        Close the recorder and finalize the recording.

        Writes all buffered data to file, optionally converts to target format.
        """
        if not self._is_open:
            return

        logger.info("Closing audio recorder...")

        self._is_recording = False
        self._is_open = False

        # Write WAV file
        with self._buffer_lock:
            if self._buffer and self._file_handle:
                # Write audio data
                self._file_handle.write(self._buffer)

                # Update file size in header
                data_size = len(self._buffer)
                file_size = data_size + 36  # 36 = header size (12 + 24)

                # Seek back and update sizes
                self._file_handle.seek(4)  # After "RIFF"
                self._file_handle.write(struct.pack('<I', file_size))

                self._file_handle.seek(40)  # After "data" chunk ID
                self._file_handle.write(struct.pack('<I', data_size))

                self._buffer.clear()

        # Close file
        if self._file_handle is not None:
            try:
                self._file_handle.close()
                duration = self._frames_written / self._sample_rate
                wav_size = Path(self._wav_filename).stat().st_size / 1024  # KB
                logger.info(f"Recording saved: {self._wav_filename}")
                logger.info(f"  Duration: {duration:.2f} sec")
                logger.info(f"  Frames: {self._frames_written}")
                logger.info(f"  File size: {wav_size:.1f} KB")
            except Exception as e:
                logger.error(f"Error closing file: {e}")
            finally:
                self._file_handle = None

        # Convert to target format if requested
        if self._convert_format:
            if self._convert_format == 'opus':
                self._convert_to_opus()
            elif self._convert_format == 'mp3':
                self._convert_to_mp3()

    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self._is_recording

    def get_duration(self) -> float:
        """Get current recording duration in seconds."""
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    def get_file_size(self) -> int:
        """Get current file size in bytes."""
        with self._buffer_lock:
            return self._bytes_written


class TeeAudioRecorder:
    """
    Audio recorder that duplicates audio data to both a recorder and a player.

    This allows recording audio while still playing it through the audio output.
    Implements the FrameSink interface and forwards data to both sinks.
    """

    def __init__(self, recorder: AudioRecorder, player):
        """
        Initialize tee recorder.

        Args:
            recorder: AudioRecorder instance
            player: Audio player (SoundDevicePlayer or QtPushAudioPlayer)
        """
        self._recorder = recorder
        self._player = player

    def open(self, codec_context: Any) -> bool:
        """Open both recorder and player."""
        result = self._player.open(codec_context)
        if result:
            self._recorder.open(codec_context)
        return result

    def push(self, frame: bytes) -> bool:
        """Push audio data to both recorder and player."""
        # Always push to player first (lower latency)
        self._player.push(frame)
        # Then to recorder
        if self._recorder.is_recording():
            self._recorder.push(frame)
        return True

    def close(self) -> None:
        """Close both recorder and player."""
        self._recorder.close()
        self._player.close()
