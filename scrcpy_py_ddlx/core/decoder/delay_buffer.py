"""
scrcpy_py_ddlx/core/decoder/delay_buffer.py

Single-frame delay buffer for minimal latency video decoding.

This module implements the DelayBuffer class, which matches the official
scrcpy delay_buffer design for thread-safe frame updates with minimal latency.
"""

import logging
from threading import Lock
from typing import Optional, Tuple


logger = logging.getLogger(__name__)


__all__ = ['DelayBuffer']


class DelayBuffer:
    """
    Single-frame delay buffer matching official scrcpy delay_buffer design.

    Official scrcpy uses a single-frame buffer with tmp_frame for atomic swap.
    This prevents tearing and ensures thread-safe frame updates.

    Based on: scrcpy/app/src/frame_buffer.c

    Thread-safe with consumed flag to prevent overwriting frames during rendering.
    """

    def __init__(self):
        """Initialize an empty delay buffer with tmp_frame mechanism."""
        self._pending_frame = None      # Current pending frame (equivalent to pending_frame)
        self._tmp_frame = None          # Temporary frame for atomic swap (equivalent to tmp_frame)
        self._consumed = True           # Track if frame has been consumed
        self._lock = Lock()

    def push(self, frame) -> Tuple[bool, bool]:
        """
        Push a frame to the buffer.

        CRITICAL: Direct assignment (no tmp_frame copy).
        consume() returns a copy, so we don't need to copy here.
        This minimizes copying to only where necessary.

        Args:
            frame: Frame to push (numpy array or any object)

        Returns:
            Tuple of (success, previous_skipped)
        """
        with self._lock:
            # Check if previous frame was consumed BEFORE replacing
            previous_skipped = not self._consumed

            # Direct assignment (consume() will make a copy for the renderer)
            self._pending_frame = frame

            # Reset consumed flag to indicate new frame is available
            self._consumed = False

            return True, previous_skipped

    def consume(self) -> Optional:
        """
        Consume the frame from buffer, marking it as consumed.

        CRITICAL: Returns a COPY of the frame to prevent data corruption.
        The renderer may take time to upload the frame to GPU (glTexSubImage2D),
        during which the decoder may overwrite pending_frame. By returning a copy,
        we ensure the renderer has stable data that won't be modified.

        This is the key fix that made 135752.log stable.

        Returns:
            A copy of the current frame, or None if buffer is empty
        """
        with self._lock:
            # Official scrcpy has: assert(!fb->pending_frame_consumed)
            # We'll just check and log instead of crashing
            if self._consumed:
                logger.debug("Attempted to consume already-consumed frame")
                return None

            # CRITICAL: Copy the frame data
            # Without this, the decoder thread may overwrite the frame while
            # the renderer is uploading it to GPU, causing visual corruption
            if self._pending_frame is not None and hasattr(self._pending_frame, 'copy'):
                frame = self._pending_frame.copy()
            else:
                frame = self._pending_frame

            # Mark as consumed (allows new frame to replace it)
            self._consumed = True

            return frame

    def pop(self) -> Optional:
        """
        Pop the current frame from buffer.

        Args:
            timeout: Ignored (for compatibility with Queue interface)

        Returns:
            The current frame, or None if buffer is empty
        """
        return self.consume()

    def get_nowait(self) -> Optional:
        """
        Get the current frame without waiting or marking as consumed.

        Returns:
            The current frame, or None if buffer is empty
        """
        with self._lock:
            return self._pending_frame

    def clear(self) -> None:
        """Clear the buffer."""
        with self._lock:
            self._pending_frame = None
            self._tmp_frame = None
            self._consumed = True

    def is_empty(self) -> bool:
        """Check if buffer is empty."""
        with self._lock:
            return self._pending_frame is None

    def qsize(self) -> int:
        """
        Get the number of frames in buffer.

        Returns:
            0 if empty, 1 if has frame
        """
        with self._lock:
            return 1 if self._pending_frame is not None else 0
