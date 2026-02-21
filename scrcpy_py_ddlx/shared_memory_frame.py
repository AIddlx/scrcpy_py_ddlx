"""
Shared Memory Frame Buffer for low-latency inter-process frame transfer.

Uses multiprocessing.shared_memory (Python 3.8+) to avoid serialization overhead.
"""

import numpy as np
import struct
import time
import logging
from typing import Optional, Tuple
from multiprocessing import shared_memory, Event, Value
import ctypes

logger = logging.getLogger(__name__)


class SharedMemoryFrameBuffer:
    """
    Lock-free shared memory frame buffer for single-producer single-consumer.

    Uses two buffers (double buffering) for minimal latency:
    - Producer writes to one buffer while consumer reads from the other
    - No locks, just atomic index swap

    Frame format in shared memory:
    - 4 bytes: frame index (uint32)
    - 4 bytes: width (int32)
    - 4 bytes: height (int32)
    - 4 bytes: channels (uint32)
    - 8 bytes: timestamp (int64)
    - 4 bytes: packet_id (int32) - for latency tracking
    - N bytes: frame data (RGB/BGR uint8)
    """

    # Header: uint32 + int32 + uint32 + uint32 + int64 + int32 = 4+4+4+4+8+4 = 28 bytes
    HEADER_SIZE = 28
    HEADER_FORMAT = '<IiIIqi'  # Little-endian: uint32, int32, uint32, uint32, int64, int32

    def __init__(self, frame_shape: Tuple[int, int, int], max_fps: int = 60):
        """
        Create shared memory frame buffer.

        Args:
            frame_shape: (height, width, channels)
            max_fps: Maximum expected FPS (for timeout calculations)
        """
        self.frame_shape = frame_shape
        self.height, self.width, self.channels = frame_shape
        self.frame_size = self.height * self.width * self.channels
        self.buffer_size = self.HEADER_SIZE + self.frame_size

        # Double buffering: two buffers
        self.total_size = self.buffer_size * 2

        # Frame index (atomic counter)
        self._frame_index = Value(ctypes.c_int64, 0)

        # Create shared memory
        self.shm = shared_memory.SharedMemory(create=True, size=self.total_size)
        self.name = self.shm.name

        # Events for synchronization (lightweight)
        self._new_frame_event = Event()

        logger.info(f"SharedMemoryFrameBuffer created: {self.width}x{self.height}x{self.channels}, "
                   f"size={self.total_size/1024/1024:.1f}MB, name={self.name}")

    def get_info(self) -> dict:
        """Get buffer info for connecting from another process."""
        return {
            'name': self.name,
            'shape': self.frame_shape,
            'size': self.total_size,
            'buffer_size': self.buffer_size  # Size of each buffer (for double buffering)
        }

    def write_frame(self, frame: np.ndarray, timestamp: float = 0, packet_id: int = -1) -> bool:
        """
        Write frame to shared memory (non-blocking).

        Args:
            frame: BGR numpy array (H, W, 3)
            timestamp: Frame timestamp
            packet_id: Packet ID for latency tracking

        Returns:
            True if written successfully
        """
        if frame is None:
            return False

        # Get current frame index and calculate buffer offset
        with self._frame_index.get_lock():
            frame_idx = self._frame_index.value
            buffer_idx = frame_idx % 2
            offset = buffer_idx * self.buffer_size

        # Ensure frame shape matches
        h, w = frame.shape[:2]
        if h != self.height or w != self.width:
            logger.warning(f"Frame shape mismatch: got {w}x{h}, expected {self.width}x{self.height}")
            # Update stored dimensions
            self.height, self.width = h, w

        # Write frame data FIRST (before header with frame index)
        # This ensures reader sees valid data when frame index changes
        if not frame.flags['C_CONTIGUOUS']:
            frame = np.ascontiguousarray(frame)
        self.shm.buf[offset + self.HEADER_SIZE:offset + self.HEADER_SIZE + frame.nbytes] = frame.tobytes()

        # Write header LAST (with frame index)
        # Reader uses frame index to determine if data is ready
        timestamp_int = int(timestamp * 1000000)  # Convert to microseconds
        header = struct.pack(
            self.HEADER_FORMAT,
            frame_idx & 0xFFFFFFFF,  # Frame index (lower 32 bits)
            w,                  # Width
            h,                  # Height
            frame.shape[2] if len(frame.shape) > 2 else 1,  # Channels
            timestamp_int,      # Timestamp
            packet_id           # Packet ID for latency tracking
        )
        self.shm.buf[offset:offset + self.HEADER_SIZE] = header

        # Increment frame index AFTER writing is complete
        with self._frame_index.get_lock():
            self._frame_index.value = frame_idx + 1

        # Latency tracking
        if packet_id >= 0:
            try:
                from scrcpy_py_ddlx.latency_tracker import get_tracker
                get_tracker().record_shm_write(packet_id)
            except Exception:
                pass

        # Signal new frame
        self._new_frame_event.set()

        return True

    def read_frame(self, timeout: float = 0.1) -> Optional[Tuple[np.ndarray, int, int]]:
        """
        Read latest frame from shared memory.

        Args:
            timeout: Max time to wait for new frame

        Returns:
            Tuple of (BGR numpy array, frame_idx, packet_id) or None if timeout
        """
        # Wait for new frame
        if not self._new_frame_event.wait(timeout):
            return None

        self._new_frame_event.clear()

        # Get current buffer index
        idx = self._frame_index.value
        if idx == 0:
            return None

        # Read from the completed buffer (idx - 1)
        buffer_idx = (idx - 1) % 2
        offset = buffer_idx * self.buffer_size

        # Read header
        header = bytes(self.shm.buf[offset:offset + self.HEADER_SIZE])
        frame_idx, width, height, channels, timestamp, packet_id = struct.unpack(self.HEADER_FORMAT, header)

        # Read frame data
        frame_size = height * width * channels
        frame_bytes = bytes(self.shm.buf[offset + self.HEADER_SIZE:offset + self.HEADER_SIZE + frame_size])

        # Create numpy array (zero-copy view, then copy for safety)
        frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape((height, width, channels)).copy()

        return frame, frame_idx, packet_id

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """Get latest frame without waiting (non-blocking)."""
        return self.read_frame(timeout=0)

    def close(self):
        """Close and cleanup shared memory."""
        try:
            self.shm.close()
            self.shm.unlink()
            logger.info(f"SharedMemoryFrameBuffer closed: {self.name}")
        except Exception as e:
            logger.debug(f"SharedMemoryFrameBuffer close error: {e}")


class SharedMemoryFrameReader:
    """
    Reader side of shared memory frame buffer.

    Connects to an existing shared memory created by SharedMemoryFrameBuffer.
    """

    # Must match SharedMemoryFrameBuffer
    HEADER_SIZE = 28
    HEADER_FORMAT = '<IiIIqi'

    def __init__(self, name: str, shape: Tuple[int, int, int], buffer_size: Optional[int] = None):
        """
        Connect to existing shared memory.

        Args:
            name: Shared memory name
            shape: Expected frame shape (height, width, channels)
            buffer_size: Size of each buffer (from SharedMemoryFrameBuffer.get_info())
        """
        self.name = name
        self.frame_shape = shape
        self.height, self.width, self.channels = shape
        self.frame_size = self.height * self.width * self.channels

        # Use provided buffer_size or calculate from shape
        if buffer_size is not None:
            self.buffer_size = buffer_size
        else:
            self.buffer_size = self.HEADER_SIZE + self.frame_size

        # Connect to existing shared memory
        self.shm = shared_memory.SharedMemory(name=name)

        # Frame tracking
        self._last_frame_idx = -1

        logger.info(f"SharedMemoryFrameReader connected: {name}, shape={shape}, buffer_size={self.buffer_size}")

    def read_frame(self) -> Optional[Tuple[np.ndarray, int, int]]:
        """
        Read latest frame (non-blocking).

        Returns:
            Tuple of (frame, frame_index, packet_id) or None if no new frame
        """
        # Read frame index from both buffers to find the latest
        latest_idx = -1
        latest_offset = 0

        for buf_idx in range(2):
            offset = buf_idx * self.buffer_size
            header = bytes(self.shm.buf[offset:offset + 4])
            frame_idx = struct.unpack('<I', header)[0]

            # Handle wrap-around
            if frame_idx > latest_idx or (self._last_frame_idx > 65000 and frame_idx < 1000):
                latest_idx = frame_idx
                latest_offset = offset

        # Check if this is a new frame
        if latest_idx == self._last_frame_idx:
            return None

        # First read: log for debugging
        if self._last_frame_idx < 0:
            logger.debug(f"First frame read: latest_idx={latest_idx}, offset={latest_offset}")

        # Read header
        header = bytes(self.shm.buf[latest_offset:latest_offset + self.HEADER_SIZE])
        frame_idx, width, height, channels, timestamp, packet_id = struct.unpack(self.HEADER_FORMAT, header)

        # Validate frame dimensions to detect incomplete writes
        if width <= 0 or height <= 0 or channels <= 0:
            logger.warning(f"Invalid frame dimensions: {width}x{height}x{channels}, skipping")
            # Still update last_frame_idx to avoid repeated failures on same frame
            self._last_frame_idx = latest_idx
            return None

        if width > 4096 or height > 4096:
            logger.warning(f"Frame dimensions too large: {width}x{height}, skipping")
            self._last_frame_idx = latest_idx
            return None

        # Read frame data
        frame_size = height * width * channels
        frame_bytes = bytes(self.shm.buf[latest_offset + self.HEADER_SIZE:latest_offset + self.HEADER_SIZE + frame_size])

        # Create numpy array
        frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape((height, width, channels)).copy()

        # Update last frame index AFTER successful read
        self._last_frame_idx = latest_idx

        # Latency tracking: record shm read
        if packet_id >= 0:
            try:
                from scrcpy_py_ddlx.latency_tracker import get_tracker
                get_tracker().record_shm_read(packet_id)
            except Exception:
                pass

        return frame, frame_idx, packet_id

    def close(self):
        """Close shared memory (don't unlink - owner does that)."""
        try:
            self.shm.close()
        except Exception as e:
            logger.debug(f"SharedMemoryFrameReader close error: {e}")
