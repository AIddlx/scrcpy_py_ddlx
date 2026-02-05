"""
Simple Video Player - Minimal implementation for scrcpy video display.

This is a complete rewrite focusing on simplicity:
- Direct socket to display pipeline
- Minimal abstraction layers
- OpenCV for display (reliable and simple)
"""

import logging
import threading
import time
from typing import Optional
import numpy as np

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

import av

logger = logging.getLogger(__name__)


class SimpleVideoPlayer:
    """
    Minimal video player for scrcpy.

    Design principles:
    1. Single-threaded decoding (no complex queues)
    2. Direct OpenCV display (no Qt complexity)
    3. Simple frame buffer (latest frame only)
    4. Minimal data processing
    """

    def __init__(self, width: int, height: int, codec_id: int = 0x68323634):
        """
        Initialize simple video player.

        Args:
            width: Video width
            height: Video height
            codec_id: Codec ID (default H264)
        """
        self.width = width
        self.height = height
        self.codec_id = codec_id

        # Single frame buffer (just latest frame)
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()

        # Decoder (PyAV)
        self._codec = None
        self._init_decoder()

        # Display
        self._window_name = "scrcpy-py-ddlx"
        self._running = False
        self._display_thread: Optional[threading.Thread] = None

    def _init_decoder(self):
        """Initialize H.264 decoder with minimal settings."""
        codec_name = "h264"  # Simple, always H264
        self._codec = av.CodecContext.create(codec_name, 'r')
        self._codec.width = self.width
        self._codec.height = self.height
        self._codec.pix_fmt = 'yuv420p'
        # Simple settings - no complex flags
        self._codec.thread_count = 1
        logger.info(f"Decoder initialized: {self.width}x{self.height}")

    def decode_packet(self, packet_data: bytes) -> bool:
        """
        Decode a single H.264 packet.

        Args:
            packet_data: Raw H.264 packet data (with annex B start codes)

        Returns:
            True if frame decoded successfully
        """
        try:
            # Create PyAV packet
            av_packet = av.Packet(packet_data)

            # Decode (may return 0 or 1 frames)
            frame_count = 0
            for frame in self._codec.decode(av_packet):
                # Convert YUV420P to RGB (simple, no special handling)
                frame_rgb = frame.reformat(format='rgb24')
                rgb_array = frame_rgb.to_ndarray().copy()

                # Store in buffer (overwrites previous)
                with self._frame_lock:
                    self._latest_frame = rgb_array

                frame_count += 1

            return frame_count > 0

        except Exception as e:
            logger.error(f"Decode error: {e}")
            return False

    def start_display(self):
        """Start display thread (OpenCV window)."""
        if not CV2_AVAILABLE:
            logger.error("OpenCV not available, cannot display")
            return

        self._running = True
        self._display_thread = threading.Thread(target=self._display_loop, daemon=True)
        self._display_thread.start()
        logger.info("Display thread started")

    def _display_loop(self):
        """Display loop - runs in separate thread."""
        cv2.namedWindow(self._window_name, cv2.WINDOW_NORMAL)

        try:
            while self._running:
                # Get latest frame
                with self._frame_lock:
                    frame = self._latest_frame
                    # Clear reference (don't hold lock during display)
                    self._latest_frame = None

                if frame is not None:
                    # Display frame (OpenCV uses BGR, our frame is RGB)
                    # Convert RGB to BGR for OpenCV
                    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

                    # Resize for display (optional - fit to screen)
                    display_frame = self._resize_for_display(frame_bgr)

                    cv2.imshow(self._window_name, display_frame)

                    # Check for quit (q key or window closed)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        logger.info("Quit requested")
                        break
                else:
                    # No frame yet
                    cv2.waitKey(10)

        except Exception as e:
            logger.error(f"Display error: {e}")
        finally:
            cv2.destroyAllWindows()
            logger.info("Display thread ended")

    def _resize_for_display(self, frame: np.ndarray) -> np.ndarray:
        """Resize frame to fit screen (max 1920x1080)."""
        max_width = 1920
        max_height = 1080

        h, w = frame.shape[:2]

        # Check if resize needed
        if w <= max_width and h <= max_height:
            return frame

        # Calculate scale
        scale = min(max_width / w, max_height / h)
        new_w = int(w * scale)
        new_h = int(h * scale)

        return cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

    def stop(self):
        """Stop display."""
        self._running = False
        if self._display_thread:
            self._display_thread.join(timeout=2.0)

    def is_running(self) -> bool:
        """Check if display is running."""
        return self._running


class SimpleStreamReceiver:
    """
    Simple stream receiver - reads from socket and decodes directly.

    This replaces the complex demuxer/decoder pipeline with a single loop.
    """

    def __init__(self, socket, player: SimpleVideoPlayer):
        """
        Initialize stream receiver.

        Args:
            socket: Connected video socket
            player: SimpleVideoPlayer instance
        """
        self._socket = socket
        self._player = player
        self._running = False
        self._receiver_thread: Optional[threading.Thread] = None

    def start(self):
        """Start receiving and decoding."""
        self._running = True
        self._receiver_thread = threading.Thread(
            target=self._receive_loop,
            daemon=True,
            name="StreamReceiver"
        )
        self._receiver_thread.start()
        logger.info("Stream receiver started")

    def _receive_loop(self):
        """Main receive loop."""
        buffer = bytearray()

        try:
            while self._running:
                # Read data from socket
                chunk = self._socket.recv(4096)
                if len(chunk) == 0:
                    logger.info("Socket closed")
                    break

                buffer.extend(chunk)

                # Try to decode H.264 packets from buffer
                # For simplicity, we assume H.264 data with start codes
                self._process_h264_buffer(buffer)

        except Exception as e:
            logger.error(f"Receive error: {e}")
        finally:
            logger.info("Stream receiver ended")

    def _process_h264_buffer(self, buffer: bytearray):
        """
        Process H.264 buffer - extract and decode packets.

        Simple approach: Look for annex B start codes (0x00 0x00 0x00 0x01)
        """
        # Find all start codes
        start_positions = []
        i = 0
        while i < len(buffer) - 4:
            if buffer[i:i+4] == b'\x00\x00\x00\x01':
                start_positions.append(i)
                i += 4
            elif buffer[i:i+3] == b'\x00\x00\x01':
                start_positions.append(i)
                i += 3
            else:
                i += 1

        # If we have at least 2 packets, decode the first one
        if len(start_positions) >= 2:
            # First packet: from start[0] to start[1]
            start = start_positions[0]
            end = start_positions[1]

            packet_data = bytes(buffer[start:end])

            # Decode this packet
            self._player.decode_packet(packet_data)

            # Remove consumed data from buffer
            del buffer[:end]

    def stop(self):
        """Stop receiving."""
        self._running = False
        if self._receiver_thread:
            self._receiver_thread.join(timeout=2.0)


# Convenience function for simple usage
def play_simple_stream(socket, width: int, height: int):
    """
    Play scrcpy video stream with simplest possible implementation.

    Args:
        socket: Connected video socket
        width: Video width
        height: Video height
    """
    player = SimpleVideoPlayer(width, height)
    player.start_display()

    receiver = SimpleStreamReceiver(socket, player)
    receiver.start()

    return player, receiver
