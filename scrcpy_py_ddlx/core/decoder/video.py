"""
scrcpy_py_ddlx/core/decoder/video.py

Video decoder for scrcpy using PyAV (FFmpeg).

This module provides video decoding capabilities for H.264, H.265, and AV1
codecs, with output as numpy arrays in BGR format suitable for OpenCV processing.
"""

import logging
from queue import Queue
import threading
from typing import Optional, Tuple

import av
import numpy as np

from .delay_buffer import DelayBuffer
from .exceptions import CodecNotSupportedError, DecodeError, DecoderInitializationError
from ..protocol import CodecId, codec_id_to_string
from ..stream import VideoPacket


logger = logging.getLogger(__name__)


# Default decoder parameters
DEFAULT_THREAD_SAFE: bool = True  # Enable thread-safe decoding


__all__ = ["VideoDecoder", "SimpleDecoder", "decode_packet"]


class VideoDecoder:
    """
    Video decoder for scrcpy streams using PyAV.

    This decoder handles H.264, H.265, and AV1 video codecs and outputs
    frames as numpy arrays in BGR format.

    Example:
        >>> decoder = VideoDecoder(width=1920, height=1080, codec_id=CodecId.H264)
        >>> decoder.start()
        >>> for packet in packets:
        ...     decoder.push(packet)
        ...     frame = decoder.get_frame(timeout=1.0)
        ...     if frame is not None:
        ...         cv2.imshow('Screen', frame)
    """

    def __init__(
        self,
        width: int,
        height: int,
        codec_id: int,
        thread_safe: bool = DEFAULT_THREAD_SAFE,
        packet_queue: Optional[Queue] = None,
        frame_sink: Optional["FrameSink"] = None,
    ) -> None:
        """
        Initialize the video decoder.

        Args:
            width: Video frame width in pixels
            height: Video frame height in pixels
            codec_id: Codec ID (CodecId.H264, CodecId.H265, or CodecId.AV1)
            thread_safe: Enable thread-safe operations (default: True)
            packet_queue: Optional external packet queue (creates internal queue if None)
            frame_sink: Optional frame sink for decoded frames (e.g., Screen, VideoWindow)

        Raises:
            CodecNotSupportedError: If the codec is not supported
        """
        self._width = width
        self._height = height
        self._codec_id = codec_id
        self._thread_safe = thread_safe

        # Frame dimensions
        self._shape: Tuple[int, int, int] = (height, width, 3)

        # Frame sink for decoded frames (optional)
        self._frame_sink = frame_sink

        # Threading primitives
        self._lock = threading.Lock() if thread_safe else None

        # Pause/Resume state (for runtime control without reconnecting)
        self._paused = False
        self._pause_event = threading.Event()
        self._running = False

        # Packet queue (optional external queue, or create internal one)
        self._packet_queue: Queue = packet_queue if packet_queue is not None else Queue(maxsize=30)

        # Use DelayBuffer (single-frame buffer with drop policy) instead of Queue
        # This matches official scrcpy design for minimal latency
        self._frame_buffer: DelayBuffer = DelayBuffer()

        # Decoder state
        self._codec_context: Optional[av.CodecContext] = None
        self._decoder_thread: Optional[threading.Thread] = None

        # Track whether extradata has been set from config packet
        self._extradata_set = False

        # Debug: save first frame to file (disabled for production)
        self._frame_count = 0
        self._save_debug_frame = False  # Disabled - no longer needed

        # Initialize decoder
        self._initialize_decoder()

    def pause(self) -> None:
        """
        Pause decoding (stop CPU consumption).

        This method is called when video is disabled at runtime.
        The decoder stops processing packets but doesn't close,
        allowing resume without reconnecting.
        """
        if self._paused:
            return

        self._paused = True
        self._pause_event.clear()  # Block decode loop
        logger.info("VideoDecoder paused")

    def resume(self) -> None:
        """
        Resume decoding.

        This method is called when video is enabled at runtime.
        """
        if not self._paused:
            return

        self._paused = False
        self._pause_event.set()  # Unblock decode loop
        logger.info("VideoDecoder resumed")

    def _initialize_decoder(self) -> None:
        """
        Initialize the FFmpeg codec context.

        Raises:
            CodecNotSupportedError: If codec is not supported
            DecoderInitializationError: If initialization fails
        """
        try:
            # Map scrcpy codec ID to PyAV codec name
            codec_name = self._get_codec_name()

            # Find the decoder
            codec = av.CodecContext.create(codec_name, "r")

            # Set codec parameters
            codec.width = self._width
            codec.height = self._height
            codec.pix_fmt = "yuv420p"

            # CRITICAL: Set LOW_DELAY flag to match official scrcpy behavior
            # This minimizes latency by reducing frame reordering and preventing
            # the decoder from holding multiple reference frames
            # FFmpeg: AV_CODEC_FLAG_LOW_DELAY = 0x00080000
            codec.flags |= 0x00080000  # AV_CODEC_FLAG_LOW_DELAY

            # CRITICAL: Add flags2 for fast decoding (matching audio decoder)
            # AV_CODEC_FLAG2_FAST allows non-spec compliant speedup tricks
            codec.flags2 |= 0x00000001  # AV_CODEC_FLAG2_FAST

            # Additional low-latency settings
            codec.thread_count = 1  # Single thread for minimal latency
            # Multi-thread decoding can cause frame reordering issues and visual corruption
            # NOTE: strict_std_compliance is not available in PyAV 16.0.1
            # Skipping this setting

            self._codec_context = codec
            logger.info(
                f"Initialized {codec_name} decoder for {self._width}x{self._height} "
                f"with LOW_DELAY flag (flags={codec.flags}, flags2={codec.flags2})"
            )

        except Exception as e:
            # Catch any exception from codec initialization
            # Note: CVError doesn't exist in PyAV 16.0.1
            if "codec" in str(e).lower() or "not found" in str(e).lower():
                raise CodecNotSupportedError(
                    f"Codec {codec_id_to_string(self._codec_id)} not supported: {e}"
                )
            else:
                raise DecoderInitializationError(
                    f"Failed to initialize codec context: {e}"
                )
        except ValueError as e:
            raise CodecNotSupportedError(
                f"Codec {codec_id_to_string(self._codec_id)} not supported: {e}"
            )

    def _get_codec_name(self) -> str:
        """
        Get the PyAV codec name for the current codec ID.

        Returns:
            Codec name string

        Raises:
            CodecNotSupportedError: If codec is not supported
        """
        if self._codec_id == CodecId.H264:
            return "h264"
        elif self._codec_id == CodecId.H265:
            return "hevc"
        elif self._codec_id == CodecId.AV1:
            return "av1"
        else:
            raise CodecNotSupportedError(
                f"Unsupported codec: {codec_id_to_string(self._codec_id)}"
            )

    def start(self) -> None:
        """
        Start the decoder thread.

        The decoder runs in a separate thread to avoid blocking the main thread.
        """
        if self._running:
            logger.warning("Decoder is already running")
            return

        # Initialize frame sink if provided
        if self._frame_sink is not None:
            codec_ctx = {
                "width": self._width,
                "height": self._height,
                "codec_type": "video",
            }
            self._frame_sink.open(codec_ctx)
            if hasattr(self._frame_sink, "start"):
                self._frame_sink.start()

        self._running = True
        self._decoder_thread = threading.Thread(target=self._decode_loop, daemon=True)
        self._decoder_thread.start()
        logger.info("Decoder thread started")

    def stop(self) -> None:
        """Stop the decoder and wait for the thread to finish."""
        if not self._running:
            return

        logger.info("Stopping decoder...")
        self._running = False

        # Push a None to unblock the queue
        self._packet_queue.put(None, timeout=1.0)

        # Close frame sink
        if self._frame_sink is not None:
            if hasattr(self._frame_sink, "stop"):
                self._frame_sink.stop()
            self._frame_sink.close()

        # Wait for thread to finish
        if self._decoder_thread is not None:
            self._decoder_thread.join(timeout=5.0)
            if self._decoder_thread.is_alive():
                logger.warning("Decoder thread did not stop gracefully")

        # Clear queues
        self._clear_queues()

        logger.info("Decoder stopped")

    def _clear_queues(self) -> None:
        """Clear all frame and packet queues."""
        while not self._packet_queue.empty():
            try:
                self._packet_queue.get_nowait()
            except:
                break

        # Clear frame buffer
        self._frame_buffer.clear()

    def push(self, packet: VideoPacket) -> None:
        """
        Push a video packet to the decoder.

        Args:
            packet: The video packet to decode

        Note:
            Config packets are handled internally but do not produce frames.
        """
        if not self._running:
            logger.warning("Cannot push packet: decoder is not running")
            return

        try:
            self._packet_queue.put(packet, block=True, timeout=1.0)
        except Exception as e:
            logger.warning(f"Failed to push packet to queue: {e}")

    def _decode_loop(self) -> None:
        """
        Main decoder loop running in a separate thread.

        This loop continuously pulls packets from the queue and decodes them.
        Decoded frames are placed in the frame queue.
        """
        from queue import Empty

        logger.debug("Decoder loop started")

        while self._running:
            try:
                # Get packet from queue (with timeout to allow checking _running)
                packet = self._packet_queue.get(block=True, timeout=0.1)

                # None is a signal to stop
                if packet is None:
                    break

                # Check pause state before decoding
                if self._paused:
                    self._pause_event.wait()
                    continue

                # Handle config packets - extract extradata
                if packet.header.is_config:
                    if not self._extradata_set and self._codec_context is not None:
                        # Set extradata from config packet (SPS/PPS for H.264)
                        # This is CRITICAL for H.264 decoding to work
                        self._codec_context.extradata = packet.data
                        self._extradata_set = True
                        logger.info(f"Set codec extradata: {len(packet.data)} bytes")
                        # Log first few bytes for debugging
                        if len(packet.data) >= 4:
                            logger.debug(f"Extradata prefix: {packet.data[:4].hex()}")
                    # Config packet itself doesn't produce frames
                    continue

                # Decode the packet
                self._decode_packet(packet)

            except Empty:
                # Queue timeout is normal - no packets available yet
                # This happens during startup or when demuxer is slow
                continue
            except Exception as e:
                if self._running:
                    logger.error(f"Error in decoder loop: {e}", exc_info=True)
                continue

        logger.debug("Decoder loop finished")

    def _decode_packet(self, packet: VideoPacket) -> None:
        """
        Decode a single video packet.

        Args:
            packet: The video packet to decode

        Raises:
            DecodeError: If decoding fails

        OFFICIAL scrcpy BEHAVIOR:
        Process ALL frames produced by the decoder (see scrcpy/app/src/decoder.c:51-71).
        The official code loops until avcodec_receive_frame() returns AVERROR(EAGAIN).

        Note: PyAV < 10.0 uses decode() method, PyAV >= 10.0 uses send()/receive().
        """
        try:
            # Create PyAV packet
            av_packet = av.Packet(packet.data)
            av_packet.pts = packet.header.pts
            av_packet.dts = packet.header.pts

            packet_size = len(packet.data)
            is_key = packet.header.is_key_frame

            # Log packet info
            if is_key:
                logger.info(
                    f"Decoding key frame: {packet_size} bytes, pts={packet.header.pts}"
                )

            # PyAV < 10.0: decode() accepts packet and returns frames
            # This internally calls avcodec_send_packet() + avcodec_receive_frame() loop
            for frame in self._codec_context.decode(av_packet):
                try:
                    self._frame_count += 1

                    # CRITICAL: Removed per-frame debug logging for performance
                    # These logs severely impact performance at 60fps+
                    # logger.debug(
                    #     f"Decoded frame: {frame.width}x{frame.height}, "
                    #     f"format={frame.format}, pts={frame.pts}"
                    # )

                    # Convert frame to numpy array (RGB format)
                    bgr_frame = self._frame_to_bgr(frame)

                    # Put frame in delay buffer (single-frame, drops old frames)
                    success, previous_skipped = self._frame_buffer.push(bgr_frame)

                    # CRITICAL: Reduced frame drop logging for performance
                    # Only log drops periodically (every 100 drops) at DEBUG level
                    if previous_skipped and self._frame_count % 100 == 0:
                        logger.debug(
                            f"Frame drops detected (count={self._frame_count})"
                        )

                    # Always trigger update (removed event reuse mechanism - was causing issues)
                    # Push to frame sink if provided (e.g., Screen for video window)
                    if self._frame_sink is not None:
                        self._frame_sink.push(bgr_frame)

                except Exception as e:
                    logger.warning(f"Error processing decoded frame: {e}")
                    continue

        except av.error.BlockingIOError:
            # This is expected when the decoder is full
            pass
        except av.error.EOFError:
            logger.debug("Decoder EOF")
        except Exception as e:
            raise DecodeError(f"Failed to decode packet: {e}")

    def _frame_to_bgr(self, frame: av.VideoFrame) -> np.ndarray:
        """
        Convert a PyAV VideoFrame to RGB numpy array for display.

        This method converts decoded frames from PyAV's native format to RGB24,
        which matches QImage.Format_RGB888 for display in the video window.

        The conversion pipeline:
        1. PyAV decodes H.264/H.265 to YUV420P
        2. reformat() converts YUV to RGB24 (R-G-B order)
        3. to_ndarray() converts to numpy array
        4. .copy() ensures we own the data (PyAV reuses buffers)

        Args:
            frame: The PyAV VideoFrame to convert (decoded from H.264/H.265)

        Returns:
            RGB format numpy array with shape (height, width, 3)
            Format: RGB24 (R-G-B order), matching QImage.Format_RGB888
            Layout: C-contiguous, strides=(width*3, 3, 1)

        Raises:
            DecodeError: If conversion fails

        Note:
            Despite the legacy method name (_frame_to_bgr), this returns RGB
            format because QImage.Format_RGB888 expects RGB order, not BGR.
        """
        try:
            # Use the actual frame dimensions from the decoded frame
            # This handles device rotation where the frame size changes
            actual_width = frame.width
            actual_height = frame.height

            # Convert frame to RGB24 format using actual dimensions
            # PyAV's reformat handles YUV→RGB conversion with proper color matrix
            frame_rgb = frame.reformat(
                width=actual_width, height=actual_height, format="rgb24"
            )

            # Get the image data as numpy array and COPY it
            # CRITICAL: PyAV reuses frame buffers, so we must copy the data
            # to ensure we own it and it won't be overwritten
            img_array = frame_rgb.to_ndarray().copy()

            # CRITICAL: Removed per-frame debug checks for performance
            # These checks severely impact performance at 60fps+
            # Only enable for debugging when needed
            # if logger.isEnabledFor(logging.DEBUG):
            #     # Check shape
            #     expected_shape = (self._height, self._width, 3)
            #     if img_array.shape != expected_shape:
            #         logger.warning(
            #             f"Unexpected array shape: {img_array.shape}, "
            #             f"expected: {expected_shape}"
            #         )
            #     # Check contiguity
            #     if not img_array.flags['C_CONTIGUOUS']:
            #         logger.warning(
            #             f"Array is not C-contiguous, strides: {img_array.strides}"
            #         )
            #     # Log first pixel for color verification
            #     if img_array.size > 0:
            #         pixel = img_array[0, 0]
            #         logger.debug(
            #             f"Converted frame to RGB: first pixel "
            #             f"R={pixel[0]}, G={pixel[1]}, B={pixel[2]}"
            #         )

            # DEBUG: Save first frame to file to verify decoding is correct
            if self._save_debug_frame and self._frame_count == 1:
                try:
                    # Try OpenCV first
                    import cv2

                    # OpenCV expects BGR, so we need to convert RGB to BGR
                    img_bgr = img_array[:, :, ::-1]  # RGB to BGR (reverse channels)
                    cv2.imwrite("debug_frame_1.png", img_bgr)
                    logger.info("Saved first frame to debug_frame_1.png (using OpenCV)")
                    self._save_debug_frame = False
                except ImportError:
                    try:
                        # Fallback to PIL
                        from PIL import Image

                        pil_img = Image.fromarray(img_array, "RGB")
                        pil_img.save("debug_frame_1.png")
                        logger.info(
                            "Saved first frame to debug_frame_1.png (using PIL)"
                        )
                        self._save_debug_frame = False
                    except ImportError:
                        logger.warning(
                            "Neither OpenCV nor PIL available, saving raw numpy array instead"
                        )
                        # Save raw numpy array
                        np.save("debug_frame_1.npy", img_array)
                        logger.info(
                            "Saved first frame to debug_frame_1.npy (raw numpy array)"
                        )
                        self._save_debug_frame = False
                except Exception as e:
                    logger.error(f"Failed to save debug frame: {e}")

            # Return RGB array (already C-contiguous, safe to use with QImage)
            return img_array

        except Exception as e:
            raise DecodeError(f"Failed to convert frame to RGB: {e}")

    def get_frame(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        """
        Get the next decoded frame.

        Args:
            timeout: Maximum time to wait for a frame (seconds)
                    Note: DelayBuffer doesn't support blocking, so timeout
                    is handled by polling. This is acceptable for real-time
                    screen mirroring where low latency is critical.

        Returns:
            BGR numpy array with shape (height, width, 3), or None if timeout

        Example:
            >>> frame = decoder.get_frame(timeout=0.1)
            >>> if frame is not None:
            ...     cv2.imshow('Screen', frame)
        """
        # DelayBuffer is non-blocking, but we poll for compatibility
        import time

        start_time = time.time()
        while True:
            frame = self._frame_buffer.pop()
            if frame is not None:
                return frame
            if time.time() - start_time >= timeout:
                return None
            time.sleep(0.001)  # Small sleep to avoid busy-waiting

    def get_frame_nowait(self) -> Optional[np.ndarray]:
        """
        Get a frame without blocking.

        Returns:
            BGR numpy array if available, None otherwise
        """
        return self._frame_buffer.get_nowait()

    def get_frame_count(self) -> int:
        """
        Get the number of frames currently in the buffer.

        Returns:
            0 if empty, 1 if has frame
        """
        return self._frame_buffer.qsize()

    def clear_frames(self) -> None:
        """Clear all pending frames from the buffer."""
        self._frame_buffer.clear()

    @property
    def width(self) -> int:
        """Get video width."""
        return self._width

    @property
    def height(self) -> int:
        """Get video height."""
        return self._height

    @property
    def codec_id(self) -> int:
        """Get codec ID."""
        return self._codec_id

    @property
    def is_running(self) -> bool:
        """Check if decoder is running."""
        return self._running

    @property
    def shape(self) -> Tuple[int, int, int]:
        """
        Get the output frame shape.

        Returns:
            Tuple of (height, width, channels)
        """
        return self._shape

    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()


class SimpleDecoder:
    """
    A simplified synchronous decoder for single-threaded use.

    This decoder is simpler than VideoDecoder but runs synchronously,
    which may be more suitable for some use cases.

    Example:
        >>> decoder = SimpleDecoder(width=1920, height=1080, codec_id=CodecId.H264)
        >>> for packet in packets:
        ...     frames = decoder.decode(packet)
        ...     for frame in frames:
        ...         cv2.imshow('Screen', frame)
    """

    def __init__(self, width: int, height: int, codec_id: int) -> None:
        """
        Initialize the simple decoder.

        Args:
            width: Video frame width in pixels
            height: Video frame height in pixels
            codec_id: Codec ID (CodecId.H264, CodecId.H265, or CodecId.AV1)
        """
        self._width = width
        self._height = height
        self._codec_id = codec_id
        self._codec_context: Optional[av.CodecContext] = None
        self._shape: Tuple[int, int, int] = (height, width, 3)

        # Initialize decoder
        self._initialize_decoder()

    def _initialize_decoder(self) -> None:
        """Initialize the FFmpeg codec context."""
        try:
            # Map scrcpy codec ID to PyAV codec name
            if self._codec_id == CodecId.H264:
                codec_name = "h264"
            elif self._codec_id == CodecId.H265:
                codec_name = "hevc"
            elif self._codec_id == CodecId.AV1:
                codec_name = "av1"
            else:
                raise CodecNotSupportedError(
                    f"Unsupported codec: {codec_id_to_string(self._codec_id)}"
                )

            # Create decoder
            self._codec_context = av.CodecContext.create(codec_name, "r")
            self._codec_context.width = self._width
            self._codec_context.height = self._height
            self._codec_context.pix_fmt = "yuv420p"

            logger.info(f"Initialized {codec_name} decoder")

        except Exception as e:
            raise DecoderInitializationError(f"Failed to initialize decoder: {e}")

    def decode(self, packet: VideoPacket) -> list[np.ndarray]:
        """
        Decode a video packet and return frames.

        Args:
            packet: The video packet to decode

        Returns:
            List of BGR numpy arrays (may be empty for config packets)

        Raises:
            DecodeError: If decoding fails
        """
        if self._codec_context is None:
            raise DecodeError("Decoder not initialized")

        # Skip config packets
        if packet.header.is_config:
            return []

        frames = []

        try:
            # Create and send packet
            av_packet = av.Packet(packet.data)
            av_packet.pts = packet.header.pts
            av_packet.dts = packet.header.pts

            # Decode packet (PyAV >= 10.0: decode() accepts packet directly)
            # Receive all available frames
            for frame in self._codec_context.decode(av_packet):
                # Convert to RGB (not BGR!)
                # RGB24 format matches QImage.Format_RGB888
                frame_rgb = frame.reformat(
                    width=self._width, height=self._height, format="rgb24"
                )
                img_array = frame_rgb.to_ndarray().copy()
                # img_array is already in RGB format, no need to swap channels
                frames.append(img_array)

        except av.error.BlockingIOError:
            pass  # Decoder is full, expected behavior
        except Exception as e:
            raise DecodeError(f"Failed to decode packet: {e}")

        return frames

    def flush(self) -> list[np.ndarray]:
        """
        Flush the decoder to get any remaining frames.

        Returns:
            List of remaining BGR numpy arrays
        """
        if self._codec_context is None:
            return []

        frames = []

        try:
            for frame in self._codec_context.decode():
                frame_rgb = frame.reformat(
                    width=self._width, height=self._height, format="rgb24"
                )
                img_array = frame_rgb.to_ndarray().copy()
                frames.append(img_array)
        except Exception:
            pass

        return frames

    @property
    def width(self) -> int:
        """Get video width."""
        return self._width

    @property
    def height(self) -> int:
        """Get video height."""
        return self._height

    @property
    def codec_id(self) -> int:
        """Get codec ID."""
        return self._codec_id

    @property
    def shape(self) -> Tuple[int, int, int]:
        """Get the output frame shape."""
        return self._shape


# Convenience function for quick decoding
def decode_packet(packet: VideoPacket, width: int, height: int) -> list[np.ndarray]:
    """
    Convenience function to decode a single packet.

    This creates a temporary decoder for one-off decoding needs.

    Args:
        packet: The video packet to decode
        width: Video frame width in pixels
        height: Video frame height in pixels

    Returns:
        List of BGR numpy arrays

    Example:
        >>> frames = decode_packet(packet, 1920, 1080)
        >>> for frame in frames:
        ...     cv2.imshow('Screen', frame)
    """
    decoder = SimpleDecoder(width, height, packet.codec_id)
    return decoder.decode(packet)
