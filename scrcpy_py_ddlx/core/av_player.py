"""
Audio and Video players/recorders for scrcpy.

This module implements:
- AudioPlayer: Plays decoded audio frames using PyAudio
- Recorder: Records video/audio packets to file using PyAV
- Screen: Frame buffer with callbacks for frame rendering

Based on official scrcpy implementation (app/src/audio_player.c, recorder.c, screen.c).
"""

import logging
import threading
import queue
import time
from typing import Optional, Callable, Any
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    pyaudio = None

import av

logger = logging.getLogger(__name__)


# Audio configuration
DEFAULT_TARGET_BUFFERING_MS = 35  # Target buffering delay (ms)
DEFAULT_OUTPUT_BUFFER_MS = 25      # SDL audio output buffer size (ms)


class AudioFormat(Enum):
    """Audio sample formats."""
    U8 = "u8"
    S16 = "s16"
    S32 = "s32"
    F32 = "f32"


@dataclass
class AudioConfig:
    """Audio configuration."""
    sample_rate: int
    channels: int
    format: AudioFormat = AudioFormat.F32


class FrameSink:
    """
    Base class for frame sinks (receivers of decoded frames).

    Based on official scrcpy frame_sink trait.
    """

    def open(self, codec_context: Any) -> bool:
        """
        Initialize the sink with codec parameters.

        Args:
            codec_context: AVCodecContext or dict with codec parameters

        Returns:
            True if successful
        """
        raise NotImplementedError

    def close(self) -> None:
        """Close the sink and cleanup resources."""
        raise NotImplementedError

    def push(self, frame: Any) -> bool:
        """
        Push a decoded frame to the sink.

        Args:
            frame: AVFrame or decoded frame data

        Returns:
            True if successful
        """
        raise NotImplementedError


class AudioPlayer(FrameSink):
    """
    Audio player using PyAudio for playback.

    This player receives decoded audio frames and plays them through
    the default audio output device.

    Based on official scrcpy audio_player (SDL2-based).

    Example:
        >>> player = AudioPlayer()
        >>> player.open(codec_context)
        >>> player.push(frame)  # Push decoded frames
        >>> player.close()
    """

    def __init__(
        self,
        target_buffering_ms: int = DEFAULT_TARGET_BUFFERING_MS,
        output_buffer_ms: int = DEFAULT_OUTPUT_BUFFER_MS
    ):
        """
        Initialize the audio player.

        Args:
            target_buffering_ms: Target buffering delay (ms)
            output_buffer_ms: Output buffer size (ms)
        """
        if not PYAUDIO_AVAILABLE:
            raise RuntimeError("PyAudio not available. Install with: pip install pyaudio")

        self._target_buffering_ms = target_buffering_ms
        self._output_buffer_ms = output_buffer_ms

        # Audio state
        self._config: Optional[AudioConfig] = None
        self._stream: Optional[pyaudio.Stream] = None
        self._pa: Optional[pyaudio.PyAudio] = None

        # Frame buffer (audio regulator)
        self._frame_queue: queue.Queue = queue.Queue(maxsize=10)
        self._sample_buffer: bytearray = bytearray()
        self._buffer_lock = threading.Lock()

        # Threading
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Statistics
        self._frames_played = 0
        self._frames_dropped = 0

    def open(self, codec_context: Any) -> bool:
        """
        Initialize audio player with codec parameters.

        Args:
            codec_context: Codec context with sample_rate, channels

        Returns:
            True if successful
        """
        try:
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

            self._config = AudioConfig(
                sample_rate=sample_rate,
                channels=channels,
                format=AudioFormat.F32
            )

            # Calculate buffer size
            frames_per_buffer = int(sample_rate * self._output_buffer_ms / 1000)

            # Initialize PyAudio
            self._pa = pyaudio.PyAudio()

            # Open audio stream
            self._stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=sample_rate,
                output=True,
                frames_per_buffer=frames_per_buffer,
                stream_callback=self._audio_callback
            )

            logger.info(
                f"AudioPlayer opened: {sample_rate}Hz, {channels} channels, "
                f"{frames_per_buffer} frames/buffer"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to open audio player: {e}")
            return False

    def close(self) -> None:
        """Close the audio player and cleanup resources."""
        self._running = False

        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception as e:
                logger.debug(f"Error closing stream: {e}")
            self._stream = None

        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception as e:
                logger.debug(f"Error terminating PyAudio: {e}")
            self._pa = None

        # Clear buffers
        with self._buffer_lock:
            self._sample_buffer.clear()

        logger.info(f"AudioPlayer closed (played: {self._frames_played}, dropped: {self._frames_dropped})")

    def push(self, frame: Any) -> bool:
        """
        Push a decoded audio frame to the player.

        Args:
            frame: AVFrame or audio data

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
                samples = audio_data.tobytes()
            elif isinstance(frame, bytes):
                samples = frame
            else:
                logger.warning("Unknown frame type")
                return False

            # Add to sample buffer
            with self._buffer_lock:
                self._sample_buffer.extend(samples)

            return True

        except Exception as e:
            logger.error(f"Error processing audio frame: {e}")
            return False

    def start(self) -> None:
        """Start audio playback."""
        if self._running:
            return

        self._running = True
        if self._stream is not None:
            self._stream.start_stream()
        logger.info("AudioPlayer started")

    def stop(self) -> None:
        """Stop audio playback."""
        self._running = False
        if self._stream is not None:
            self._stream.stop_stream()
        logger.info("AudioPlayer stopped")

    def _audio_callback(self, in_data, frame_count, time_info, status):
        """
        PyAudio callback for pulling samples.

        This is called by the audio thread when it needs more samples.
        """
        try:
            # Calculate bytes needed
            bytes_needed = frame_count * self._config.channels * 4  # 4 bytes per float32

            with self._buffer_lock:
                if len(self._sample_buffer) >= bytes_needed:
                    # Get samples from buffer
                    samples = self._sample_buffer[:bytes_needed]
                    del self._sample_buffer[:bytes_needed]
                    self._frames_played += 1
                    return (bytes(samples), pyaudio.paContinue)
                else:
                    # Not enough samples - return silence
                    silence = b'\x00' * bytes_needed
                    self._frames_dropped += 1
                    return (silence, pyaudio.paContinue)

        except Exception as e:
            logger.error(f"Audio callback error: {e}")
            silence = b'\x00' * (frame_count * self._config.channels * 4)
            return (silence, pyaudio.paContinue)


class PacketSink:
    """
    Base class for packet sinks (receivers of encoded packets).

    Based on official scrcpy packet_sink trait.
    """

    def open(self, codec_context: Any) -> bool:
        """Initialize with codec parameters."""
        raise NotImplementedError

    def close(self) -> None:
        """Signal end of stream."""
        raise NotImplementedError

    def push(self, packet: Any) -> bool:
        """Process an encoded packet."""
        raise NotImplementedError


class Recorder(PacketSink):
    """
    Records video/audio packets to a file using PyAV muxing.

    Supports MP4, MKV, and other formats.

    Based on official scrcpy recorder.

    Example:
        >>> recorder = Recorder("output.mp4", format="mp4", video=True, audio=False)
        >>> recorder.open(video_codec_context)
        >>> recorder.push(video_packet)
        >>> recorder.close()
    """

    def __init__(
        self,
        filename: str,
        format: str = "mp4",
        video: bool = True,
        audio: bool = False,
        on_ended: Optional[Callable[[bool], None]] = None
    ):
        """
        Initialize the recorder.

        Args:
            filename: Output filename
            format: Container format (mp4, mkv, etc.)
            video: Enable video recording
            audio: Enable audio recording
            on_ended: Callback when recording ends
        """
        self._filename = filename
        self._format = format
        self._video_enabled = video
        self._audio_enabled = audio
        self._on_ended = on_ended

        # Output container
        self._output: Optional[av.OutputContainer] = None
        self._video_stream: Optional[av.VideoStream] = None
        self._audio_stream: Optional[av.AudioStream] = None

        # Codec contexts
        self._video_codec_ctx: Optional[Any] = None
        self._audio_codec_ctx: Optional[Any] = None

        # Packet queues
        self._video_queue: queue.Queue = queue.Queue(maxsize=100)
        self._audio_queue: queue.Queue = queue.Queue(maxsize=100)

        # Threading
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Statistics
        self._video_packets_written = 0
        self._audio_packets_written = 0

    def open(self, codec_context: Any) -> bool:
        """
        Initialize a stream with codec parameters.

        Can be called twice: once for video, once for audio.

        Args:
            codec_context: Codec context with parameters

        Returns:
            True if successful
        """
        try:
            # Determine if this is video or audio
            codec_type = self._get_codec_type(codec_context)

            if codec_type == "video":
                return self._open_video_stream(codec_context)
            elif codec_type == "audio":
                return self._audio_enabled and self._open_audio_stream(codec_context)

            return False

        except Exception as e:
            logger.error(f"Failed to open stream: {e}")
            return False

    def _get_codec_type(self, codec_context: Any) -> str:
        """Determine codec type from context."""
        if isinstance(codec_context, dict):
            return codec_context.get("codec_type", "video")
        elif hasattr(codec_context, "codec_type"):
            return "video" if codec_context.codec_type == "video" else "audio"
        return "video"  # Default

    def _open_video_stream(self, codec_context: Any) -> bool:
        """Open video stream."""
        with self._lock:
            try:
                # Extract video parameters
                if isinstance(codec_context, dict):
                    width = codec_context.get("width", 1920)
                    height = codec_context.get("height", 1080)
                    codec_id = codec_context.get("codec_id", 0)
                elif hasattr(codec_context, "width"):
                    width = codec_context.width
                    height = codec_context.height
                    codec_id = codec_context.codec_id if hasattr(codec_context, "codec_id") else 0
                else:
                    logger.warning("Invalid video codec context")
                    return False

                self._video_codec_ctx = codec_context

                # Create output container if not exists
                if self._output is None:
                    self._output = av.open(self._filename, mode='w', format=self._format)

                # Add video stream
                codec_name = self._get_codec_name(codec_id)
                self._video_stream = self._output.add_stream(codec_name, rate=30)
                self._video_stream.width = width
                self._video_stream.height = height
                self._video_stream.pix_fmt = "yuv420p"

                logger.info(f"Video stream opened: {width}x{height}, codec={codec_name}")
                return True

            except Exception as e:
                logger.error(f"Failed to open video stream: {e}")
                return False

    def _open_audio_stream(self, codec_context: Any) -> bool:
        """Open audio stream."""
        with self._lock:
            try:
                # Extract audio parameters
                if isinstance(codec_context, dict):
                    sample_rate = codec_context.get("sample_rate", 48000)
                    channels = codec_context.get("channels", 2)
                elif hasattr(codec_context, "sample_rate"):
                    sample_rate = codec_context.sample_rate
                    channels = codec_context.channels if hasattr(codec_context, "channels") else 2
                else:
                    logger.warning("Invalid audio codec context")
                    return False

                self._audio_codec_ctx = codec_context

                # Create output container if not exists
                if self._output is None:
                    self._output = av.open(self._filename, mode='w', format=self._format)

                # Add audio stream
                self._audio_stream = self._output.add_stream('aac', rate=sample_rate)
                self._audio_stream.channels = channels
                self._audio_stream.layout = 'stereo' if channels == 2 else 'mono'

                logger.info(f"Audio stream opened: {sample_rate}Hz, {channels} channels")
                return True

            except Exception as e:
                logger.error(f"Failed to open audio stream: {e}")
                return False

    def _get_codec_name(self, codec_id: int) -> str:
        """Map scrcpy codec ID to FFmpeg codec name."""
        from .protocol import CodecId
        if codec_id == CodecId.H264:
            return 'h264'
        elif codec_id == CodecId.H265:
            return 'hevc'
        elif codec_id == CodecId.AV1:
            return 'av1'
        return 'h264'  # Default

    def start(self) -> None:
        """Start recording."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_recorder, daemon=True)
        self._thread.start()
        logger.info(f"Recorder started: {self._filename}")

    def stop(self) -> None:
        """Stop recording."""
        if not self._running:
            return

        logger.info("Stopping recorder...")
        self._running = False

        # Wait for thread to finish
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

        # Close output
        if self._output is not None:
            try:
                self._output.close()
            except Exception as e:
                logger.debug(f"Error closing output: {e}")
            self._output = None

        logger.info(
            f"Recorder stopped (video: {self._video_packets_written}, "
            f"audio: {self._audio_packets_written})"
        )

    def push(self, packet: Any) -> bool:
        """
        Push an encoded packet to the recorder.

        Args:
            packet: Encoded packet (AVPacket or VideoPacket)

        Returns:
            True if successful
        """
        try:
            # Determine packet type
            if hasattr(packet, "header") or "video" in str(type(packet)).lower():
                # Video packet
                if self._video_enabled and not self._video_queue.full():
                    self._video_queue.put(packet)
                    return True
            else:
                # Audio packet
                if self._audio_enabled and not self._audio_queue.full():
                    self._audio_queue.put(packet)
                    return True

            return False

        except Exception as e:
            logger.error(f"Error pushing packet: {e}")
            return False

    def _run_recorder(self) -> None:
        """Recorder thread main loop."""
        try:
            # Write header
            if self._output is not None:
                self._output.start()

            last_video_pts = None
            last_audio_pts = None

            while self._running:
                # Get next packet (prioritize video)
                packet = None
                is_video = False

                if self._video_enabled:
                    try:
                        packet = self._video_queue.get(timeout=0.1)
                        is_video = True
                    except queue.Empty:
                        pass

                if packet is None and self._audio_enabled:
                    try:
                        packet = self._audio_queue.get(timeout=0.1)
                        is_video = False
                    except queue.Empty:
                        pass

                if packet is None:
                    continue

                # Convert packet to PyAV format
                av_packet = self._convert_packet(packet, is_video)
                if av_packet is None:
                    continue

                # Write packet
                stream = self._video_stream if is_video else self._audio_stream
                if stream is not None:
                    # Rescale PTS if needed
                    av_packet.stream = stream
                    self._output.mux(av_packet)

                    if is_video:
                        self._video_packets_written += 1
                    else:
                        self._audio_packets_written += 1

        except Exception as e:
            logger.error(f"Recorder error: {e}")

        finally:
            # Write trailer
            if self._output is not None:
                try:
                    # Close all streams first
                    for stream in self._output.streams:
                        stream.close()
                except Exception as e:
                    logger.debug(f"Error closing streams: {e}")

            # Call callback
            if self._on_ended is not None:
                try:
                    self._on_ended(True)
                except Exception as e:
                    logger.error(f"Error in on_ended callback: {e}")

    def _convert_packet(self, packet: Any, is_video: bool) -> Optional[av.Packet]:
        """Convert packet to PyAV format."""
        try:
            # Get packet data
            if hasattr(packet, "data"):
                data = packet.data
            elif isinstance(packet, bytes):
                data = packet
            elif hasattr(packet, "get"):
                data = packet.get("data", b"")
            else:
                return None

            # Get PTS
            if hasattr(packet, "pts"):
                pts = packet.pts
            elif hasattr(packet, "header") and hasattr(packet.header, "pts"):
                pts = packet.header.pts
            else:
                pts = 0

            # Create PyAV packet
            av_packet = av.Packet(data)
            av_packet.pts = pts
            av_packet.dts = pts

            return av_packet

        except Exception as e:
            logger.error(f"Error converting packet: {e}")
            return None

    def close(self) -> None:
        """Signal end of stream (alias for stop)."""
        self.stop()


class Screen(FrameSink):
    """
    Screen frame buffer with callbacks for frame rendering.

    Maintains a single-frame buffer (latest frame) and provides
    callbacks for frame notifications.

    Based on official scrcpy screen.

    Example:
        >>> def on_frame(frame):
        ...     cv2.imshow('Screen', frame)
        >>>
        >>> screen = Screen(on_frame_callback=on_frame)
        >>> screen.open(codec_context)
        >>> screen.push(frame)
    """

    def __init__(
        self,
        on_frame_callback: Optional[Callable[[Any], None]] = None,
        on_init_callback: Optional[Callable[[int, int], None]] = None
    ):
        """
        Initialize the screen.

        Args:
            on_frame_callback: DEPRECATED - No longer used (kept for compatibility)
            on_init_callback: Called with (width, height) on init
        """
        self._on_frame_callback = on_frame_callback  # DEPRECATED: Kept for compatibility
        self._on_init_callback = on_init_callback

        # Direct access to DelayBuffer (set by client)
        # This allows video_window to consume directly from DelayBuffer
        # instead of going through Screen's _frame storage
        self._delay_buffer: Optional['DelayBuffer'] = None

        # Screen parameters
        self._width = 0
        self._height = 0

        # Statistics (only tracking, no storage)
        self._frames_received = 0
        self._frames_shown = 0

    def open(self, codec_context: Any) -> bool:
        """
        Initialize screen with video dimensions.

        Args:
            codec_context: Codec context with width, height

        Returns:
            True if successful
        """
        try:
            # Extract video parameters
            if isinstance(codec_context, dict):
                self._width = codec_context.get("width", 1920)
                self._height = codec_context.get("height", 1080)
            elif hasattr(codec_context, "width"):
                self._width = codec_context.width
                self._height = codec_context.height
            else:
                logger.warning("Invalid codec context, using defaults")
                self._width = 1920
                self._height = 1080

            # Call init callback
            if self._on_init_callback is not None:
                try:
                    self._on_init_callback(self._width, self._height)
                except Exception as e:
                    logger.error(f"Error in init callback: {e}")

            logger.info(f"Screen initialized: {self._width}x{self._height}")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize screen: {e}")
            return False

    def close(self) -> None:
        """Close screen and cleanup."""
        # Clear DelayBuffer reference
        self._delay_buffer = None
        logger.info(f"Screen closed (received: {self._frames_received}, shown: {self._frames_shown})")

    def push(self, frame: Any) -> bool:
        """
        Push a decoded frame to the screen.

        NOTE: The actual frame storage is in the DelayBuffer, which is managed by the decoder.
        This method tracks statistics and triggers callback to notify video_window that
        a new frame is available in DelayBuffer.

        Args:
            frame: Decoded frame (numpy array or AVFrame) - IGNORED (already in DelayBuffer)

        Returns:
            True if successful
        """
        try:
            # Track statistics - frame is already in DelayBuffer
            self._frames_received += 1

            # Log periodically for debugging (use DEBUG level to reduce console noise)
            if self._frames_received % 60 == 1:
                logger.debug(f"[Screen] push called (count={self._frames_received})")

            # CRITICAL: Call callback to notify video_window that new frame is available
            # The callback is update_frame() which just sets _has_new_frame=True
            # Frame data is NOT passed - video_window consumes from DelayBuffer directly
            if self._on_frame_callback is not None:
                try:
                    # Pass None to indicate "new frame available, consume from DelayBuffer"
                    self._on_frame_callback(None)
                except Exception as e:
                    logger.error(f"Error in frame callback: {e}")

            return True

        except Exception as e:
            logger.error(f"Error processing frame: {e}")
            return False

    def get_frame(self) -> Optional[Any]:
        """
        Get the current frame without removing it.

        DEPRECATED: Frames are now consumed directly from DelayBuffer by video_window.
        This method returns frame from DelayBuffer without consuming.

        Returns:
            Current frame or None
        """
        if self._delay_buffer is not None:
            return self._delay_buffer.get_nowait()
        return None

    def consume_frame(self) -> Optional[Any]:
        """
        Get and consume the current frame.

        DEPRECATED: Frames are now consumed directly from DelayBuffer by video_window.

        Returns:
            Current frame or None
        """
        if self._delay_buffer is not None:
            frame = self._delay_buffer.consume()
            if frame is not None:
                self._frames_shown += 1
            return frame
        return None

    def set_delay_buffer(self, delay_buffer: 'DelayBuffer') -> None:
        """
        Set the DelayBuffer reference for direct access by video_window.

        This allows video_window to consume frames directly from the DelayBuffer
        instead of going through Screen's frame storage, eliminating the
        multi-buffer synchronization problem.

        Args:
            delay_buffer: The DelayBuffer from VideoDecoder
        """
        self._delay_buffer = delay_buffer
        logger.debug("Screen DelayBuffer reference set")

    def get_delay_buffer(self) -> Optional['DelayBuffer']:
        """
        Get the DelayBuffer reference.

        Returns:
            The DelayBuffer from VideoDecoder, or None if not set
        """
        return self._delay_buffer

    @property
    def width(self) -> int:
        """Get screen width."""
        return self._width

    @property
    def height(self) -> int:
        """Get screen height."""
        return self._height
