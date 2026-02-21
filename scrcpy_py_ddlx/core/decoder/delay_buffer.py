"""
scrcpy_py_ddlx/core/decoder/delay_buffer.py

Single-frame delay buffer for minimal latency video decoding.

This module implements the DelayBuffer class, which matches the official
scrcpy delay_buffer design for thread-safe frame updates with minimal latency.

Features:
- Event-driven notification (eliminates polling latency)
- Single-frame buffer with consumed flag
- Thread-safe with Condition variable
"""

import logging
import time
from threading import Lock, Condition
from typing import Optional, Tuple, NamedTuple


logger = logging.getLogger(__name__)


__all__ = ['DelayBuffer', 'FrameWithMetadata']


class FrameWithMetadata(NamedTuple):
    """Frame with metadata for latency tracking."""
    frame: any
    packet_id: int = -1
    pts: int = 0  # Presentation timestamp from device (nanoseconds)
    capture_time: float = 0.0  # Time when frame was decoded on PC (seconds)
    udp_recv_time: float = 0.0  # Time when UDP packet was received (seconds) - for TRUE e2e latency
    send_time_ns: int = 0  # Device send time in nanoseconds (for full E2E latency)
    width: int = 0  # Frame width in pixels (for rotation detection)
    height: int = 0  # Frame height in pixels (for rotation detection)


class DelayBuffer:
    """
    Single-frame delay buffer with event-driven notification.

    Official scrcpy uses a single-frame buffer with tmp_frame for atomic swap.
    This prevents tearing and ensures thread-safe frame updates.

    Enhanced with Condition variable for event-driven consumption,
    eliminating fixed polling latency.

    Based on: scrcpy/app/src/frame_buffer.c
    """

    def __init__(self):
        """Initialize an empty delay buffer with event notification."""
        self._pending_frame: Optional[FrameWithMetadata] = None  # Current pending frame with metadata
        self._tmp_frame = None          # Temporary frame for atomic swap (equivalent to tmp_frame)
        self._consumed = True           # Track if frame has been consumed
        self._lock = Lock()
        self._condition = Condition(self._lock)  # Event-driven notification
        self._lock_wait_count = 0  # Track lock contention
        self._total_lock_wait_time = 0.0  # Total time spent waiting for lock

    def push(self, frame, packet_id: int = -1, pts: int = 0, capture_time: float = 0.0,
             udp_recv_time: float = 0.0, send_time_ns: int = 0, width: int = 0, height: int = 0) -> Tuple[bool, bool]:
        """
        Push a frame to the buffer and notify waiting consumers.

        CRITICAL: Direct assignment (no tmp_frame copy).
        consume() returns a copy, so we don't need to copy here.
        This minimizes copying to only where necessary.

        Args:
            frame: Frame to push (numpy array or any object)
            packet_id: Packet ID for latency tracking (default: -1 for no tracking)
            pts: Presentation timestamp from device (nanoseconds)
            capture_time: Time when frame was decoded on PC (seconds)
            udp_recv_time: Time when UDP packet was received (seconds) - for TRUE e2e latency
            send_time_ns: Device send time in nanoseconds (for full E2E latency)
            width: Frame width in pixels (for rotation detection)
            height: Frame height in pixels (for rotation detection)

        Returns:
            Tuple of (success, previous_skipped)
        """
        with self._condition:
            # Check if previous frame was consumed BEFORE replacing
            previous_skipped = not self._consumed

            # Direct assignment (consume() will make a copy for the renderer)
            self._pending_frame = FrameWithMetadata(
                frame=frame, packet_id=packet_id, pts=pts,
                capture_time=capture_time, udp_recv_time=udp_recv_time,
                send_time_ns=send_time_ns, width=width, height=height
            )

            # Reset consumed flag to indicate new frame is available
            self._consumed = False

            # Notify waiting consumer (event-driven, eliminates polling latency)
            self._condition.notify()

            return True, previous_skipped

    def wait_for_frame(self, timeout: float = 0.001) -> Optional[FrameWithMetadata]:
        """
        Wait for a new frame to be available, then consume it.

        This is the event-driven alternative to polling has_new_frame() + consume().
        Eliminates fixed polling latency by using Condition variable.

        CRITICAL OPTIMIZATION: Returns frame reference, NOT a copy!
        The caller (frame_sender) writes directly to SimpleSHM, which does its own copy.
        This eliminates duplicate copying and prevents lock contention.

        Args:
            timeout: Maximum time to wait in seconds (default: 1ms for low latency)

        Returns:
            FrameWithMetadata if available, None if timeout
        """
        with self._condition:
            # Wait for notification if no frame available
            if self._consumed or self._pending_frame is None:
                self._condition.wait(timeout)

            # Check again after wait
            if self._consumed or self._pending_frame is None:
                return None

            # Get frame reference and metadata - NO COPY HERE!
            # The caller must process the frame quickly before decoder overwrites it
            raw_frame = self._pending_frame.frame
            packet_id = self._pending_frame.packet_id
            pts = self._pending_frame.pts
            capture_time = self._pending_frame.capture_time
            udp_recv_time = self._pending_frame.udp_recv_time

            # Mark as consumed immediately
            self._consumed = True

        # Return frame reference (caller must handle it quickly)
        return FrameWithMetadata(
            frame=raw_frame, packet_id=packet_id, pts=pts,
            capture_time=capture_time, udp_recv_time=udp_recv_time
        )

    def consume(self) -> Optional[FrameWithMetadata]:
        """
        Consume the frame from buffer, marking it as consumed.

        CRITICAL OPTIMIZATION: Returns frame reference, NOT a copy!
        Same reason as wait_for_frame() - prevents lock contention.

        Returns:
            FrameWithMetadata with frame reference, or None if buffer is empty
        """
        with self._condition:
            if self._consumed or self._pending_frame is None:
                return None

            # Get frame reference and metadata - NO COPY
            raw_frame = self._pending_frame.frame
            packet_id = self._pending_frame.packet_id
            pts = self._pending_frame.pts
            capture_time = self._pending_frame.capture_time
            udp_recv_time = self._pending_frame.udp_recv_time

            # Mark as consumed immediately
            self._consumed = True

        return FrameWithMetadata(
            frame=raw_frame, packet_id=packet_id, pts=pts,
            capture_time=capture_time, udp_recv_time=udp_recv_time
        )

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

    def has_new_frame(self) -> bool:
        """
        Check if there is a new frame available to consume.

        Returns:
            True if there is a frame that hasn't been consumed yet
        """
        with self._lock:
            return self._pending_frame is not None and not self._consumed
