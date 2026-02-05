"""
Socket Manager Module

This module provides the SocketManager class for managing multiple
scrcpy socket connections (video, audio, control).
"""

import threading
import logging
import time
from typing import Optional

from .types import SocketType, SocketConfig, SocketConnectionError
from .video import VideoSocket
from .audio import AudioSocket
from .control import ControlSocket

logger = logging.getLogger(__name__)


class SocketManager:
    """
    Manage multiple scrcpy socket connections

    Handles lifecycle of video, audio, and control sockets:
    - Connection establishment
    - State monitoring
    - Error recovery
    - Cleanup

    Supports both tunnel modes:
    - adb forward (client mode): Sockets connect to device
    - adb reverse (server mode): Sockets listen for device connection

    Example:
        >>> manager = SocketManager()
        >>> # For adb forward (client mode)
        >>> manager.connect_all(port=27183, enable_audio=True)
        >>> # For adb reverse (server mode)
        >>> manager.connect_all(port=27183, is_server_mode=True, enable_audio=True)
        >>> video_data = manager.video_socket.recv(4096)
        >>> manager.control_socket.send_keycode(keycode, action)
        >>> manager.close_all()
    """

    def __init__(self):
        """Initialize socket manager"""
        self.video_socket: VideoSocket | None = None
        self.audio_socket: AudioSocket | None = None
        self.control_socket: ControlSocket | None = None
        self._lock = threading.RLock()
        self._is_server_mode: bool = False  # Track connection mode

    def connect_all(
        self,
        host: str = "127.0.0.1",
        port: int = 27183,
        enable_video: bool = True,
        enable_audio: bool = True,
        enable_control: bool = True,
        retries: int = 100,
        retry_delay: float = 0.1,
        is_server_mode: bool = False,
        tunnel: "Optional[object]" = None,
    ) -> bool:
        """
        Connect all enabled sockets

        Args:
            host: Target host (for client mode) or bind address (for server mode)
            port: Target port (for client mode) or listen port (for server mode)
            enable_video: Enable video socket
            enable_audio: Enable audio socket
            enable_control: Enable control socket
            retries: Connection retries (client mode) or accept attempts (server mode)
            retry_delay: Delay between retries
            is_server_mode: If True, use server mode (listen+accept); if False, use client mode (connect)
            tunnel: Optional ADBTunnel object (used to auto-detect connection mode)

        Returns:
            True if all connections successful

        Raises:
            SocketConnectionError: If connection fails

        Note:
            If tunnel is provided, it overrides is_server_mode:
            - tunnel.forward=False (reverse) -> server_mode=True
            - tunnel.forward=True (forward) -> server_mode=False
        """
        # Auto-detect connection mode from tunnel if provided
        if tunnel is not None:
            # Delay import to avoid circular dependency
            from ..adb import ADBTunnel

            if not isinstance(tunnel, ADBTunnel):
                logger.warning(f"Invalid tunnel type: {type(tunnel)}")
            else:
                # reverse mode (forward=False) -> server_mode=True
                # forward mode (forward=True) -> server_mode=False
                is_server_mode = not tunnel.forward
                logger.info(
                    f"Auto-detected connection mode from tunnel: "
                    f"{'reverse (server mode)' if is_server_mode else 'forward (client mode)'}"
                )

        self._is_server_mode = is_server_mode
        mode_str = "server mode (listen)" if is_server_mode else "client mode (connect)"
        logger.info(f"Connecting sockets to {host}:{port} using {mode_str}")

        with self._lock:
            # Create socket configurations with appropriate mode
            if enable_video:
                video_config = SocketConfig(
                    host=host,
                    port=port,
                    socket_type=SocketType.VIDEO,
                    is_server_mode=is_server_mode,
                )
                self.video_socket = VideoSocket(video_config)

            if enable_audio:
                audio_config = SocketConfig(
                    host=host,
                    port=port,
                    socket_type=SocketType.AUDIO,
                    is_server_mode=is_server_mode,
                )
                self.audio_socket = AudioSocket(audio_config)

            if enable_control:
                control_config = SocketConfig(
                    host=host,
                    port=port,
                    socket_type=SocketType.CONTROL,
                    is_server_mode=is_server_mode,
                )
                self.control_socket = ControlSocket(control_config)

        # Connect sockets
        if self.video_socket:
            for attempt in range(retries):
                try:
                    self.video_socket.connect_and_validate()
                    break
                except SocketConnectionError:
                    if attempt < retries - 1:
                        time.sleep(retry_delay)
                    else:
                        raise

        if self.audio_socket:
            for attempt in range(retries):
                try:
                    self.audio_socket.connect_and_validate()
                    break
                except SocketConnectionError:
                    if attempt < retries - 1:
                        time.sleep(retry_delay)
                    else:
                        logger.warning("Failed to connect audio socket")
                        self.audio_socket = None

        if self.control_socket:
            for attempt in range(retries):
                try:
                    self.control_socket.connect_and_validate()
                    break
                except SocketConnectionError:
                    if attempt < retries - 1:
                        time.sleep(retry_delay)
                    else:
                        raise

        mode_str = "server mode" if is_server_mode else "client mode"
        logger.info(f"All sockets connected successfully ({mode_str})")
        return True

    def interrupt_all(self) -> None:
        """Interrupt all socket operations"""
        with self._lock:
            if self.video_socket:
                self.video_socket.interrupt()
            if self.audio_socket:
                self.audio_socket.interrupt()
            if self.control_socket:
                self.control_socket.interrupt()

    def close_all(self) -> None:
        """Close all socket connections"""
        with self._lock:
            if self.video_socket:
                self.video_socket.close()
            if self.audio_socket:
                self.audio_socket.close()
            if self.control_socket:
                self.control_socket.close()

        logger.info("All sockets closed")

    @property
    def is_connected(self) -> bool:
        """Check if any socket is connected"""
        with self._lock:
            return (
                (self.video_socket and self.video_socket.is_connected)
                or (self.audio_socket and self.audio_socket.is_connected)
                or (self.control_socket and self.control_socket.is_connected)
            )
