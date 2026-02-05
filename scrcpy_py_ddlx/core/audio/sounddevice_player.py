"""
SoundDevice-based audio player for scrcpy audio streams.

This module provides an audio player using the sounddevice library,
which provides a simpler API than PyAudio and better cross-platform support.

Based on official scrcpy audio_player (SDL2-based).

Resources:
- https://python-sounddevice.readthedocs.io/en/0.5.3/
- https://python-sounddevice.readthedocs.io/en/0.5.3/api/streams.html
"""

import logging
import threading
import time
from typing import Optional, Any, TYPE_CHECKING

import numpy as np

# Import sounddevice
try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False
    sd = None

# Import FrameSink for type checking and runtime
try:
    from scrcpy_py_ddlx.core.av_player import FrameSink
except ImportError:
    # Fallback if av_player import fails
    FrameSink = object

logger = logging.getLogger(__name__)

# Type hint for callback flags (only used for type checking)
if TYPE_CHECKING and sd is not None:
    CallbackFlagsType = sd.CallbackFlags
else:
    CallbackFlagsType = Any

# Audio configuration (optimized for low latency)
DEFAULT_TARGET_BUFFERING_MS = 35  # Target buffering delay (ms)
DEFAULT_OUTPUT_BUFFER_MS = 13      # Audio output buffer size (ms) - 12.5ms for low latency
DEFAULT_MAX_BUFFER_MS = 100       # Maximum buffer size (ms) - cap to prevent excess delay
DEFAULT_BLOCKSIZE = 0             # 0 = optimal (variable) blocksize


class SoundDevicePlayer(FrameSink):
    """
    Audio player using sounddevice's OutputStream for playback.

    This player receives decoded audio frames and plays them through
    the system's default audio output device using PortAudio (via sounddevice).

    Uses a callback-based approach where sounddevice pulls data when needed,
    similar to how PyAudio works but with a simpler NumPy-based API.

    Example:
        >>> player = SoundDevicePlayer()
        >>> player.open({"sample_rate": 48000, "channels": 2})
        >>> player.start()
        >>> player.push(frame)  # Push decoded frames
        >>> player.stop()
        >>> player.close()
    """

    def __init__(
        self,
        target_buffering_ms: int = DEFAULT_TARGET_BUFFERING_MS,
        output_buffer_ms: int = DEFAULT_OUTPUT_BUFFER_MS,
        max_buffer_ms: int = DEFAULT_MAX_BUFFER_MS,
        blocksize: int = DEFAULT_BLOCKSIZE
    ):
        """
        Initialize the sounddevice audio player.

        Args:
            target_buffering_ms: Target buffering delay (ms) - not currently used
            output_buffer_ms: Output buffer size (ms) - used to calculate blocksize
            max_buffer_ms: Maximum buffer size (ms) - caps internal buffer to limit delay
            blocksize: Number of frames per callback (0 = optimal/variable)
        """
        if not SOUNDDEVICE_AVAILABLE:
            raise RuntimeError(
                "sounddevice not available. Install with: pip install sounddevice"
            )

        self._target_buffering_ms = target_buffering_ms
        self._output_buffer_ms = output_buffer_ms
        self._max_buffer_ms = max_buffer_ms
        self._blocksize = blocksize

        # Audio configuration
        self._config: Optional[dict] = None
        self._stream: Optional[sd.OutputStream] = None
        self._sample_rate: int = 48000
        self._channels: int = 2

        # Sample buffer (thread-safe)
        self._sample_buffer: bytearray = bytearray()
        self._buffer_lock = threading.Lock()

        # Threading
        self._running = False

        # Statistics
        self._frames_pushed = 0
        self._total_bytes_pushed = 0
        self._frames_played = 0
        self._underruns = 0

        # Callback state
        self._callback_count = 0

    def open(self, codec_context: Any) -> bool:
        """
        Initialize audio player with codec parameters.

        Args:
            codec_context: Codec context with sample_rate, channels

        Returns:
            True if successful
        """
        try:
            # If stream already exists, just update config and return
            if self._stream is not None:
                logger.debug("SoundDevicePlayer stream already exists, skipping re-creation")
                # Update config if needed
                if isinstance(codec_context, dict):
                    self._sample_rate = codec_context.get("sample_rate", 48000)
                    self._channels = codec_context.get("channels", 2)
                elif hasattr(codec_context, "sample_rate"):
                    self._sample_rate = codec_context.sample_rate
                    self._channels = codec_context.channels if hasattr(codec_context, "channels") else 2
                return True

            # Extract audio parameters
            if isinstance(codec_context, dict):
                sample_rate = codec_context.get("sample_rate", 48000)
                channels = codec_context.get("channels", 2)
            elif hasattr(codec_context, "sample_rate"):
                sample_rate = codec_context.sample_rate
                channels = codec_context.channels if hasattr(codec_context, "channels") else 2
            else:
                logger.warning("Invalid codec context, using defaults")
                sample_rate = 48000
                channels = 2

            # Store configuration
            self._sample_rate = sample_rate
            self._channels = channels
            self._config = {
                "sample_rate": sample_rate,
                "channels": channels,
                "format": "float32"  # Use float32 for better audio quality
            }

            # Calculate blocksize from buffer size if using default
            if self._blocksize == 0:
                # Convert milliseconds to frames
                self._blocksize = int(sample_rate * self._output_buffer_ms / 1000)
                logger.info(f"Calculated blocksize: {self._blocksize} frames from {self._output_buffer_ms}ms")

            # Create OutputStream (not started yet)
            # We'll start it in start() method
            # Use float32 format (native OPUS decoder output)
            self._stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype='float32',
                blocksize=self._blocksize,
                callback=self._audio_callback,
                finished_callback=self._stream_finished
            )

            logger.info(
                f"SoundDevicePlayer opened: {sample_rate}Hz, {channels} channels, "
                f"blocksize={self._blocksize}, dtype=float32"
            )

            return True

        except Exception as e:
            logger.error(f"Failed to open sounddevice audio player: {e}")
            import traceback
            traceback.print_exc()
            return False

    def close(self) -> None:
        """Close the audio player and cleanup resources."""
        self._running = False

        if self._stream is not None:
            try:
                if self._stream.active:
                    self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.debug(f"Error closing stream: {e}")
            self._stream = None

        # Clear buffers
        with self._buffer_lock:
            self._sample_buffer.clear()

        logger.info(
            f"SoundDevicePlayer closed "
            f"(pushed: {self._frames_pushed}, played: {self._frames_played}, "
            f"underruns: {self._underruns})"
        )

    def push(self, frame: Any) -> bool:
        """
        Push a decoded audio frame to the player.

        Args:
            frame: AVFrame or audio data (numpy array or bytes)

        Returns:
            True if successful
        """
        if self._config is None or not self._running:
            return False

        try:
            # Convert frame to bytes
            if hasattr(frame, 'to_ndarray'):
                # PyAV VideoFrame (audio frames also use this)
                audio_data = frame.to_ndarray()
                # Flatten and convert to bytes
                if audio_data.ndim == 2:
                    # Planar audio - interleave channels
                    audio_data = audio_data.T.flatten()
                else:
                    audio_data = audio_data.flatten()

                # Convert to float32 bytes
                samples = audio_data.astype(np.float32).tobytes()
            elif isinstance(frame, np.ndarray):
                # Already a numpy array
                if frame.ndim == 2:
                    frame = frame.T.flatten()
                else:
                    frame = frame.flatten()
                samples = frame.astype(np.float32).tobytes()
            elif isinstance(frame, bytes):
                samples = frame
            else:
                logger.warning(f"Unknown frame type: {type(frame)}")
                return False

            # Add to sample buffer with size limiting
            with self._buffer_lock:
                was_empty = len(self._sample_buffer) == 0
                self._sample_buffer.extend(samples)

                # Limit buffer size to prevent excess delay
                # Calculate max buffer size in bytes
                max_buffer_bytes = int(self._sample_rate * self._channels * 4 * self._max_buffer_ms / 1000)
                if len(self._sample_buffer) > max_buffer_bytes:
                    # Drop oldest samples (from beginning) to stay within max buffer
                    excess_bytes = len(self._sample_buffer) - max_buffer_bytes
                    del self._sample_buffer[:excess_bytes]

                self._frames_pushed += 1
                self._total_bytes_pushed += len(samples)

            # Log occasionally
            if self._frames_pushed % 100 == 1:
                with self._buffer_lock:
                    buffer_ms = (len(self._sample_buffer) // 4 // self._channels * 1000) // self._sample_rate
                    logger.info(
                        f"[PLAYER] Pushed frame #{self._frames_pushed}: {len(samples)} bytes "
                        f"(total: {self._total_bytes_pushed}, buffer: {buffer_ms}ms)"
                    )

            return True

        except Exception as e:
            logger.error(f"Error processing audio frame: {e}")
            import traceback
            traceback.print_exc()
            return False

    def start(self) -> None:
        """Start audio playback."""
        if self._running:
            return

        if self._stream is not None:
            self._running = True
            self._stream.start()
            logger.info("SoundDevicePlayer started")
        else:
            logger.warning("Cannot start: stream not initialized")

    def stop(self) -> None:
        """Stop audio playback."""
        self._running = False

        if self._stream is not None and self._stream.active:
            try:
                self._stream.stop()
            except Exception as e:
                logger.debug(f"Error stopping stream: {e}")

        # Clear buffer to stop any remaining audio from playing
        with self._buffer_lock:
            self._sample_buffer.clear()

        logger.info("SoundDevicePlayer stopped")

    def _audio_callback(self, outdata: np.ndarray, frames: int,
                       time: Any, status: CallbackFlagsType) -> None:
        """
        sounddevice callback for providing audio samples.

        This is called by the audio thread when it needs more samples.
        The signature is specific to OutputStream (no indata parameter).

        Args:
            outdata: NumPy array to fill with audio samples (frames, channels)
            frames: Number of frames to provide
            time: Timestamp info
            status: Callback status flags
        """
        self._callback_count += 1

        try:
            # Calculate bytes needed
            bytes_needed = frames * self._channels * 4  # 4 bytes per float32

            with self._buffer_lock:
                if len(self._sample_buffer) >= bytes_needed:
                    # Get samples from buffer
                    samples = bytes(self._sample_buffer[:bytes_needed])
                    del self._sample_buffer[:bytes_needed]

                    # Convert to numpy array and reshape
                    audio_array = np.frombuffer(samples, dtype=np.float32)
                    if self._channels > 1:
                        audio_array = audio_array.reshape(-1, self._channels)

                    # Copy to output buffer
                    outdata[:] = audio_array
                    self._frames_played += 1

                    # Log occasionally
                    if self._callback_count <= 20 or self._callback_count % 200 == 0:
                        logger.info(
                            f"[CALLBACK] #{self._callback_count}: "
                            f"Provided {frames} frames (buffer remaining: {len(self._sample_buffer)} bytes)"
                        )
                else:
                    # Not enough samples - fill with silence
                    outdata.fill(0.0)
                    self._underruns += 1

                    # Log underruns
                    if self._callback_count <= 20 or self._callback_count % 200 == 0:
                        logger.info(
                            f"[CALLBACK] #{self._callback_count}: "
                            f"Underrun (needed {bytes_needed} bytes, had {len(self._sample_buffer)})"
                        )

            # Handle status flags
            if status:
                if status.output_underflow:
                    logger.warning("Audio output underflow detected")
                if status.priming_output:
                    logger.debug("Priming output buffer")

        except Exception as e:
            logger.error(f"Audio callback error: {e}")
            import traceback
            traceback.print_exc()
            # Fill with silence on error
            outdata.fill(0.0)

    def _stream_finished(self) -> None:
        """Called when the stream finishes (becomes inactive)."""
        logger.info("Audio stream finished")

    @property
    def latency(self) -> float:
        """Get the actual stream latency in seconds."""
        if self._stream is not None:
            return self._stream.latency
        return 0.0

    @property
    def samplerate(self) -> int:
        """Get the actual sample rate."""
        if self._stream is not None:
            return self._stream.samplerate
        return self._sample_rate

    @property
    def is_active(self) -> bool:
        """Check if the stream is active."""
        return self._stream is not None and self._stream.active and self._running


# Export for easy import
AudioPlayer = SoundDevicePlayer

__all__ = [
    "SoundDevicePlayer",
    "AudioPlayer",
]
