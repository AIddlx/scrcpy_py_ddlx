"""
scrcpy_py_ddlx/core/audio/decoder.py

Audio decoder for scrcpy audio streams.

This module provides a threaded audio decoder wrapper that uses the
codec implementations from audio.codecs.base. It handles packet queuing
and output to frame sinks like AudioPlayer.
"""

import logging
from queue import Queue, Empty
import threading
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from scrcpy_py_ddlx.core.av_player import FrameSink

from scrcpy_py_ddlx.core.audio.codecs.base import (
    create_audio_decoder,
    AudioCodecBase,
)
from scrcpy_py_ddlx.core.audio.sync import PTSComparator, AudioDelayAdjuster

logger = logging.getLogger(__name__)


__all__ = ["AudioDecoder"]


class AudioDecoder:
    """
    Threaded audio decoder for scrcpy audio streams.

    This decoder wraps the audio codec implementations in a threaded
    architecture, managing packet queuing and output to frame sinks.

    Based on official scrcpy audio decoder.

    Example:
        >>> decoder = AudioDecoder(sample_rate=48000, channels=2, codec_id=AudioDecoder.OPUS)
        >>> decoder.start()
        >>> for packet in audio_packets:
        ...     decoder.push(packet)
        >>> decoder.stop()
    """

    # Audio codec types (matching AudioCodecBase)
    RAW = 0
    OPUS = 1
    AAC = 2
    FDK_AAC = 3
    FLAC = 4

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        audio_codec: int = OPUS,
        frame_sink: Optional["FrameSink"] = None,
        packet_queue: Optional[Queue] = None,
        enable_sync: bool = False,
    ) -> None:
        """
        Initialize the audio decoder.

        Args:
            sample_rate: Audio sample rate (Hz)
            channels: Number of audio channels
            audio_codec: Audio codec type (RAW, OPUS, AAC, FDK_AAC, FLAC)
            frame_sink: Optional frame sink for decoded frames (e.g., AudioPlayer)
            packet_queue: Optional external packet queue (creates internal queue if None)
            enable_sync: Enable audio/video synchronization tracking
        """
        self._sample_rate = sample_rate
        self._channels = channels
        self._audio_codec = audio_codec
        self._frame_sink = frame_sink

        # Create the codec implementation
        self._codec: Optional[AudioCodecBase] = create_audio_decoder(
            audio_codec, sample_rate, channels
        )

        # Decoder state
        self._running = False

        # Pause/Resume state (for runtime control without reconnecting)
        self._paused = False
        self._pause_event = threading.Event()

        # Use external queue if provided, otherwise create internal queue
        self._packet_queue: Queue = (
            packet_queue if packet_queue is not None else Queue(maxsize=100)
        )

        # Audio/Video synchronization
        self._enable_sync = enable_sync
        self._current_pts: int = 0
        self._pts_comparator: Optional[PTSComparator] = None
        self._delay_adjuster: Optional[AudioDelayAdjuster] = None

    def pause(self) -> None:
        """
        Pause decoding (stop CPU consumption).

        This method is called when audio is disabled at runtime.
        The decoder stops processing packets but doesn't close,
        allowing resume without reconnecting.
        """
        if self._paused:
            return

        self._paused = True
        self._pause_event.clear()  # Block decode loop
        logger.info("AudioDecoder paused")

    def resume(self) -> None:
        """
        Resume decoding.

        This method is called when audio is enabled at runtime.
        """
        if not self._paused:
            return

        self._paused = False
        self._pause_event.set()  # Unblock decode loop
        logger.info("AudioDecoder resumed")

        if self._enable_sync:
            self._pts_comparator = PTSComparator()
            self._delay_adjuster = AudioDelayAdjuster()
            logger.info("Audio/video synchronization enabled")

        logger.info(
            f"AudioDecoder created: {self._get_codec_name()} codec, "
            f"{self._sample_rate}Hz, {self._channels} channels"
        )

    def _get_codec_name(self) -> str:
        """Get the codec name for logging."""
        if self._audio_codec == self.RAW:
            return "RAW"
        elif self._audio_codec == self.OPUS:
            return "OPUS"
        elif self._audio_codec == self.AAC:
            return "AAC"
        elif self._audio_codec == self.FDK_AAC:
            return "FDK_AAC"
        elif self._audio_codec == self.FLAC:
            return "FLAC"
        else:
            return "UNKNOWN"

    def start(self) -> None:
        """Start the decoder thread."""
        if self._running:
            logger.warning("Audio decoder is already running")
            return

        self._running = True
        self._decoder_thread = threading.Thread(target=self._decode_loop, daemon=True)
        self._decoder_thread.start()

        # Initialize frame sink
        if self._frame_sink is not None:
            codec_ctx = {
                "sample_rate": self._sample_rate,
                "channels": self._channels,
                "codec_type": "audio",
            }
            self._frame_sink.open(codec_ctx)
            if hasattr(self._frame_sink, "start"):
                self._frame_sink.start()

        logger.info("Audio decoder thread started")

    def stop(self) -> None:
        """Stop the decoder and wait for the thread to finish."""
        if not self._running:
            return

        logger.info("Stopping audio decoder...")
        self._running = False

        # Push a None to unblock the queue
        try:
            self._packet_queue.put(None, timeout=1.0)
        except Exception:
            pass

        # Wait for thread to finish
        if self._decoder_thread is not None:
            self._decoder_thread.join(timeout=5.0)
            if self._decoder_thread.is_alive():
                logger.warning("Audio decoder thread did not stop gracefully")

        # Close frame sink
        if self._frame_sink is not None:
            if hasattr(self._frame_sink, "stop"):
                self._frame_sink.stop()
            self._frame_sink.close()

        # Reset codec
        if self._codec is not None:
            self._codec.reset()

        # Clear queue
        while not self._packet_queue.empty():
            try:
                self._packet_queue.get_nowait()
            except Exception:
                break

        logger.info("Audio decoder stopped")

    def push(self, packet: bytes) -> None:
        """
        Push an audio packet to the decoder.

        Args:
            packet: Raw audio packet data
        """
        if not self._running:
            logger.warning("Cannot push packet: audio decoder is not running")
            return

        try:
            self._packet_queue.put(packet, block=True, timeout=1.0)
        except Exception as e:
            logger.warning(f"Failed to push audio packet to queue: {e}")

    def _decode_loop(self) -> None:
        """Main decoder loop running in a separate thread."""
        logger.debug("Audio decoder loop started")

        while self._running:
            try:
                # Get packet from queue
                packet = self._packet_queue.get(block=True, timeout=0.1)

                # None is a signal to stop
                if packet is None:
                    break

                # Check pause state before decoding
                if self._paused:
                    self._pause_event.wait()
                    continue

                # Decode the packet
                self._decode_packet(packet)

            except Empty:
                # Queue timeout is normal - no packets available yet
                continue
            except Exception as e:
                if self._running:
                    logger.error(f"Error in audio decoder loop: {e}")
                continue

        logger.debug("Audio decoder loop finished")

    def _decode_packet(self, packet: bytes) -> None:
        """
        Decode a single audio packet.

        Args:
            packet: The audio packet to decode
        """
        try:
            if self._codec is None:
                logger.warning("Codec not initialized, skipping packet")
                return

            # Skip empty packets (normal when device is not playing audio)
            if not packet or len(packet) == 0:
                return

            # Log packet reception for debugging
            logger.info(f"[AUDIO] Decoding packet: {len(packet)} bytes")

            # Decode packet using codec implementation
            decoded_data = self._codec.decode(packet)

            # Log decoded samples
            if decoded_data:
                sample_count = len(decoded_data) // 4  # float32 = 4 bytes per sample
                logger.info(
                    f"[AUDIO] Decoded {sample_count} samples ({len(decoded_data)} bytes)"
                )

            # Send to frame sink if available
            if self._frame_sink is not None and decoded_data:
                try:
                    self._frame_sink.push(decoded_data)
                    logger.info(f"[AUDIO] Sent {len(decoded_data)} bytes to player")
                except Exception as e:
                    logger.error(f"Error pushing decoded data to sink: {e}")

        except Exception as e:
            # Silently skip decoding errors (normal for empty/silent audio)
            # Only log at debug level to avoid spam
            logger.debug(f"Skipping audio packet (decode error): {e}")

    # ========================================================================
    # Audio/Video Synchronization Methods
    # ========================================================================

    def sync_with_video(self, video_pts: int) -> int:
        """
        Synchronize audio with video by comparing timestamps.

        Args:
            video_pts: Video presentation timestamp

        Returns:
            Delay adjustment in milliseconds (positive = delay audio, negative = advance audio)
        """
        if not self._enable_sync or self._pts_comparator is None:
            return 0

        # Calculate delay between audio and video
        delay = self._pts_comparator.get_smoothed_delay(video_pts, self._current_pts)

        # Adjust audio delay if needed
        if (
            self._delay_adjuster is not None and abs(delay) > 10
        ):  # Only adjust if > 10ms off
            # Convert PTS units to milliseconds (assuming PTS is in microseconds)
            delay_ms = delay // 1000
            self._delay_adjuster.adjust(delay_ms)
            return delay_ms

        return 0

    def set_audio_pts(self, pts: int) -> None:
        """
        Set the current audio PTS.

        Args:
            pts: Audio presentation timestamp
        """
        self._current_pts = pts

    def get_audio_pts(self) -> int:
        """Get the current audio presentation timestamp."""
        return self._current_pts

    def get_sync_delay(self) -> int:
        """
        Get the current audio delay for synchronization.

        Returns:
            Current delay in milliseconds
        """
        if self._delay_adjuster is not None:
            return self._delay_adjuster.get_delay()
        return 0

    def set_sync_delay(self, delay_ms: int) -> bool:
        """
        Set the audio delay directly.

        Args:
            delay_ms: Delay in milliseconds

        Returns:
            True if delay was set successfully
        """
        if self._delay_adjuster is not None:
            return self._delay_adjuster.set_delay(delay_ms)
        return False

    def reset_sync(self) -> None:
        """Reset synchronization state."""
        self._current_pts = 0
        if self._pts_comparator is not None:
            self._pts_comparator.reset()
        if self._delay_adjuster is not None:
            self._delay_adjuster.reset()
        logger.debug("Audio/video synchronization reset")

    @property
    def sample_rate(self) -> int:
        """Get sample rate."""
        return self._sample_rate

    @property
    def channels(self) -> int:
        """Get number of channels."""
        return self._channels

    @property
    def codec(self) -> int:
        """Get audio codec type."""
        return self._audio_codec

    @property
    def is_running(self) -> bool:
        """Check if decoder is running."""
        return self._running

    @property
    def codec_impl(self) -> Optional[AudioCodecBase]:
        """Get the underlying codec implementation."""
        return self._codec
