"""
scrcpy_py_ddlx/core/decoder/video.py

Video decoder for scrcpy using PyAV (FFmpeg).

This module provides video decoding capabilities for H.264, H.265, and AV1
codecs, with output as numpy arrays in BGR format suitable for OpenCV processing.

Supports hardware-accelerated decoding via NVIDIA CUVID/NVDEC, Intel QSV, etc.
"""

import logging
import os
from queue import Queue
import threading
import time
from typing import Optional, Tuple, List

import av
import numpy as np

try:
    from av.codec.hwaccel import HWAccel
    HWACCEL_AVAILABLE = True
except ImportError:
    HWAccel = None
    HWACCEL_AVAILABLE = False

# 实验性零拷贝GPU模式（需要PyAV 17+）
# 设置环境变量 SCRCPY_ZERO_COPY_GPU=1 启用
ZERO_COPY_GPU_ENABLED = os.environ.get('SCRCPY_ZERO_COPY_GPU', '0') == '1'

from .delay_buffer import DelayBuffer
from .exceptions import CodecNotSupportedError, DecodeError, DecoderInitializationError
from ..protocol import CodecId, codec_id_to_string
from ..stream import VideoPacket


logger = logging.getLogger(__name__)


# Default decoder parameters
DEFAULT_THREAD_SAFE: bool = True  # Enable thread-safe decoding
DEFAULT_HW_ACCEL: bool = True     # Enable hardware acceleration by default


__all__ = ["VideoDecoder", "SimpleDecoder", "decode_packet"]


def _detect_best_hw_device_type() -> Optional[str]:
    """
    Detect the best hardware device type for the current system.

    Returns:
        Device type string (e.g., "cuda", "qsv", "d3d11va", "vaapi", "videotoolbox")
        or None if no hardware acceleration is available.
    """
    import platform

    # Priority order for each platform
    if platform.system() == "Windows":
        priority = ["cuda", "qsv", "d3d11va", "dxva2"]
    elif platform.system() == "Darwin":  # macOS
        priority = ["videotoolbox"]
    else:  # Linux and others
        priority = ["cuda", "vaapi", "qsv"]

    for device_type in priority:
        try:
            # Try to create a HWAccel to check if it's available
            if HWACCEL_AVAILABLE:
                hwaccel = HWAccel(device_type=device_type)
                # Test by checking if FFmpeg knows this device type
                import av.codec.hwaccel as hwaccel_module
                available = hwaccel_module.hwdevices_available()
                if device_type in available:
                    logger.info(f"Detected hardware device: {device_type}")
                    return device_type
        except Exception as e:
            logger.debug(f"Hardware device {device_type} not available: {e}")
            continue

    logger.warning("No hardware device type detected, will use software decoding")
    return None


def _get_available_hw_decoders() -> List[str]:
    """
    Get list of available hardware decoders.

    Returns:
        List of available hardware decoder names
    """
    hw_decoders = [
        # NVIDIA NVDEC (preferred)
        "h264_nvdec", "hevc_nvdec", "av1_nvdec",
        # NVIDIA CUVID (fallback for older FFmpeg)
        "h264_cuvid", "hevc_cuvid", "av1_cuvid",
        # Intel QSV
        "h264_qsv", "hevc_qsv", "av1_qsv",
        # D3D11VA (Windows)
        "h264_d3d11va", "hevc_d3d11va",
        # VideoToolbox (macOS)
        "h264_videotoolbox", "hevc_videotoolbox",
        # VAAPI (Linux)
        "h264_vaapi", "hevc_vaapi", "av1_vaapi",
    ]

    available = []
    for decoder in hw_decoders:
        try:
            av.CodecContext.create(decoder, 'r')
            available.append(decoder)
        except Exception:
            pass

    return available


def _select_best_decoder(codec_id: int, hw_accel: bool = True) -> str:
    """
    Select the best decoder for the given codec.

    Args:
        codec_id: Codec ID (CodecId.H264, CodecId.H265, or CodecId.AV1)
        hw_accel: Whether to use hardware acceleration

    Returns:
        Decoder name string
    """
    import platform

    # Base codec name
    if codec_id == CodecId.H264:
        base = "h264"
    elif codec_id == CodecId.H265:
        base = "hevc"
    elif codec_id == CodecId.AV1:
        base = "av1"
    else:
        raise CodecNotSupportedError(f"Unsupported codec: {codec_id_to_string(codec_id)}")

    if hw_accel:
        # Platform-specific hardware decoder priority
        if platform.system() == "Windows":
            hw_suffixes = ["nvdec", "cuvid", "qsv", "d3d11va"]
        elif platform.system() == "Darwin":  # macOS
            hw_suffixes = ["videotoolbox"]
        else:  # Linux and others
            hw_suffixes = ["nvdec", "cuvid", "vaapi", "qsv"]

        for suffix in hw_suffixes:
            hw_decoder = f"{base}_{suffix}"
            try:
                av.CodecContext.create(hw_decoder, 'r')
                logger.info(f"Selected hardware decoder: {hw_decoder}")
                return hw_decoder
            except Exception:
                continue

        logger.warning(f"No hardware decoder available for {base}, falling back to software")

    return base


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
        hw_accel: bool = DEFAULT_HW_ACCEL,
        shm_writer: Optional["SimpleSHMWriter"] = None,
        output_nv12: bool = False,
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
            hw_accel: Enable hardware acceleration (default: True)
            shm_writer: Optional SimpleSHMWriter for direct frame output (bypasses DelayBuffer)
            output_nv12: Output NV12 format instead of RGB (for GPU YUV rendering, default: False)

        Raises:
            CodecNotSupportedError: If the codec is not supported
        """
        self._width = width
        self._height = height
        self._codec_id = codec_id
        self._thread_safe = thread_safe
        self._hw_accel = hw_accel
        self._using_hw_decoder = False

        # PyAV HWAccel context (for GPU decoding)
        self._hwaccel: Optional["HWAccel"] = None
        self._hw_device_type: Optional[str] = None
        self._using_zero_copy: bool = False  # 实验性零拷贝GPU模式

        # Frame dimensions
        self._shape: Tuple[int, int, int] = (height, width, 3)

        # Frame sink for decoded frames (optional)
        self._frame_sink = frame_sink

        # Direct SHM writer (bypasses DelayBuffer and frame_sender_thread)
        self._shm_writer = shm_writer

        # Output format: NV12 for GPU rendering, RGB for CPU rendering
        self._output_nv12 = output_nv12

        # Pre-allocated buffers for NV12 (avoid per-frame allocation)
        self._y_buffer: Optional[np.ndarray] = None
        self._u_buffer: Optional[np.ndarray] = None
        self._v_buffer: Optional[np.ndarray] = None
        self._buffer_width: int = 0
        self._buffer_height: int = 0

        # Threading primitives
        self._lock = threading.Lock() if thread_safe else None

        # Pause/Resume state (for runtime control without reconnecting)
        self._paused = False
        self._pause_event = threading.Event()
        self._running = False

        # Packet queue (optional external queue, or create internal one)
        # Use small queue (3) to minimize latency - old packets are dropped
        self._packet_queue: Queue = packet_queue if packet_queue is not None else Queue(maxsize=3)

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

        # PTS Clock Drift Diagnostic: Track PTS vs wall clock timing
        self._last_pts = 0  # Last frame's PTS (nanoseconds)
        self._last_pts_wall_time = 0.0  # Wall clock time when last frame was decoded
        self._pts_drift_samples = []  # List of (pts_delta_ns, wall_delta_ns, drift_ns)
        self._first_pts = 0  # First PTS seen (for absolute timing analysis)
        self._first_pts_wall_time = 0.0  # Wall clock time of first frame

        # Frame size change callback (called when decoded frame resolution differs from expected)
        self._frame_size_changed_callback: Optional[callable] = None

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

    def set_frame_size_changed_callback(self, callback: Optional[callable]) -> None:
        """
        Set the callback to notify when decoded frame resolution changes.

        This is used to handle screen rotation when the decoder detects that
        the actual frame dimensions differ from the expected dimensions.
        The callback receives (width, height) of the new frame size.

        Args:
            callback: Function that takes (width, height) arguments, or None to disable
        """
        self._frame_size_changed_callback = callback
        logger.debug("Frame size change callback set")

    def _initialize_decoder(self) -> None:
        """
        Initialize the FFmpeg codec context.

        Supports hardware-accelerated decoding via NVIDIA CUVID/NVDEC,
        Intel QSV, and other hardware decoders.

        Raises:
            CodecNotSupportedError: If codec is not supported
            DecoderInitializationError: If initialization fails
        """
        try:
            # 实验性零拷贝GPU模式
            # 需要设置环境变量 SCRCPY_ZERO_COPY_GPU=1 和 PYTHONPATH 指向编译的PyAV
            if ZERO_COPY_GPU_ENABLED and HWACCEL_AVAILABLE:
                self._zero_copy_mode = True
                codec_name = self._get_base_codec_name()  # 使用基础解码器名（hevc, h264）
                self._hw_device_type = _detect_best_hw_device_type()

                if self._hw_device_type:
                    try:
                        # 尝试使用is_hw_owned参数（PyAV 17+）
                        hwaccel = HWAccel(
                            device_type=self._hw_device_type,
                            is_hw_owned=True,  # 关键：帧保留在GPU
                            allow_software_fallback=False
                        )
                        codec = av.CodecContext.create(codec_name, "r", hwaccel=hwaccel)
                        self._hwaccel = hwaccel
                        self._using_hw_decoder = True
                        self._using_zero_copy = True
                        codec.width = self._width
                        codec.height = self._height
                        codec.thread_count = 0
                        self._codec_context = codec
                        logger.info(f"[EXPERIMENTAL] Zero-copy GPU mode enabled: {codec_name} with {self._hw_device_type}")
                        return
                    except TypeError:
                        # is_hw_owned参数不可用（PyAV < 17）
                        logger.warning("[EXPERIMENTAL] is_hw_owned not available, falling back to normal mode")
                    except Exception as e:
                        logger.warning(f"[EXPERIMENTAL] Zero-copy GPU mode failed: {e}, falling back")

            # 正常模式
            self._zero_copy_mode = False
            self._using_zero_copy = False

            # Select best decoder (hardware or software)
            codec_name = self._get_codec_name()

            # Check if we're using hardware decoder
            is_hw_decoder = any(hw in codec_name for hw in ["nvdec", "cuvid", "qsv", "d3d11va", "vaapi", "videotoolbox"])
            self._using_hw_decoder = is_hw_decoder

            # Try to use PyAV HWAccel for GPU decoding
            hwaccel = None
            if self._hw_accel and HWACCEL_AVAILABLE and not is_hw_decoder:
                # Only use HWAccel with software decoder name (let HWAccel handle hardware selection)
                self._hw_device_type = _detect_best_hw_device_type()
                if self._hw_device_type:
                    try:
                        hwaccel = HWAccel(
                            device_type=self._hw_device_type,
                            allow_software_fallback=True
                        )
                        logger.info(f"Created HWAccel with device type: {self._hw_device_type}")
                    except Exception as e:
                        logger.warning(f"Failed to create HWAccel: {e}")
                        hwaccel = None

            # Create codec context with optional hwaccel
            if hwaccel:
                codec = av.CodecContext.create(codec_name, "r", hwaccel=hwaccel)
                self._hwaccel = hwaccel
                self._using_hw_decoder = True
                logger.info(f"Using PyAV HWAccel: {self._hw_device_type}")
            else:
                codec = av.CodecContext.create(codec_name, "r")

            # Set codec parameters
            codec.width = self._width
            codec.height = self._height
            codec.pix_fmt = "yuv420p"

            if not self._using_hw_decoder:
                # Software decoder settings
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
            else:
                # Hardware decoder settings
                logger.info(f"Using hardware decoder: {codec_name}")
                # Hardware decoders may need different settings
                # Most hardware decoders handle threading internally
                codec.thread_count = 0  # Let hardware decoder decide

            self._codec_context = codec
            decoder_type = "HARDWARE" if self._using_hw_decoder else "SOFTWARE"
            hw_info = f" (HWAccel: {self._hw_device_type})" if self._hw_device_type else ""
            logger.info(
                f"Initialized {codec_name} decoder ({decoder_type}) for {self._width}x{self._height}{hw_info}"
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

    def _reinitialize_decoder(self, new_extradata: bytes) -> None:
        """
        Reinitialize the decoder with new configuration (e.g., after screen rotation).

        This is necessary when the video configuration changes (resolution, SPS/PPS),
        which happens during screen rotation or resolution changes.

        Args:
            new_extradata: New codec extradata (SPS/PPS for H.264/H.265)
        """
        try:
            # CRITICAL: Clear old packets and frames before reinitializing
            # Old packets decoded with new decoder settings will cause corruption
            self._clear_queues()
            logger.debug("Cleared old packets and frames before decoder reinit")

            # Old codec context will be garbage collected
            # PyAV's VideoCodecContext doesn't have a close() method
            # Just set to None and let Python handle cleanup
            if self._codec_context is not None:
                self._codec_context = None

            # CRITICAL: Reset dimensions before reinitializing
            # This allows the decoder to infer new dimensions from SPS/PPS
            # If we keep old dimensions, the decoder may force frames to old size
            old_width, old_height = self._width, self._height
            self._width = 0
            self._height = 0
            logger.info(f"[DECODER] Reset dimensions before reinit (was {old_width}x{old_height})")

            # Reinitialize with same settings (but now width=0, height=0)
            self._initialize_decoder()

            # Set new extradata
            if self._codec_context is not None:
                self._codec_context.extradata = new_extradata
                self._extradata_set = True
                logger.info(f"Decoder reinitialized with new extradata: {len(new_extradata)} bytes")

        except Exception as e:
            logger.error(f"Failed to reinitialize decoder: {e}")
            # Try to continue with old context
            if self._codec_context is not None:
                self._codec_context.extradata = new_extradata

    def _get_codec_name(self) -> str:
        """
        Get the PyAV codec name for the current codec ID.

        Selects the best available decoder, preferring hardware acceleration.

        Returns:
            Codec name string

        Raises:
            CodecNotSupportedError: If codec is not supported
        """
        return _select_best_decoder(self._codec_id, self._hw_accel)

    def _get_base_codec_name(self) -> str:
        """
        Get the base codec name without hardware suffix.

        Used for experimental zero-copy GPU mode with HWAccel.

        Returns:
            Base codec name string (e.g., 'hevc', 'h264', 'av1')
        """
        if self._codec_id == CodecId.H264:
            return "h264"
        elif self._codec_id == CodecId.H265:
            return "hevc"
        elif self._codec_id == CodecId.AV1:
            return "av1"
        else:
            raise CodecNotSupportedError(f"Unsupported codec: {codec_id_to_string(self._codec_id)}")

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
            # Non-blocking put: if queue is full, drop oldest packet
            # This minimizes latency by always keeping the newest packets
            if self._packet_queue.full():
                try:
                    # Try to remove oldest packet to make room
                    self._packet_queue.get_nowait()
                    logger.debug("Dropped oldest packet from full queue")
                except:
                    pass
            self._packet_queue.put_nowait(packet)
        except Exception as e:
            logger.debug(f"Failed to push packet to queue: {e}")

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
                    if self._codec_context is not None:
                        old_extradata = self._codec_context.extradata
                        is_update = old_extradata is not None and len(old_extradata) > 0

                        # Check if config has changed (screen rotation)
                        if is_update and old_extradata != packet.data:
                            logger.info(f"Config changed, reinitializing decoder (screen rotation?)")
                            # Reinitialize decoder with new config
                            self._reinitialize_decoder(packet.data)
                        else:
                            # Just update extradata
                            self._codec_context.extradata = packet.data
                            self._extradata_set = True

                            if is_update:
                                logger.info(f"Updated codec extradata: {len(packet.data)} bytes")
                            else:
                                logger.info(f"Set codec extradata: {len(packet.data)} bytes")

                        # Log first few bytes for debugging
                        if len(packet.data) >= 4:
                            logger.debug(f"Extradata prefix: {packet.data[:4].hex()}")
                    # Config packet itself doesn't produce frames
                    continue

                # Latency tracking: record decode start
                packet_id = packet.packet_id
                if packet_id >= 0:
                    try:
                        from scrcpy_py_ddlx.latency_tracker import get_tracker
                        get_tracker().record_decode_start(packet_id)
                    except Exception:
                        pass

                # Track TRUE_E2E latency at decode start
                decode_start_time = time.time()
                if packet_id >= 0:
                    try:
                        from scrcpy_py_ddlx.latency_tracker import get_tracker
                        udp_recv_time = get_tracker().get_udp_recv_time(packet_id)
                        if udp_recv_time > 0:
                            latency_so_far = (decode_start_time - udp_recv_time) * 1000
                            if latency_so_far > 50:  # Log if > 50ms before decode starts
                                logger.info(f"[DECODER] Packet #{packet_id}: latency before decode = {latency_so_far:.0f}ms, queue_size={self._packet_queue.qsize()}")
                    except Exception:
                        pass

                # Decode the packet
                self._decode_packet(packet, packet_id)

            except Empty:
                # Queue timeout is normal - no packets available yet
                # This happens during startup or when demuxer is slow
                continue
            except Exception as e:
                if self._running:
                    logger.error(f"Error in decoder loop: {e}", exc_info=True)
                continue

        logger.debug("Decoder loop finished")

    def _decode_packet(self, packet: VideoPacket, packet_id: int = -1) -> None:
        """
        Decode a single video packet.

        Args:
            packet: The video packet to decode
            packet_id: Packet ID for latency tracking

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

                    # Convert frame to output format (RGB or NV12)
                    if self._output_nv12:
                        # NV12 format for GPU YUV rendering (avoids CPU YUV→RGB conversion)

                        # 实验性零拷贝GPU模式：检测GPU帧并跳过CPU传输
                        if self._using_zero_copy and frame.format.name == 'cuda':
                            # GPU帧：通过DLPack导出，零拷贝
                            frame_data, frame_w, frame_h = self._frame_to_nv12_dict_gpu(frame)
                        elif self._shm_writer is not None:
                            # For SHM: return bytes format
                            frame_data, frame_w, frame_h = self._frame_to_nv12(frame)
                        else:
                            # For DelayBuffer: return dict with separate Y/U/V planes
                            frame_data, frame_w, frame_h = self._frame_to_nv12_dict(frame)

                        # Debug logging (periodic)
                        if self._frame_count <= 5 or self._frame_count % 60 == 0:
                            if frame_data is not None:
                                if isinstance(frame_data, dict):
                                    if frame_data.get('is_gpu', False):
                                        # GPU零拷贝模式
                                        logger.info(f"[DECODER] NV12 GPU dict: y_gpu_shape={frame_data['y_gpu'].shape}, "
                                                   f"uv_gpu_shape={frame_data['uv_gpu'].shape}")
                                    else:
                                        # CPU模式
                                        logger.info(f"[DECODER] NV12 dict: y_shape={frame_data['y'].shape}, "
                                                   f"u_shape={frame_data['u'].shape}, v_shape={frame_data['v'].shape}")
                                else:
                                    logger.info(f"[DECODER] NV12 bytes: size={len(frame_data)}, {frame_w}x{frame_h}")
                            else:
                                logger.error(f"[DECODER] NV12 conversion failed!")

                        bgr_frame = None  # Not used in NV12 mode
                    else:
                        # RGB format for CPU rendering
                        bgr_frame = self._frame_to_bgr(frame)
                        frame_data = None  # Not used in RGB mode
                        frame_w = frame.width
                        frame_h = frame.height

                    # Check if frame size changed (device rotation)
                    # This handles race conditions where decoder reinitializes with old dimensions
                    # before receiving frames with new dimensions
                    if frame_w != self._width or frame_h != self._height:
                        old_w, old_h = self._width, self._height
                        self._width = frame_w
                        self._height = frame_h
                        self._shape = (frame_h, frame_w, 3)
                        logger.info(
                            f"[DECODER] Frame size changed: {old_w}x{old_h} -> {frame_w}x{frame_h}"
                        )
                        # Notify callback if set
                        if self._frame_size_changed_callback:
                            try:
                                self._frame_size_changed_callback(frame_w, frame_h)
                            except Exception as e:
                                logger.warning(f"Frame size change callback error: {e}")

                    # Latency tracking: record decode complete
                    if packet_id >= 0:
                        try:
                            from scrcpy_py_ddlx.latency_tracker import get_tracker
                            tracker = get_tracker()
                            tracker.record_decode_complete(packet_id)
                            # Also record shm_write as "frame ready" time
                            # This represents when frame is ready for preview
                            tracker.record_shm_write(packet_id)
                        except Exception:
                            pass

                    # Put frame in delay buffer (single-frame, drops old frames)
                    # Include PTS, capture time, and UDP recv time for end-to-end latency tracking
                    import time as time_module
                    capture_time = time_module.time()

                    # Get PTS - prefer frame.pts if available, fall back to packet PTS
                    # packet.header.pts is already in nanoseconds from device
                    try:
                        frame_pts_raw = frame.pts
                        packet_pts_raw = packet.header.pts

                        # Use frame.pts directly (it's already in nanoseconds for scrcpy)
                        # The previous time_base conversion was incorrect for this codec
                        pts = frame_pts_raw if frame_pts_raw is not None else packet_pts_raw

                        # Diagnostic: Log PTS values for debugging
                        if self._frame_count <= 10 or self._frame_count % 60 == 0:
                            logger.info(
                                f"[PTS_DEBUG] Frame #{self._frame_count}: "
                                f"frame.pts={frame_pts_raw}, packet.pts={packet_pts_raw}, "
                                f"using pts={pts}"
                            )
                    except Exception as e:
                        pts = packet.header.pts
                        if self._frame_count <= 10 or self._frame_count % 60 == 0:
                            logger.warning(f"[PTS_DEBUG] Frame #{self._frame_count}: PTS error: {e}, using packet.pts={pts}")

                    # PTS Clock Drift Diagnostic: Compare PTS delta vs wall clock delta
                    # This detects if device clock and PC clock are synchronized
                    # IMPORTANT: PTS from scrcpy device is in MICROSECONDS, not nanoseconds!
                    if self._last_pts != 0:
                        pts_delta_us = pts - self._last_pts  # PTS increment in MICROSECONDS
                        wall_delta_us = int((capture_time - self._last_pts_wall_time) * 1e6)  # Wall clock increment in MICROSECONDS
                        drift_us = pts_delta_us - wall_delta_us  # Positive = device clock faster

                        # Store sample for periodic analysis
                        self._pts_drift_samples.append((pts_delta_us, wall_delta_us, drift_us))

                        # Log every 60 frames with analysis
                        if self._frame_count % 60 == 0 and len(self._pts_drift_samples) >= 10:
                            # Calculate average drift (PTS is in microseconds)
                            avg_pts_delta_ms = sum(s[0] for s in self._pts_drift_samples[-30:]) / len(self._pts_drift_samples[-30:]) / 1e3  # us to ms
                            avg_wall_delta_ms = sum(s[1] for s in self._pts_drift_samples[-30:]) / len(self._pts_drift_samples[-30:]) / 1e3  # us to ms
                            avg_drift_ms = sum(s[2] for s in self._pts_drift_samples[-30:]) / len(self._pts_drift_samples[-30:]) / 1e3  # us to ms

                            # Calculate cumulative drift from first frame (PTS is in MICROSECONDS)
                            total_pts_us = pts - self._first_pts
                            total_wall_us = int((capture_time - self._first_pts_wall_time) * 1e6)
                            total_drift_ms = (total_pts_us - total_wall_us) / 1e3  # us to ms

                            # Calculate expected frame interval (for 60fps = 16.67ms)
                            expected_interval_ms = 1000.0 / 60  # Assume 60fps

                            logger.info(
                                f"[PTS_DRIFT] Frame #{self._frame_count}: "
                                f"pts_delta={avg_pts_delta_ms:.1f}ms, "
                                f"wall_delta={avg_wall_delta_ms:.1f}ms, "
                                f"drift={avg_drift_ms:.2f}ms/frame, "
                                f"total_drift={total_drift_ms:.0f}ms, "
                                f"expected_interval={expected_interval_ms:.1f}ms"
                            )

                            # Warning if drift is significant (> 5ms per frame accumulates quickly)
                            if abs(avg_drift_ms) > 5:
                                logger.warning(
                                    f"[PTS_DRIFT] SIGNIFICANT CLOCK DRIFT DETECTED! "
                                    f"Device clock is {'faster' if avg_drift_ms > 0 else 'slower'} than PC clock. "
                                    f"This may cause TRUE_E2E to be inaccurate."
                                )

                            # Clear old samples to prevent memory growth
                            self._pts_drift_samples = self._pts_drift_samples[-60:]

                    # Record first PTS for absolute timing analysis
                    if self._first_pts == 0:
                        self._first_pts = pts
                        self._first_pts_wall_time = capture_time
                        logger.info(f"[PTS_DRIFT] First frame: pts={pts}, wall_time={capture_time}")

                    self._last_pts = pts
                    self._last_pts_wall_time = capture_time

                    # Get UDP recv time for TRUE end-to-end latency tracking
                    udp_recv_time = 0.0
                    send_time_ns = 0
                    if packet_id >= 0:
                        try:
                            from scrcpy_py_ddlx.latency_tracker import get_tracker
                            udp_recv_time = get_tracker().get_udp_recv_time(packet_id)
                        except Exception:
                            pass
                    # Get device send time for full E2E latency tracking
                    send_time_ns = getattr(packet, 'send_time_ns', 0)

                    # CRITICAL DIAGNOSTIC: Log packet_id and UDP time relationship every 100 frames
                    if self._frame_count % 100 == 0:
                        import datetime as dt
                        udp_time_str = dt.datetime.fromtimestamp(udp_recv_time).strftime('%H:%M:%S.%f')[:-3] if udp_recv_time > 0 else "N/A"
                        capture_time_str = dt.datetime.fromtimestamp(capture_time).strftime('%H:%M:%S.%f')[:-3]
                        logger.info(f"[DECODER] Frame #{self._frame_count}: packet_id={packet_id}, pts={pts}, UDP={udp_time_str}, CAPTURE={capture_time_str}")

                    # If shm_writer is set, write directly to SHM (bypasses DelayBuffer and frame_sender_thread)
                    # This eliminates GIL contention between decoder and frame_sender
                    if self._shm_writer is not None:
                        if self._output_nv12 and frame_data is not None:
                            # Write NV12 format for GPU YUV rendering
                            self._shm_writer.write_nv12_frame(frame_data, frame_w, frame_h, pts, capture_time, udp_recv_time)
                        elif bgr_frame is not None:
                            # Write RGB format for CPU rendering
                            self._shm_writer.write_frame(bgr_frame, pts, capture_time, udp_recv_time)
                    else:
                        # Fallback: use DelayBuffer
                        if self._output_nv12 and frame_data is not None:
                            # NV12 format: frame_data is a dict with y, u, v planes
                            success, previous_skipped = self._frame_buffer.push(frame_data, packet_id, pts, capture_time, udp_recv_time, send_time_ns, self._width, self._height)
                            # Log frame skips (renderer not consuming fast enough)
                            if previous_skipped:
                                self._skip_count = getattr(self, '_skip_count', 0) + 1
                                if self._skip_count % 10 == 1:  # Log every 10 skips
                                    logger.warning(f"[FRAME_SKIP] NV12 frame #{packet_id} skipped (total skips={self._skip_count})")
                        elif bgr_frame is not None:
                            # RGB format (original behavior)
                            success, previous_skipped = self._frame_buffer.push(bgr_frame, packet_id, pts, capture_time, udp_recv_time, send_time_ns, self._width, self._height)

                            # CRITICAL: Reduced frame drop logging for performance
                            # Only log drops periodically (every 100 drops) at DEBUG level
                            if previous_skipped and self._frame_count % 100 == 0:
                                logger.debug(
                                    f"Frame drops detected (count={self._frame_count})"
                                )

                    # Always trigger update (removed event reuse mechanism - was causing issues)
                    # Push to frame sink if provided (e.g., Screen for video window)
                    # Note: frame_sink expects RGB format for CPU mode, or None for NV12 mode
                    if self._frame_sink is not None:
                        if bgr_frame is not None:
                            self._frame_sink.push(bgr_frame)
                        elif self._output_nv12 and frame_data is not None:
                            # NV12 mode: push a placeholder to trigger frame_sink callback
                            # The actual frame is in DelayBuffer, this just notifies the UI
                            self._frame_sink.push(None)

                except Exception as e:
                    logger.warning(f"Error processing decoded frame: {e}")
                    continue

        except av.error.BlockingIOError:
            # This is expected when the decoder is full
            pass
        except av.error.EOFError:
            logger.debug(f"Decoder EOF on packet: pts={packet.header.pts}, size={len(packet.data)}, is_key={packet.header.is_key_frame}")
        except Exception as e:
            raise DecodeError(f"Failed to decode packet: {e}")

    def _frame_to_bgr(self, frame: av.VideoFrame) -> np.ndarray:
        """
        Convert a PyAV VideoFrame to RGB numpy array for display.

        This method converts decoded frames from PyAV's native format to RGB24,
        which matches QImage.Format_RGB888 for display in the video window.

        The conversion pipeline:
        1. PyAV decodes H.264/H.265 to YUV420P or NV12 (hardware decoder)
        2. reformat() converts YUV to RGB24 (R-G-B order)
        3. to_ndarray() converts to numpy array

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

        WARNING:
            Do NOT do frame[:, :, ::-1] conversion on the output!
            The output is already RGB, not BGR. Doing BGR->RGB conversion
            will swap R and B channels, causing color distortion
            (blue becomes purple).

            See: docs/development/DATA_FORMAT_CONVENTIONS.md
        """
        try:
            # Use the actual frame dimensions from the decoded frame
            # This handles device rotation where the frame size changes
            actual_width = frame.width
            actual_height = frame.height

            # Check if frame is already in a format we can use directly
            frame_format = str(frame.format) if hasattr(frame, 'format') else 'unknown'

            # Try to use the most efficient conversion path
            # For hardware decoders, the output might be NV12 which we need to convert
            if frame_format.lower() in ['nv12', 'yuv420p']:
                # Standard YUV formats - use reformat
                frame_rgb = frame.reformat(
                    width=actual_width, height=actual_height, format="rgb24"
                )
            else:
                # Unknown format - let PyAV handle it
                frame_rgb = frame.reformat(
                    width=actual_width, height=actual_height, format="rgb24"
                )

            # Get the image data as numpy array
            # Note: to_ndarray() returns a view when possible, which is fast
            img_array = frame_rgb.to_ndarray()

            # Ensure C-contiguous for SimpleSHM write
            if not img_array.flags['C_CONTIGUOUS']:
                img_array = np.ascontiguousarray(img_array)

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

            # Return RGB array
            return img_array

        except Exception as e:
            raise DecodeError(f"Failed to convert frame to RGB: {e}")

    def _frame_to_nv12(self, frame: av.VideoFrame) -> tuple:
        """
        Convert a PyAV VideoFrame to NV12 format for GPU rendering.

        NV12 is a semi-planar format:
        - Y plane: height * width bytes (luminance)
        - UV plane: height/2 * width bytes (interleaved U and V, each subsampled 2x)

        This avoids CPU-based YUV→RGB conversion, letting OpenGL shaders do it on GPU.

        Args:
            frame: The PyAV VideoFrame to convert (decoded from H.264/H.265)

        Returns:
            Tuple of (nv12_data, width, height) where nv12_data is bytes
        """
        try:
            actual_width = frame.width
            actual_height = frame.height

            if actual_width <= 0 or actual_height <= 0:
                logger.warning(f"Invalid frame dimensions: {actual_width}x{actual_height}")
                return None, 0, 0

            # Try to convert to NV12 format
            try:
                frame_nv12 = frame.reformat(
                    width=actual_width, height=actual_height, format="nv12"
                )
            except Exception as e:
                logger.debug(f"NV12 reformat failed, falling back to YUV420P: {e}")
                # Fallback: convert via yuv420p
                frame_yuv = frame.reformat(format="yuv420p")
                planes = frame_yuv.planes
                if len(planes) != 3:
                    logger.warning(f"YUV420P has unexpected plane count: {len(planes)}")
                    return None, 0, 0

                # Handle stride padding for YUV420P using PyAV recommended approach
                y_plane_raw = planes[0]
                u_plane_raw = planes[1]
                v_plane_raw = planes[2]

                # Extract Y plane
                y_array = np.frombuffer(y_plane_raw, np.uint8).reshape(actual_height, y_plane_raw.line_size)
                y_plane = y_array[:, :actual_width]

                # Extract U plane (half resolution)
                u_array = np.frombuffer(u_plane_raw, np.uint8).reshape(actual_height // 2, u_plane_raw.line_size)
                u_plane = u_array[:, :actual_width // 2]

                # Extract V plane (half resolution)
                v_array = np.frombuffer(v_plane_raw, np.uint8).reshape(actual_height // 2, v_plane_raw.line_size)
                v_plane = v_array[:, :actual_width // 2]

                # Create UV interleaved plane (NV12 format)
                uv_plane = np.empty((actual_height // 2, actual_width), dtype=np.uint8)
                uv_plane[:, 0::2] = u_plane  # U at even columns
                uv_plane[:, 1::2] = v_plane  # V at odd columns

                # Combine Y and UV planes
                nv12_data = np.concatenate([y_plane.ravel(), uv_plane.ravel()])
                return nv12_data.tobytes(), actual_width, actual_height

            # Get the planes from NV12 frame
            planes = frame_nv12.planes
            if planes is None or len(planes) < 2:
                logger.warning(f"NV12 frame has unexpected planes: {planes}")
                return None, 0, 0

            # Handle stride padding using PyAV recommended approach
            # reshape to (height, line_size) then slice to (height, width)
            y_plane = planes[0]
            uv_plane = planes[1]
            y_linesize = y_plane.line_size
            uv_linesize = uv_plane.line_size

            # Fast path: no stride padding
            if y_linesize == actual_width and uv_linesize == actual_width:
                # Direct copy without numpy processing
                return bytes(y_plane) + bytes(uv_plane), actual_width, actual_height

            # Slow path: handle stride padding
            # Extract Y plane (handle stride if needed)
            y_array = np.frombuffer(y_plane, np.uint8).reshape(actual_height, y_linesize)
            y_data = y_array[:, :actual_width].ravel()

            # Extract UV plane (handle stride if needed)
            uv_array = np.frombuffer(uv_plane, np.uint8).reshape(actual_height // 2, uv_linesize)
            uv_data = uv_array[:, :actual_width].ravel()

            # Combine without intermediate tobytes() calls
            nv12_data = np.concatenate([y_data, uv_data])
            return nv12_data.tobytes(), actual_width, actual_height

        except Exception as e:
            logger.warning(f"Failed to convert frame to NV12: {e}")
            return None, 0, 0

    def _frame_to_nv12_dict(self, frame: av.VideoFrame) -> tuple:
        """
        Convert a PyAV VideoFrame to NV12 format as a dict with separate Y/U/V planes.

        OPTIMIZED: Uses pre-allocated buffers to avoid per-frame memory allocation.
        This reduces CPU usage by ~15-20% by eliminating numpy array creation overhead.

        Args:
            frame: The PyAV VideoFrame to convert (decoded from H.264/H.265)

        Returns:
            Tuple of (nv12_dict, width, height) where nv12_dict contains:
            - 'y': Y plane numpy array (height, width)
            - 'u': U plane numpy array (height/2, width/2)
            - 'v': V plane numpy array (height/2, width/2)
            - 'y_stride': actual Y plane width (may include padding)
            - 'uv_stride': actual UV plane width (may include padding)
        """
        try:
            actual_width = frame.width
            actual_height = frame.height

            if actual_width <= 0 or actual_height <= 0:
                logger.warning(f"Invalid frame dimensions: {actual_width}x{actual_height}")
                return None, 0, 0

            # Check if we need to reallocate buffers (size changed)
            if (self._buffer_width != actual_width or self._buffer_height != actual_height):
                # Free old buffers
                self._y_buffer = None
                self._u_buffer = None
                self._v_buffer = None

                # Pre-allocate new buffers
                self._y_buffer = np.empty((actual_height, actual_width), dtype=np.uint8)
                self._u_buffer = np.empty((actual_height // 2, actual_width // 2), dtype=np.uint8)
                self._v_buffer = np.empty((actual_height // 2, actual_width // 2), dtype=np.uint8)
                self._buffer_width = actual_width
                self._buffer_height = actual_height
                logger.debug(f"[NV12] Reallocated buffers: {actual_width}x{actual_height}")

            # Convert to NV12 format
            try:
                frame_nv12 = frame.reformat(
                    width=actual_width, height=actual_height, format="nv12"
                )
            except Exception as e:
                logger.debug(f"NV12 reformat failed, falling back to YUV420P: {e}")
                # Fallback: convert via YUV420P
                frame_yuv = frame.reformat(format="yuv420p")
                planes = frame_yuv.planes
                if len(planes) != 3:
                    logger.warning(f"YUV420P has unexpected plane count: {len(planes)}")
                    return None, 0, 0

                # Handle stride padding for YUV420P - use pre-allocated buffers
                y_plane_raw = planes[0]
                u_plane_raw = planes[1]
                v_plane_raw = planes[2]

                # Extract Y plane - copy to pre-allocated buffer
                y_array = np.frombuffer(y_plane_raw, np.uint8).reshape(actual_height, y_plane_raw.line_size)
                np.copyto(self._y_buffer, y_array[:, :actual_width])

                # Extract U plane (half resolution)
                u_array = np.frombuffer(u_plane_raw, np.uint8).reshape(actual_height // 2, u_plane_raw.line_size)
                np.copyto(self._u_buffer, u_array[:, :actual_width // 2])

                # Extract V plane (half resolution)
                v_array = np.frombuffer(v_plane_raw, np.uint8).reshape(actual_height // 2, v_plane_raw.line_size)
                np.copyto(self._v_buffer, v_array[:, :actual_width // 2])

                return {
                    'y': self._y_buffer,
                    'u': self._u_buffer,
                    'v': self._v_buffer,
                    'y_stride': actual_width,
                    'uv_stride': actual_width // 2
                }, actual_width, actual_height

            # Get the planes from NV12 frame
            planes = frame_nv12.planes
            if planes is None or len(planes) < 2:
                logger.warning(f"NV12 frame has unexpected planes: {planes}")
                return None, 0, 0

            # Handle stride padding - copy to pre-allocated buffers
            y_plane = planes[0]
            uv_plane = planes[1]
            y_linesize = y_plane.line_size
            uv_linesize = uv_plane.line_size

            # Extract Y plane - copy to pre-allocated buffer
            y_array = np.frombuffer(y_plane, np.uint8).reshape(actual_height, y_linesize)
            np.copyto(self._y_buffer, y_array[:, :actual_width])

            # Extract UV plane and split into U and V
            uv_array = np.frombuffer(uv_plane, np.uint8).reshape(actual_height // 2, uv_linesize)
            uv_data = uv_array[:, :actual_width]

            # Split interleaved UV into separate U and V planes - copy to pre-allocated buffers
            np.copyto(self._u_buffer, uv_data[:, 0::2])  # U at even columns
            np.copyto(self._v_buffer, uv_data[:, 1::2])  # V at odd columns

            return {
                'y': self._y_buffer,
                'u': self._u_buffer,
                'v': self._v_buffer,
                'y_stride': y_linesize,
                'uv_stride': uv_linesize
            }, actual_width, actual_height

        except Exception as e:
            logger.warning(f"Failed to convert frame to NV12 dict: {e}")
            return None, 0, 0

    def _frame_to_nv12_dict_gpu(self, frame: "av.VideoFrame") -> tuple:
        """
        [EXPERIMENTAL] Handle GPU frame (cuda format) without CPU transfer.

        This is for zero-copy GPU rendering mode. The frame data stays in GPU memory
        and is exported via DLPack for CuPy/PyTorch processing.

        Args:
            frame: PyAV VideoFrame with format='cuda' (GPU memory)

        Returns:
            Special dict with GPU arrays (CuPy) instead of numpy:
            - 'y_gpu': CuPy array for Y plane (GPU memory)
            - 'uv_gpu': CuPy array for UV plane (GPU memory)
            - 'is_gpu': True to indicate GPU data
            - 'y_stride', 'uv_stride': stride info
        """
        actual_width = frame.width
        actual_height = frame.height

        # Log GPU frame detection
        if self._frame_count <= 5:
            logger.info(f"[ZERO_COPY] GPU frame detected: format={frame.format.name}, "
                       f"planes={len(frame.planes) if frame.planes else 0}")

        # Try to use CuPy for zero-copy GPU access
        try:
            import cupy as cp
            CUPY_AVAILABLE = True
        except ImportError:
            CUPY_AVAILABLE = False
            if self._frame_count <= 5:
                logger.warning("[ZERO_COPY] CuPy not available, falling back to CPU")

        if not CUPY_AVAILABLE:
            return self._frame_to_nv12_dict(frame)

        # Export GPU frame via DLPack
        try:
            if frame.planes and len(frame.planes) >= 2:
                # NV12 format: Y plane + UV plane
                y_plane = frame.planes[0]
                uv_plane = frame.planes[1]

                if hasattr(y_plane, '__dlpack__') and hasattr(uv_plane, '__dlpack__'):
                    # DLPack导出（零拷贝，数据仍在GPU）
                    y_gpu = cp.fromDlpack(y_plane.__dlpack__())
                    uv_gpu = cp.fromDlpack(uv_plane.__dlpack__())

                    y_stride = y_plane.line_size
                    uv_stride = uv_plane.line_size

                    if self._frame_count <= 5:
                        # 检查CuPy数组的属性
                        logger.info(f"[ZERO_COPY] GPU arrays: y={y_gpu.shape}, uv={uv_gpu.shape}, "
                                   f"y_stride={y_stride}, uv_stride={uv_stride}")
                        logger.info(f"[ZERO_COPY] CuPy details: y_ptr={y_gpu.data.ptr}, "
                                   f"y_contig={y_gpu.flags['C_CONTIGUOUS']}, "
                                   f"y_device={cp.cuda.Device().id}")
                        # 验证指针是否可访问
                        try:
                            _ = y_gpu[0, 0]  # 尝试访问一个元素
                            logger.info("[ZERO_COPY] CuPy array access OK")
                        except Exception as e:
                            logger.warning(f"[ZERO_COPY] CuPy array access failed: {e}")

                    # 返回GPU数组字典（特殊格式，OpenGL需要识别is_gpu=True）
                    return {
                        'y_gpu': y_gpu,
                        'uv_gpu': uv_gpu,
                        'is_gpu': True,
                        'y_stride': y_stride,
                        'uv_stride': uv_stride,
                        'width': actual_width,
                        'height': actual_height,
                    }, actual_width, actual_height

        except Exception as e:
            if self._frame_count <= 5:
                logger.warning(f"[ZERO_COPY] DLPack export failed: {e}, falling back to CPU")

        # Fallback: Use standard reformat path
        return self._frame_to_nv12_dict(frame)

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
            result = self._frame_buffer.pop()
            if result is not None:
                # DelayBuffer returns FrameWithMetadata, extract frame
                return result.frame if hasattr(result, 'frame') else result
            if time.time() - start_time >= timeout:
                return None
            time.sleep(0.001)  # Small sleep to avoid busy-waiting

    def get_frame_nowait(self) -> Optional[np.ndarray]:
        """
        Get a frame without blocking.

        Returns:
            BGR numpy array if available, None otherwise
        """
        result = self._frame_buffer.get_nowait()
        if result is not None:
            # DelayBuffer returns FrameWithMetadata, extract frame
            return result.frame if hasattr(result, 'frame') else result
        return None

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
