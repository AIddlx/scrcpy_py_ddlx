"""
Simple Shared Memory for low-latency single-frame transfer.

No double buffering, no frame index comparison - just write/read the latest frame.
"""

import numpy as np
import struct
import time
import logging
from typing import Optional, Tuple
from multiprocessing import shared_memory, Value
import ctypes

logger = logging.getLogger(__name__)


class SimpleSHMWriter:
    """
    Simple single-frame shared memory writer.

    Frame format:
    - 4 bytes: write_counter (uint32) - increments on each write
    - 4 bytes: width (int32)
    - 4 bytes: height (int32)
    - 4 bytes: format (uint32) - 0=RGB24 (channels=3), 1=NV12 (YUV420 semi-planar)
    - 8 bytes: pts (int64) - presentation timestamp from device (nanoseconds)
    - 8 bytes: capture_time_ns (int64) - time when frame was decoded on PC (nanoseconds)
    - 8 bytes: udp_recv_time_ns (int64) - time when UDP packet was received (nanoseconds)
    - N bytes: frame data

    NV12 format:
    - Y plane: width * height bytes (luminance, full resolution)
    - UV plane: width * height / 2 bytes (interleaved U/V, half resolution)
    - Total: width * height * 1.5 bytes
    """

    HEADER_SIZE = 44  # 4 + 4 + 4 + 4 + 8 + 8 + 8 + 4 (padding for alignment)
    HEADER_FORMAT = '<IiIIqqqI'  # uint32, int32, uint32, uint32, int64, int64, int64, uint32 (padding)

    # Frame format constants
    FORMAT_RGB24 = 0  # RGB24 format (H, W, 3) - channels=3
    FORMAT_NV12 = 1   # NV12 format (YUV420 semi-planar) - Y + UV planes

    def __init__(self, max_width: int = 1920, max_height: int = 4096, channels: int = 3, notify_callback=None):
        """
        Create simple shared memory buffer.

        Args:
            max_width: Maximum frame width
            max_height: Maximum frame height
            channels: Number of color channels (for RGB mode)
            notify_callback: Optional callback to call after writing frame (for event-driven mode)
        """
        self.max_width = max_width
        self.max_height = max_height
        self.channels = channels
        self._notify_callback = notify_callback

        # Calculate max frame size for both RGB and NV12 formats
        # RGB24: w * h * 3
        # NV12: w * h * 1.5
        # Use the larger of the two for buffer size
        max_rgb_size = max_width * max_height * channels
        max_nv12_size = int(max_width * max_height * 1.5)
        self.max_frame_size = max(max_rgb_size, max_nv12_size)

        # Total size: header + max frame
        self.total_size = self.HEADER_SIZE + self.max_frame_size

        # Write counter (atomic)
        self._write_counter = Value(ctypes.c_uint32, 0)

        # Create shared memory
        self.shm = shared_memory.SharedMemory(create=True, size=self.total_size)
        self.name = self.shm.name

        logger.info(f"SimpleSHMWriter created: max={max_width}x{max_height}, "
                   f"max_rgb={max_rgb_size/1024/1024:.1f}MB, max_nv12={max_nv12_size/1024/1024:.1f}MB, "
                   f"buffer={self.total_size/1024/1024:.1f}MB, name={self.name}")

    def get_info(self) -> dict:
        """Get buffer info for connecting from another process."""
        return {
            'name': self.name,
            'size': self.total_size,
            'max_width': self.max_width,
            'max_height': self.max_height,
            'channels': self.channels
        }

    def write_frame(self, frame: np.ndarray, pts: int = 0, capture_time: float = 0.0, udp_recv_time: float = 0.0) -> bool:
        """
        Write RGB frame to shared memory.

        Args:
            frame: RGB numpy array (H, W, 3)
            pts: Presentation timestamp from device (nanoseconds)
            capture_time: Time when frame was decoded on PC (seconds, from time.time())
            udp_recv_time: Time when UDP packet was received (seconds, from time.time())

        Returns:
            True if written successfully
        """
        if frame is None:
            return False

        h, w = frame.shape[:2]
        frame_size = h * w * self.channels

        if frame_size > self.max_frame_size:
            logger.warning(f"Frame too large: {w}x{h} > max {self.max_width}x{self.max_height}")
            return False

        # Ensure contiguous
        if not frame.flags['C_CONTIGUOUS']:
            frame = np.ascontiguousarray(frame)

        # CRITICAL: Write frame data FIRST
        self.shm.buf[self.HEADER_SIZE:self.HEADER_SIZE + frame_size] = frame.tobytes()

        # Write header LAST (with counter)
        # Reader uses counter to detect new frames
        # Convert times to nanoseconds (int64) for precision
        capture_time_ns = int(capture_time * 1e9) if capture_time > 0 else 0
        udp_recv_time_ns = int(udp_recv_time * 1e9) if udp_recv_time > 0 else 0

        new_counter = self._write_counter.value + 1
        # Pack with padding (last I is padding for alignment)
        header = struct.pack(self.HEADER_FORMAT, new_counter, w, h, self.FORMAT_RGB24, pts, capture_time_ns, udp_recv_time_ns, 0)
        self.shm.buf[0:self.HEADER_SIZE] = header

        # Increment counter (atomic) - this is the signal that frame is ready
        with self._write_counter.get_lock():
            self._write_counter.value = new_counter

        # CRITICAL DIAGNOSTIC: Log every 100 writes with human-readable times
        if new_counter % 100 == 0:
            import datetime as dt
            import time as time_module
            now = time_module.time()
            udp_time_str = dt.datetime.fromtimestamp(udp_recv_time).strftime('%H:%M:%S.%f')[:-3] if udp_recv_time > 0 else "N/A"
            now_str = dt.datetime.fromtimestamp(now).strftime('%H:%M:%S.%f')[:-3]
            # Calculate delay from UDP recv to SHM write
            shm_write_delay_ms = (now - udp_recv_time) * 1000 if udp_recv_time > 0 else 0
            logger.info(f"[SHM_WRITE] counter={new_counter}, format=RGB24, pts={pts}, UDP={udp_time_str}, NOW={now_str}, delay={shm_write_delay_ms:.0f}ms")

        # Notify listener (event-driven mode)
        if self._notify_callback:
            self._notify_callback()

        return True

    def write_nv12_frame(self, nv12_data: bytes, width: int, height: int, pts: int = 0, capture_time: float = 0.0, udp_recv_time: float = 0.0) -> bool:
        """
        Write NV12 frame to shared memory.

        NV12 format bypasses CPU-based YUV→RGB conversion, allowing GPU shaders
        to do the conversion which is much faster for high-resolution video.

        Args:
            nv12_data: NV12 format bytes or numpy array (Y plane + UV plane interleaved)
            width: Frame width in pixels
            height: Frame height in pixels
            pts: Presentation timestamp from device (nanoseconds)
            capture_time: Time when frame was decoded on PC (seconds, from time.time())
            udp_recv_time: Time when UDP packet was received (seconds, from time.time())

        Returns:
            True if written successfully
        """
        if nv12_data is None or width <= 0 or height <= 0:
            return False

        # NV12 size: Y (w*h) + UV (w*h/2) = w*h*1.5
        frame_size = int(width * height * 1.5)

        if frame_size > self.max_frame_size:
            logger.warning(f"NV12 frame too large: {width}x{height} = {frame_size} bytes > max {self.max_frame_size}")
            return False

        # Convert to bytes if numpy array
        if hasattr(nv12_data, 'tobytes'):
            nv12_bytes = nv12_data.tobytes()
        else:
            nv12_bytes = nv12_data

        # Ensure we have the right amount of data
        if len(nv12_bytes) < frame_size:
            logger.warning(f"NV12 data too small: {len(nv12_bytes)} < {frame_size}")
            return False

        # CRITICAL: Write frame data FIRST
        self.shm.buf[self.HEADER_SIZE:self.HEADER_SIZE + frame_size] = nv12_bytes[:frame_size]

        # Write header LAST
        capture_time_ns = int(capture_time * 1e9) if capture_time > 0 else 0
        udp_recv_time_ns = int(udp_recv_time * 1e9) if udp_recv_time > 0 else 0

        new_counter = self._write_counter.value + 1
        # Pack with NV12 format marker
        header = struct.pack(self.HEADER_FORMAT, new_counter, width, height, self.FORMAT_NV12, pts, capture_time_ns, udp_recv_time_ns, 0)
        self.shm.buf[0:self.HEADER_SIZE] = header

        # Increment counter (atomic)
        with self._write_counter.get_lock():
            self._write_counter.value = new_counter

        # CRITICAL DIAGNOSTIC: Log every 100 writes
        if new_counter % 100 == 0:
            import datetime as dt
            import time as time_module
            now = time_module.time()
            udp_time_str = dt.datetime.fromtimestamp(udp_recv_time).strftime('%H:%M:%S.%f')[:-3] if udp_recv_time > 0 else "N/A"
            now_str = dt.datetime.fromtimestamp(now).strftime('%H:%M:%S.%f')[:-3]
            shm_write_delay_ms = (now - udp_recv_time) * 1000 if udp_recv_time > 0 else 0
            logger.info(f"[SHM_WRITE] counter={new_counter}, format=NV12, size={width}x{height}, UDP={udp_time_str}, NOW={now_str}, delay={shm_write_delay_ms:.0f}ms")

        # Notify listener (event-driven mode)
        if self._notify_callback:
            self._notify_callback()

        return True

    def close(self):
        """Close and unlink shared memory."""
        try:
            self.shm.close()
            self.shm.unlink()
            logger.debug(f"SimpleSHMWriter closed: {self.name}")
        except Exception as e:
            logger.debug(f"SimpleSHMWriter close error: {e}")


class SimpleSHMReader:
    """
    Simple single-frame shared memory reader.

    Just reads whatever is in shared memory - always the latest frame.
    """

    def __init__(self, name: str, size: int, max_width: int = 1920,
                 max_height: int = 4096, channels: int = 3):
        """
        Connect to existing shared memory.

        Args:
            name: Shared memory name
            size: Total size
            max_width: Maximum frame width
            max_height: Maximum frame height
            channels: Number of color channels
        """
        self.name = name
        self.size = size
        self.max_width = max_width
        self.max_height = max_height
        self.channels = channels

        # Connect to shared memory
        self.shm = shared_memory.SharedMemory(name=name)

        # Track last counter to detect new frames
        self._last_counter = 0

        logger.info(f"SimpleSHMReader connected: {name}, size={size/1024/1024:.1f}MB")

    def read_frame(self) -> Optional[Tuple[np.ndarray, int, float, float]]:
        """
        Read latest RGB frame from shared memory.

        CRITICAL: Uses retry mechanism to prevent race condition where:
        1. Reader reads header (counter=N, udp_recv_time=OLD)
        2. Writer writes new frame data and header (counter=N+1)
        3. Reader reads frame data (which is now NEW frame)
        4. Reader returns (NEW frame, OLD udp_recv_time) - MISMATCH!

        The retry mechanism ensures header and frame data are consistent.

        IMPORTANT: Always returns the LATEST frame, even if counter didn't change
        since last read. This prevents accumulated delay when reader is slower
        than writer.

        NOTE: This method only reads RGB frames. Use read_frame_ex() for NV12 support.

        Returns:
            Tuple of (frame, pts, capture_time, udp_recv_time), or None if no frame yet
            - frame: numpy array (H, W, 3)
            - pts: presentation timestamp from device (nanoseconds)
            - capture_time: time when frame was decoded on PC (seconds)
            - udp_recv_time: time when UDP packet was received (seconds)
        """
        result = self.read_frame_ex()
        if result is None:
            return None

        frame, pts, capture_time, udp_recv_time, format_flag = result

        # For NV12 format, caller should use read_frame_ex() directly
        if format_flag == SimpleSHMWriter.FORMAT_NV12:
            logger.warning("read_frame() called on NV12 frame, use read_frame_ex() instead")
            # Convert NV12 to RGB for backward compatibility (slow!)
            frame = self._nv12_to_rgb(frame, frame.shape[1], frame.shape[0] * 2 // 3)

        return frame, pts, capture_time, udp_recv_time

    def read_frame_ex(self) -> Optional[Tuple[np.ndarray, int, float, float, int, int, int]]:
        """
        Read latest frame from shared memory with format and dimension information.

        Returns:
            Tuple of (frame, pts, capture_time, udp_recv_time, format, width, height), or None if no frame yet
            - frame: numpy array (H, W, 3) for RGB or (H*1.5, W) for NV12
            - pts: presentation timestamp from device (nanoseconds)
            - capture_time: time when frame was decoded on PC (seconds)
            - udp_recv_time: time when UDP packet was received (seconds)
            - format: SimpleSHMWriter.FORMAT_RGB24 or SimpleSHMWriter.FORMAT_NV12
            - width: frame width in pixels
            - height: frame height in pixels
        """
        read_start_time = time.time()
        max_retries = 10

        for retry in range(max_retries):
            # Step 1: Read header FIRST
            header = bytes(self.shm.buf[0:SimpleSHMWriter.HEADER_SIZE])
            # Unpack with padding (last I is padding)
            counter1, width, height, format_flag, pts, capture_time_ns, udp_recv_time_ns, _ = struct.unpack(SimpleSHMWriter.HEADER_FORMAT, header)

            # Check if ANY frame exists (counter > 0)
            if counter1 == 0:
                return None  # No frame written yet

            # Validate dimensions
            if width <= 0 or height <= 0 or width > self.max_width or height > self.max_height:
                logger.warning(f"Invalid dimensions: {width}x{height}")
                return None

            # Step 2: Calculate frame size based on format
            if format_flag == SimpleSHMWriter.FORMAT_NV12:
                frame_size = int(width * height * 1.5)  # NV12: Y + UV
            else:
                frame_size = height * width * 3  # RGB24

            # Read frame data
            frame_data_start = time.time()
            frame_bytes = bytes(self.shm.buf[SimpleSHMWriter.HEADER_SIZE:
                                              SimpleSHMWriter.HEADER_SIZE + frame_size])
            frame_data_end = time.time()

            # Step 3: Read header AGAIN to check if it changed during frame read
            header2 = bytes(self.shm.buf[0:SimpleSHMWriter.HEADER_SIZE])
            counter2, _, _, _, _, _, _, _ = struct.unpack(SimpleSHMWriter.HEADER_FORMAT, header2)

            # If counter changed, writer updated the frame during our read - retry
            if counter1 != counter2:
                # Race condition detected - retry with new frame
                if retry < max_retries - 1:
                    logger.debug(f"[SHM_RACE] Counter changed during read: {counter1} -> {counter2}, retrying")
                    continue
                else:
                    logger.warning(f"SimpleSHM: max retries reached, counter changed {counter1} -> {counter2}")
                    return None

            # Counter stable - we have consistent frame data and metadata
            read_end_time = time.time()
            total_read_ms = (read_end_time - read_start_time) * 1000
            frame_data_ms = (frame_data_end - frame_data_start) * 1000

            # Log if we skipped frames (for debugging)
            if self._last_counter > 0 and counter1 - self._last_counter > 1:
                skipped = counter1 - self._last_counter - 1
                logger.info(f"[SHM_READ] Counter jump: {self._last_counter} -> {counter1} (skipped {skipped} frames)")

            # Diagnostic: Log every 100 successful reads with PTS and timing
            if counter1 % 100 == 0:
                import datetime as dt
                now = time.time()
                udp_recv_time_sec = udp_recv_time_ns / 1e9 if udp_recv_time_ns > 0 else 0
                true_e2e_ms = (now - udp_recv_time_sec) * 1000 if udp_recv_time_sec > 0 else 0
                udp_time_str = dt.datetime.fromtimestamp(udp_recv_time_sec).strftime('%H:%M:%S.%f')[:-3] if udp_recv_time_sec > 0 else "N/A"
                now_str = dt.datetime.fromtimestamp(now).strftime('%H:%M:%S.%f')[:-3]
                format_str = "NV12" if format_flag == SimpleSHMWriter.FORMAT_NV12 else "RGB24"
                logger.info(f"[SHM_READ] counter={counter1}, format={format_str}, pts={pts}, UDP={udp_time_str}, NOW={now_str}, E2E={true_e2e_ms:.0f}ms, read_time={total_read_ms:.2f}ms")

            # Update last counter
            self._last_counter = counter1

            # Create numpy array based on format
            numpy_start = time.time()
            if format_flag == SimpleSHMWriter.FORMAT_NV12:
                # NV12: Y plane (height * width) + UV plane (height/2 * width)
                frame = np.frombuffer(frame_bytes, dtype=np.uint8).copy()
            else:
                # RGB24: (height, width, 3)
                frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape((height, width, 3)).copy()
            numpy_ms = (time.time() - numpy_start) * 1000

            # Log timing for first 10 frames and every 60
            if counter1 <= 10 or counter1 % 60 == 0:
                logger.debug(f"[SHM_TIMING] counter={counter1}: total_read={total_read_ms:.2f}ms, frame_data={frame_data_ms:.2f}ms, numpy_copy={numpy_ms:.2f}ms")

            # Convert times from nanoseconds to seconds
            capture_time = capture_time_ns / 1e9 if capture_time_ns > 0 else 0.0
            udp_recv_time = udp_recv_time_ns / 1e9 if udp_recv_time_ns > 0 else 0.0

            return frame, pts, capture_time, udp_recv_time, format_flag, width, height

        return None

    def _nv12_to_rgb(self, nv12_data: np.ndarray, width: int, height: int) -> np.ndarray:
        """
        Convert NV12 to RGB (slow CPU conversion, for backward compatibility only).

        For performance, use GPU shaders instead.
        """
        # Extract Y and UV planes
        y_size = width * height
        y_plane = nv12_data[:y_size].reshape((height, width))
        uv_plane = nv12_data[y_size:].reshape((height // 2, width))

        # Extract U and V (interleaved)
        u = uv_plane[:, 0::2].astype(np.float32)
        v = uv_plane[:, 1::2].astype(np.float32)

        # Upsample U and V to full resolution
        u_up = np.repeat(np.repeat(u, 2, axis=0), 2, axis=1)
        v_up = np.repeat(np.repeat(v, 2, axis=0), 2, axis=1)

        # Convert to RGB (BT.601 coefficients)
        y = y_plane.astype(np.float32)
        r = y + 1.402 * (v_up - 128)
        g = y - 0.344 * (u_up - 128) - 0.714 * (v_up - 128)
        b = y + 1.772 * (u_up - 128)

        # Clamp and stack
        rgb = np.stack([
            np.clip(r, 0, 255).astype(np.uint8),
            np.clip(g, 0, 255).astype(np.uint8),
            np.clip(b, 0, 255).astype(np.uint8)
        ], axis=2)

        return rgb

    def close(self):
        """Close shared memory (don't unlink - owner does that)."""
        try:
            self.shm.close()
        except Exception as e:
            logger.debug(f"SimpleSHMReader close error: {e}")
