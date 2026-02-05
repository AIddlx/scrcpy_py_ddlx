"""
Component initialization for scrcpy client.

This module handles the initialization of all subsystems:
- Video/Audio demuxers
- Video/Audio decoders
- Recorder (optional)
- Controller
- Screen
- AudioPlayer (optional)
- Device message receiver
- Video window (optional)
"""

import socket
import threading
import logging

from scrcpy_py_ddlx.core.protocol import CodecId
from scrcpy_py_ddlx.core.decoder import VideoDecoder, AudioDecoder
from scrcpy_py_ddlx.core.demuxer import (
    VideoDemuxer as OldVideoDemuxer,
    StreamingVideoDemuxer,
    create_streaming_video_demuxer,
    create_streaming_audio_demuxer,
)
from scrcpy_py_ddlx.core.audio.demuxer import AudioDemuxer as OldAudioDemuxer
from scrcpy_py_ddlx.core.control import ControlMessageQueue
from scrcpy_py_ddlx.core.av_player import Recorder, Screen
from scrcpy_py_ddlx.core.audio import AudioPlayer  # Use new audio subsystem (SoundDevicePlayer/QtPushAudioPlayer)
from scrcpy_py_ddlx.core.device_msg import DeviceMessageReceiver, ReceiverCallbacks
from scrcpy_py_ddlx.client.config import ClientConfig, ClientState

logger = logging.getLogger(__name__)

# Feature flag: Use streaming demuxer (NEW) or buffer-based demuxer (OLD)
USE_STREAMING_DEMUXER = True


class ComponentFactory:
    """
    Factory for creating and initializing all client components.

    This class provides methods to initialize each subsystem in the
    correct order as specified by the official scrcpy initialization sequence.
    """

    def __init__(self, config: ClientConfig, state: ClientState,
                 video_socket: socket.socket,
                 control_socket: socket.socket = None,
                 audio_socket: socket.socket = None):
        """
        Initialize component factory.

        Args:
            config: Client configuration
            state: Client state (will be updated with component references)
            video_socket: Video socket connection
            control_socket: Control socket connection (optional)
            audio_socket: Audio socket connection (optional)
        """
        self.config = config
        self.state = state
        self._video_socket = video_socket
        self._control_socket = control_socket
        self._audio_socket = audio_socket

        # Queues for demuxer->decoder communication
        self._video_packet_queue = None
        self._audio_packet_queue = None

    def create_video_demuxer(self):
        """Initialize video demuxer (Step 2)."""
        from queue import Queue

        try:
            if USE_STREAMING_DEMUXER:
                # Use streaming demuxer (NEW - recommended)
                logger.info("Using StreamingVideoDemuxer (no fixed buffer)")
                demuxer, queue = create_streaming_video_demuxer(
                    self._video_socket,
                    self.state.codec_id,
                    packet_queue_size=3  # Keep queue size for latency control
                )
            else:
                # Use buffer-based demuxer (OLD - for fallback)
                logger.info("Using buffer-based VideoDemuxer (2MB buffer)")
                queue = Queue(maxsize=3)
                demuxer = OldVideoDemuxer(
                    self._video_socket,
                    queue,
                    self.state.codec_id,
                    buffer_size=2 * 1024 * 1024  # 2MB
                )

            self._video_packet_queue = queue
            logger.info("VideoDemuxer initialized")
            return demuxer
        except Exception as e:
            logger.error(f"VideoDemuxer initialization failed: {e}")
            return None

    def create_audio_demuxer(self):
        """Initialize audio demuxer (Step 3)."""
        from queue import Queue

        try:
            # Audio uses separate socket in official architecture
            if self._audio_socket is None:
                logger.warning("Audio socket not initialized, skipping AudioDemuxer")
                return None

            if USE_STREAMING_DEMUXER:
                # Use streaming demuxer (NEW - recommended)
                demuxer, queue = create_streaming_audio_demuxer(
                    self._audio_socket,
                    self.config.audio_codec,
                    packet_queue_size=3
                )
            else:
                # Use buffer-based demuxer (OLD - for fallback)
                queue = Queue(maxsize=3)
                demuxer = OldAudioDemuxer(
                    self._audio_socket,
                    queue,
                    self.config.audio_codec,
                    buffer_size=2 * 1024 * 1024  # 2MB
                )

            self._audio_packet_queue = queue
            logger.info("AudioDemuxer initialized")
            return demuxer
        except Exception as e:
            logger.error(f"AudioDemuxer initialization failed: {e}")
            return None

    def create_video_decoder(self):
        """Initialize video decoder (Step 4)."""
        try:
            width, height = self.state.device_size
            decoder = VideoDecoder(
                width=width,
                height=height,
                codec_id=self.state.codec_id,
                packet_queue=self._video_packet_queue  # Connect to demuxer's queue
            )
            decoder.start()
            logger.info("VideoDecoder started")
            return decoder
        except Exception as e:
            logger.error(f"VideoDecoder initialization failed: {e}")
            return None

    def create_audio_decoder(self):
        """Initialize audio decoder (Step 5)."""
        try:
            decoder = AudioDecoder(
                sample_rate=48000,
                channels=2,
                audio_codec=self.config.audio_codec,
                frame_sink=None,  # Will connect to AudioPlayer in create_audio_player
                packet_queue=self._audio_packet_queue  # Connect to demuxer's queue
            )
            # Don't start yet - wait until frame_sink is connected in create_audio_player
            logger.info("AudioDecoder initialized")
            return decoder
        except Exception as e:
            logger.error(f"AudioDecoder initialization failed: {e}")
            return None

    def create_recorder(self):
        """Initialize recorder (Step 6 - optional)."""
        if not self.config.record_filename:
            return None

        try:
            def on_ended(success):
                logger.info(f"Recording {'completed' if success else 'failed'}")

            recorder = Recorder(
                filename=self.config.record_filename,
                format=self.config.record_format,
                video=True,
                audio=self.config.audio,
                on_ended=on_ended
            )

            # Initialize with video codec context
            video_codec_ctx = {
                "width": self.state.device_size[0],
                "height": self.state.device_size[1],
                "codec_id": self.state.codec_id
            }
            recorder.open(video_codec_ctx)

            # Start recorder
            recorder.start()

            logger.info(f"Recorder initialized: {self.config.record_filename}")
            return recorder
        except Exception as e:
            logger.error(f"Recorder initialization failed: {e}")
            return None

    def create_controller(self, control_loop_func):
        """Initialize controller (Step 7)."""
        try:
            control_thread = threading.Thread(
                target=control_loop_func,
                name="Controller",
                daemon=True
            )
            control_thread.start()
            logger.info("Controller started")
            return control_thread
        except Exception as e:
            logger.error(f"Controller initialization failed: {e}")
            return None

    def create_screen(self, video_decoder, video_window):
        """Initialize screen (Step 8)."""
        try:
            # Create wrapper callback for user-provided frame callback
            # CRITICAL: We still call video_window.update_frame() to trigger paintGL
            # The callback passes None to indicate "consume from DelayBuffer"
            def wrapped_frame_callback(frame):
                # Trigger video_window update (sets _has_new_frame=True)
                # This causes paintGL to consume from DelayBuffer
                if video_window is not None:
                    video_window.update_frame(None)
                # Call user-provided callback
                if self.config.frame_callback:
                    self.config.frame_callback(frame)

            # Create screen with frame callback
            screen = Screen(
                on_frame_callback=wrapped_frame_callback,
                on_init_callback=self.config.init_callback
            )

            # Initialize with video codec context
            video_codec_ctx = {
                "width": self.state.device_size[0],
                "height": self.state.device_size[1]
            }
            screen.open(video_codec_ctx)

            # CRITICAL: Pass DelayBuffer reference to Screen
            # This allows Screen to expose DelayBuffer access to video_window
            if video_decoder is not None:
                screen.set_delay_buffer(video_decoder._frame_buffer)
                logger.info("DelayBuffer reference passed to Screen")

            # Connect video decoder to screen as frame sink
            # This ensures decoded frames are pushed to the screen
            if video_decoder is not None:
                video_decoder._frame_sink = screen
                logger.info("Connected video decoder to screen")

            logger.info("Screen initialized")
            return screen
        except Exception as e:
            logger.error(f"Screen initialization failed: {e}")
            return None

    def create_video_window(self, video_decoder, control_queue):
        """Initialize video window (Step 8.5 - optional, requires PySide6)."""
        if not self.config.show_window:
            return None

        try:
            # Import from new location (not through old shim)
            from scrcpy_py_ddlx.core.player.video.factory import create_video_window

            video_window = create_video_window(use_opengl=True)
            if video_window is None:
                logger.warning("Video window creation failed (PySide6 not available)")
                return None

            # Set device info
            video_window.set_device_info(
                self.state.device_name,
                self.state.device_size[0],
                self.state.device_size[1]
            )

            # Set control queue for input events
            video_window.set_control_queue(control_queue)

            # CRITICAL: Pass DelayBuffer reference to video_window
            # This allows video_window to consume frames directly from DelayBuffer
            # instead of going through Screen's frame storage
            if video_decoder is not None:
                video_window.set_delay_buffer(video_decoder._frame_buffer)
                logger.debug("DelayBuffer reference passed to video_window")

            # Set consume callback to notify DelayBuffer when frame is rendered
            # This is critical for the consumed flag mechanism to work properly
            if video_decoder is not None:
                video_window.set_consume_callback(
                    video_decoder._frame_buffer.consume
                )
                logger.debug("Consume callback connected to DelayBuffer")

            # Show window
            video_window.show()

            logger.info("Video window initialized")
            return video_window
        except Exception as e:
            logger.error(f"Video window initialization failed: {e}")
            return None

    def create_audio_player(self, audio_decoder):
        """Initialize audio player (Step 9 - optional)."""
        if audio_decoder is None:
            return None

        try:
            # Use default AudioPlayer (QtPushAudioPlayer by default, falls back to SoundDevicePlayer)
            from scrcpy_py_ddlx.core.audio import AudioPlayer

            if AudioPlayer is None:
                logger.warning("No audio player available (install sounddevice or ensure PySide6.QtMultimedia is available)")
                return None

            player = AudioPlayer()

            # Connect audio decoder to audio player FIRST
            audio_decoder._frame_sink = player

            # Start the audio decoder first so it can detect audio parameters
            audio_decoder.start()
            logger.info("AudioDecoder started")

            # Wait briefly for decoder to process first frame and detect audio parameters
            import time
            for _ in range(20):  # Wait up to 2 seconds
                time.sleep(0.1)
                if hasattr(audio_decoder.codec_impl, 'detected_sample_rate'):
                    detected_rate = audio_decoder.codec_impl.detected_sample_rate
                    detected_ch = audio_decoder.codec_impl.detected_channels
                    if detected_rate is not None:
                        logger.info(f"Detected audio parameters: {detected_rate}Hz, {detected_ch} channels")
                        break
            else:
                # Fallback to defaults if detection failed
                logger.warning("Could not detect audio parameters, using defaults (48000Hz, 2ch)")
                detected_rate = 48000
                detected_ch = 2

            # Initialize audio player with DETECTED parameters
            audio_codec_ctx = {
                "sample_rate": detected_rate,
                "channels": detected_ch,
                "codec_type": "audio"
            }
            player.open(audio_codec_ctx)
            player.start()  # This sets _running = True

            logger.info(f"AudioPlayer initialized (using {AudioPlayer.__name__})")
            return player
        except Exception as e:
            logger.error(f"AudioPlayer initialization failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    def create_device_receiver(self, clipboard_event_callback):
        """Initialize device message receiver (clipboard, etc)."""
        try:
            # Create receiver callbacks
            callbacks = ReceiverCallbacks(
                on_clipboard=clipboard_event_callback,
                on_uhid_output=None,
                on_app_list=None  # For list_apps control message
            )

            # Check if control is enabled and socket is available
            if not self.config.control or self._control_socket is None:
                logger.info("Control disabled, skipping DeviceReceiver")
                return None

            # In forward mode, control socket is already connected
            # In reverse mode, we need to create a listening socket
            if self.state.tunnel and self.state.tunnel.forward:
                # Forward mode: use already connected control socket
                receiver = DeviceMessageReceiver(
                    socket=self._control_socket,
                    callbacks=callbacks
                )
                receiver.start()
                logger.info("DeviceReceiver started (on existing control socket)")
            elif self.state.tunnel and not self.state.tunnel.forward:
                # Reverse mode: create listening socket and accept connection
                if self.state.tunnel is None:
                    raise ConnectionError("Tunnel not initialized, cannot create control socket")

                listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                listen_socket.settimeout(self.config.socket_timeout)
                listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listen_socket.bind(("127.0.0.1", self.state.tunnel.local_port))
                listen_socket.listen(1)

                logger.info("Waiting for control connection from server...")
                control_client_socket, addr = listen_socket.accept()
                logger.info(f"Control socket connected from {addr[0]}:{addr[1]}")

                listen_socket.close()
                # Note: The control socket needs to be stored externally

                receiver = DeviceMessageReceiver(
                    socket=control_client_socket,
                    callbacks=callbacks
                )
                receiver.start()

                logger.info("DeviceReceiver started (on accepted control socket)")
                # Return both receiver and the control socket for storage
                return receiver, control_client_socket
            else:
                logger.warning("Unknown tunnel mode, skipping DeviceReceiver")
                return None

            return receiver
        except Exception as e:
            logger.error(f"DeviceReceiver initialization failed: {e}")
            return None

    def create_control_queue(self):
        """Create the control message queue."""
        return ControlMessageQueue()


__all__ = [
    "ComponentFactory",
    "USE_STREAMING_DEMUXER",
]
