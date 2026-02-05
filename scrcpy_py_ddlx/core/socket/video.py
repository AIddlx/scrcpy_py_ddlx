"""
Video Socket Module

This module provides the VideoSocket class for handling video stream
reception from the scrcpy server.
"""

import queue
import logging

from .base import ScrcpySocket
from .types import SocketConfig, SocketType, SocketConnectionError

logger = logging.getLogger(__name__)


class VideoSocket(ScrcpySocket):
    """
    Video stream socket

    Handles video data reception from scrcpy server.
    Video data is typically H.264/H.265 encoded frames.

    Example:
        >>> config = SocketConfig(socket_type=SocketType.VIDEO, port=27183)
        >>> video_sock = VideoSocket(config)
        >>> video_sock.connect()
        >>> while True:
        ...     frame_data = video_sock.recv_video_frame()
        ...     # Process frame
    """

    def __init__(self, config: SocketConfig | None = None):
        """
        Initialize video socket

        Args:
            config: Socket configuration (uses video defaults if None)
        """
        if config is None:
            config = SocketConfig(socket_type=SocketType.VIDEO)

        config.buffer_size = 256 * 1024  # Larger buffer for video
        super().__init__(config)
        self._frame_queue: queue.Queue = queue.Queue(maxsize=30)

    def connect_and_validate(self) -> bool:
        """
        Connect and validate by reading initial byte

        Returns:
            True if connection valid

        Raises:
            SocketConnectionError: If connection validation fails
        """
        self.connect()

        try:
            # Read initial byte to verify server is ready
            initial_byte = self.recv(1)
            if len(initial_byte) == 0:
                raise SocketConnectionError("No initial data received")
            logger.debug("Video socket validated")
            return True

        except Exception as e:
            self.close()
            raise SocketConnectionError(f"Video socket validation failed: {e}")

    def recv_video_frame(self) -> bytes:
        """
        Receive a video frame

        Returns:
            Frame data

        Note:
            Video frames may be split across multiple recv calls.
            Use recv_all() for complete frames.
        """
        return self.recv(self.config.buffer_size)
