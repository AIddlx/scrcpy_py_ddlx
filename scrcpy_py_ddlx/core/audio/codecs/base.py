"""Audio codec implementations using PyAV/FFmpeg."""

import logging
from abc import ABC, abstractmethod
from typing import Optional

try:
    import av
    AV_AVAILABLE = True
except ImportError:
    av = None
    AV_AVAILABLE = False

logger = logging.getLogger(__name__)

__all__ = [
    'AudioCodecBase',
    'OpusDecoder',
    'AACDecoder',
    'FLACDecoder',
    'RAWDecoder',
    'create_audio_decoder'
]


class AudioCodecBase(ABC):
    """
    Base class for audio codec implementations.
    """

    # Codec type constants
    RAW = 0
    OPUS = 1
    AAC = 2
    FDK_AAC = 3
    FLAC = 4

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        codec_id: int = OPUS
    ):
        self._sample_rate = sample_rate
        self._channels = channels
        self._codec_id = codec_id

    @abstractmethod
    def decode(self, data: bytes) -> bytes:
        """Decode audio data."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Reset decoder state."""
        pass

    @property
    def sample_rate(self) -> int:
        """Get sample rate."""
        return self._sample_rate

    @property
    def channels(self) -> int:
        """Get number of channels."""
        return self._channels

    @property
    def codec_id(self) -> int:
        """Get codec ID."""
        return self._codec_id


class PyAVDecoder(AudioCodecBase):
    """
    Base class for PyAV-based audio decoders.

    This class provides common functionality for all PyAV-based decoders.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        codec_id: int = None,
        codec_name: str = 'opus'
    ):
        if codec_id is None:
            codec_id = AudioCodecBase.OPUS
        super().__init__(sample_rate, channels, codec_id)
        self._codec_name = codec_name
        self._codec_context: Optional[av.CodecContext] = None
        self._actual_sample_rate: Optional[int] = None  # Actual rate from stream
        self._actual_channels: Optional[int] = None      # Actual channels from stream
        self._stream_analyzed: bool = False             # Whether we've analyzed the stream
        self._initialize_decoder()

    def _initialize_decoder(self) -> None:
        """Initialize the PyAV codec context."""
        if not AV_AVAILABLE:
            logger.warning("PyAV not available, audio decoding will be passthrough")
            return

        try:
            # Create decoder
            self._codec_context = av.CodecContext.create(self._codec_name, 'r')

            # Set sample rate
            self._codec_context.sample_rate = self._sample_rate

            # For audio codecs, channels are determined by the stream itself
            # We don't set channels directly - the decoder will detect from the stream
            # Sample format is also auto-detected from the stream
            # Note: PyAV audio codec contexts don't support setting sample_fmt directly

            logger.info(
                f"Initialized {self._codec_name} audio decoder: "
                f"{self._sample_rate}Hz, {self._channels} channels"
            )

        except Exception as e:
            logger.error(f"Failed to initialize {self._codec_name} decoder: {e}")
            self._codec_context = None

    def decode(self, data: bytes) -> bytes:
        """
        Decode audio data using PyAV.

        Args:
            data: Encoded audio packet

        Returns:
            PCM audio data as bytes (float32 format - native OPUS output)
        """
        if self._codec_context is None or not data:
            # Return empty bytes if decoder not available or empty data
            return b''

        try:
            # Create PyAV packet
            av_packet = av.Packet(data)

            # Decode packet
            output_frames = []
            frame_count = 0
            for frame in self._codec_context.decode(av_packet):
                frame_count += 1
                # Extract actual audio parameters from first frame
                if not self._stream_analyzed and hasattr(frame, 'sample_rate'):
                    self._actual_sample_rate = frame.sample_rate
                    self._actual_channels = frame.channels if hasattr(frame, 'channels') else 2
                    self._stream_analyzed = True
                    logger.info(
                        f"[CODEC] Detected audio parameters from stream: "
                        f"{self._actual_sample_rate}Hz, {self._actual_channels} channels, "
                        f"format={frame.format.name if hasattr(frame, 'format') else 'N/A'}"
                    )

                # Convert frame to float32 (native OPUS format)
                if hasattr(frame, 'to_ndarray'):
                    import numpy as np

                    # Get native float32 data (no conversion)
                    audio_data = frame.to_ndarray()  # Already float32 from OPUS

                    # Interleave channels if planar
                    if audio_data.ndim == 2:
                        # Planar audio - interleave channels
                        audio_data = audio_data.T.flatten()
                    else:
                        audio_data = audio_data.flatten()

                    # Convert to bytes (float32, little-endian)
                    samples = audio_data.tobytes()
                    output_frames.append(samples)

            # Combine all frames
            if output_frames:
                result = b''.join(output_frames)
                if frame_count > 0:
                    sample_count = len(result) // 4  # float32 = 4 bytes per sample
                    logger.info(f"[AUDIO] Decoded {sample_count} samples ({len(result)} bytes)")
                return result

            # No frames decoded (empty packet or config packet)
            return b''

        except Exception as e:
            # Silently skip decode errors (normal for empty/silent audio)
            logger.debug(f"Audio decode error (skipping packet): {e}")
            return b''

    def reset(self) -> None:
        """Reset decoder state."""
        if self._codec_context is not None:
            try:
                self._codec_context.close()
            except Exception as e:
                logger.debug(f"Error closing codec context: {e}")

        self._stream_analyzed = False
        self._actual_sample_rate = None
        self._actual_channels = None
        self._initialize_decoder()

    @property
    def detected_sample_rate(self) -> int:
        """Get the actual sample rate detected from the audio stream."""
        return self._actual_sample_rate if self._actual_sample_rate is not None else self._sample_rate

    @property
    def detected_channels(self) -> int:
        """Get the actual number of channels detected from the audio stream."""
        return self._actual_channels if self._actual_channels is not None else self._channels


class OpusDecoder(PyAVDecoder):
    """
    OPUS codec decoder using PyAV/FFmpeg.

    OPUS is a low-latency audio codec designed for real-time applications.
    It supports constant and variable bitrate encoding, and frame sizes
    from 2.5 ms to 60 ms.

    Example:
        >>> decoder = OpusDecoder(sample_rate=48000, channels=2)
        >>> pcm_data = decoder.decode(opus_packet)
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2
    ):
        super().__init__(sample_rate, channels, AudioCodecBase.OPUS, 'opus')


class AACDecoder(PyAVDecoder):
    """
    AAC (Advanced Audio Coding) codec decoder using PyAV/FFmpeg.

    AAC is a standardized, lossy compression and encoding scheme for digital audio.
    It provides good quality at moderate bitrates.

    Example:
        >>> decoder = AACDecoder(sample_rate=44100, channels=2)
        >>> pcm_data = decoder.decode(aac_packet)
    """

    def __init__(
        self,
        sample_rate: int = 48000,  # scrcpy uses 48000 by default
        channels: int = 2
    ):
        super().__init__(sample_rate, channels, AudioCodecBase.AAC, 'aac')


class FLACDecoder(PyAVDecoder):
    """
    FLAC (Free Lossless Audio Codec) decoder using PyAV/FFmpeg.

    FLAC is a lossless audio codec that compresses audio without any loss
    in quality.

    Example:
        >>> decoder = FLACDecoder(sample_rate=48000, channels=2)
        >>> pcm_data = decoder.decode(flac_frame)
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2
    ):
        super().__init__(sample_rate, channels, AudioCodecBase.FLAC, 'flac')


class RAWDecoder(AudioCodecBase):
    """
    RAW (PCM) audio codec handler.

    RAW audio is uncompressed PCM data that doesn't require decoding.

    Example:
        >>> decoder = RAWDecoder(sample_rate=48000, channels=2)
        >>> pcm_data = decoder.decode(raw_data)  # Returns data unchanged
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 2,
        sample_format: str = 's16'  # signed 16-bit little-endian
    ):
        super().__init__(sample_rate, channels, self.RAW)
        self._sample_format = sample_format

        logger.info(
            f"RAW audio handler initialized: {sample_rate}Hz, "
            f"{channels} channels, format={sample_format}"
        )

    def decode(self, data: bytes) -> bytes:
        """
        Pass through RAW audio data (no decoding needed).

        Args:
            data: PCM audio data

        Returns:
            The same PCM audio data (unchanged)
        """
        # RAW audio doesn't need decoding
        return data

    def reset(self) -> None:
        """Reset RAW handler state (no-op for RAW)."""
        # No state to reset for RAW audio
        logger.debug("RAW audio handler reset")


def create_audio_decoder(
    codec_id: int,
    sample_rate: int = 48000,
    channels: int = 2
) -> AudioCodecBase:
    """
    Factory function to create audio decoder based on codec ID.

    Args:
        codec_id: Audio codec type (RAW, OPUS, AAC, FDK_AAC, FLAC)
        sample_rate: Sample rate in Hz
        channels: Number of channels

    Returns:
        Audio decoder instance

    Example:
        >>> decoder = create_audio_decoder(AudioCodecBase.OPUS)
        >>> pcm_data = decoder.decode(audio_packet)
    """
    if codec_id == AudioCodecBase.RAW:
        return RAWDecoder(sample_rate, channels)
    elif codec_id == AudioCodecBase.OPUS:
        return OpusDecoder(sample_rate, channels)
    elif codec_id == AudioCodecBase.AAC:
        return AACDecoder(sample_rate, channels)
    elif codec_id == AudioCodecBase.FDK_AAC:
        # FDK_AAC uses AAC decoder
        return AACDecoder(sample_rate, channels)
    elif codec_id == AudioCodecBase.FLAC:
        return FLACDecoder(sample_rate, channels)
    else:
        logger.warning(f"Unknown codec ID {codec_id}, defaulting to OPUS")
        return OpusDecoder(sample_rate, channels)
