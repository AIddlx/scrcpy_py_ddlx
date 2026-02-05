"""Audio/video synchronization framework."""

import logging
from typing import Optional, List

logger = logging.getLogger(__name__)

__all__ = ['PTSComparator', 'AudioDelayAdjuster']


class PTSComparator:
    """
    Compare audio and video PTS (Presentation Time Stamps) to calculate delay.

    This class helps maintain audio/video synchronization by comparing
    the presentation timestamps of audio and video frames.

    Example:
        >>> comparator = PTSComparator()
        >>> delay = comparator.get_delay(video_pts=1000, audio_pts=1050)
        >>> print(f"Audio leads video by {delay} units")
    """

    _video_pts_history: List[int]
    _audio_pts_history: List[int]
    _history_size: int

    def __init__(self) -> None:
        """Initialize the PTS comparator."""
        self._video_pts_history = []
        self._audio_pts_history = []
        self._history_size = 10

    def get_delay(self, video_pts: int, audio_pts: int) -> int:
        """
        Calculate the delay between audio and video.

        Args:
            video_pts: Video presentation timestamp
            audio_pts: Audio presentation timestamp

        Returns:
            Delay in timestamp units (audio_pts - video_pts)
            Positive value means audio is ahead of video
            Negative value means video is ahead of audio
        """
        delay = audio_pts - video_pts

        # Store history for smoothing
        self._update_history(video_pts, audio_pts)

        return delay

    def get_smoothed_delay(self, video_pts: int, audio_pts: int) -> int:
        """
        Calculate smoothed delay using history.

        Args:
            video_pts: Video presentation timestamp
            audio_pts: Audio presentation timestamp

        Returns:
            Smoothed delay value
        """
        self._update_history(video_pts, audio_pts)

        if len(self._video_pts_history) < 3:
            return audio_pts - video_pts

        # Calculate average delay over recent history
        delays = []
        for v_pts, a_pts in zip(self._video_pts_history, self._audio_pts_history):
            delays.append(a_pts - v_pts)

        return sum(delays) // len(delays)

    def _update_history(self, video_pts: int, audio_pts: int) -> None:
        """Update PTS history for smoothing."""
        self._video_pts_history.append(video_pts)
        self._audio_pts_history.append(audio_pts)

        # Keep history size limited
        if len(self._video_pts_history) > self._history_size:
            self._video_pts_history.pop(0)
            self._audio_pts_history.pop(0)

    def reset(self) -> None:
        """Reset the comparator history."""
        self._video_pts_history.clear()
        self._audio_pts_history.clear()


class AudioDelayAdjuster:
    """
    Adjust audio playback delay for synchronization.

    This class provides mechanisms to adjust audio playback timing
    to maintain synchronization with video.

    Example:
        >>> adjuster = AudioDelayAdjuster()
        >>> adjuster.adjust(delay_ms=50)  # Add 50ms delay to audio
        >>> adjuster.adjust(delay_ms=-25)  # Reduce delay by 25ms
    """

    _current_delay_ms: int
    _target_delay_ms: int
    _max_delay_ms: int
    _min_delay_ms: int

    def __init__(self) -> None:
        """Initialize the audio delay adjuster."""
        self._current_delay_ms = 0
        self._target_delay_ms = 0
        self._max_delay_ms = 500  # Maximum 500ms delay
        self._min_delay_ms = -200  # Maximum 200ms advance

    def adjust(self, delay_ms: int) -> bool:
        """
        Adjust audio playback delay.

        Args:
            delay_ms: Delay adjustment in milliseconds
                     Positive = delay audio
                     Negative = advance audio

        Returns:
            True if adjustment was applied, False if adjustment was rejected
        """
        new_delay = self._target_delay_ms + delay_ms

        # Clamp to valid range
        if new_delay > self._max_delay_ms:
            logger.warning(
                f"Requested delay {new_delay}ms exceeds maximum {self._max_delay_ms}ms"
            )
            self._target_delay_ms = self._max_delay_ms
            return False

        if new_delay < self._min_delay_ms:
            logger.warning(
                f"Requested delay {new_delay}ms below minimum {self._min_delay_ms}ms"
            )
            self._target_delay_ms = self._min_delay_ms
            return False

        self._target_delay_ms = new_delay
        logger.debug(f"Audio delay adjusted to {self._target_delay_ms}ms")

        # TODO: Implement actual delay adjustment
        # This would typically involve:
        # - Adjusting audio device buffer size
        # - Adding/dropping audio frames
        # - Adjusting playback rate
        self._current_delay_ms = self._target_delay_ms

        return True

    def set_delay(self, delay_ms: int) -> bool:
        """
        Set absolute audio delay.

        Args:
            delay_ms: Absolute delay in milliseconds

        Returns:
            True if delay was set, False if value was out of range
        """
        if delay_ms > self._max_delay_ms or delay_ms < self._min_delay_ms:
            logger.warning(
                f"Delay {delay_ms}ms out of range [{self._min_delay_ms}, {self._max_delay_ms}]"
            )
            return False

        self._target_delay_ms = delay_ms
        self._current_delay_ms = delay_ms
        logger.debug(f"Audio delay set to {delay_ms}ms")

        # TODO: Implement actual delay adjustment
        return True

    def get_delay(self) -> int:
        """Get current audio delay in milliseconds."""
        return self._current_delay_ms

    def reset(self) -> None:
        """Reset delay to zero."""
        self._current_delay_ms = 0
        self._target_delay_ms = 0
        logger.debug("Audio delay reset to 0ms")
