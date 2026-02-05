"""
Main scrcpy client implementation.

This module provides the complete Client class that integrates all components
of the scrcpy_py_ddlx library following the official scrcpy initialization order.

Based on official scrcpy initialization: scrcpy/src/scrcpy.c
"""

import platform
import random
import socket
import logging
import time
import threading
from typing import Optional, List, Dict, Any

from scrcpy_py_ddlx.core.adb import ADBManager
from scrcpy_py_ddlx.core.protocol import (
    ControlMessageType,
    AndroidKeyEventAction,
    AndroidMotionEventAction,
    POINTER_ID_MOUSE,
    POINTER_ID_GENERIC_FINGER,
    CopyKey,
)
from scrcpy_py_ddlx.core.keycode import KeyCode
from scrcpy_py_ddlx.core.stream import StreamParser
from scrcpy_py_ddlx.core.control import ControlMessage

# Import from this package
from scrcpy_py_ddlx.client.config import ClientConfig, ClientState
from scrcpy_py_ddlx.client.components import ComponentFactory, USE_STREAMING_DEMUXER

logger = logging.getLogger(__name__)


class ScrcpyClient:
    """
    Complete scrcpy client following official initialization order.

    This client integrates all functionality directly without separate Manager classes:
    - Server connection (ADB, tunnel, sockets)
    - Component creation (demuxers, decoders, controllers)
    - Control methods (18 types)
    - Lifecycle management
    - Runtime management (Qt event loop)

    Sockets are stored in ClientState (self.state.video_socket, etc.)
    for cross-module access.

    Based on: scrcpy/src/scrcpy.c lines 400-900
    """

    def __init__(self, config: Optional[ClientConfig] = None):
        """
        Initialize the scrcpy client.

        Args:
            config: Client configuration (uses defaults if None)
        """
        from threading import Event
        from queue import Queue

        self.config = config or ClientConfig()
        self.state = ClientState()

        # Component factory (will be created during connection)
        self._component_factory = None

        # Control queue (will be created during connection)
        self._control_queue = None

        # Clipboard sequence for SET_CLIPBOARD messages
        self._clipboard_sequence = 0

        # ========== Clipboard sync state ==========
        self._clipboard_monitor_running = False
        self._last_clipboard = ""

        # ========== Lifecycle component references ==========
        self._video_demuxer = None
        self._audio_demuxer = None
        self._audio_player = None
        self._screen = None
        self._video_window = None
        self._control_thread = None
        self._recorder = None
        self._video_decoder = None
        self._audio_decoder = None
        self._device_receiver = None
        self._video_packet_queue = None
        self._audio_packet_queue = None

        # Stop event for thread coordination
        self._stop_event = Event()

        # Screenshot rate limiting
        self._screenshot_queue = Queue()  # Queue for pending screenshots
        self._screenshot_last_time = 0
        self._screenshot_min_interval = (
            0.3  # Minimum 300ms between screenshots (max ~3/sec)
        )

        # ========== Runtime decode state ==========
        # Track whether video/audio decoding is enabled (for lazy decode mode)
        self._video_enabled = True  # Start enabled, will be paused if lazy_decode is on
        self._audio_enabled = True  # Start enabled, will be paused if lazy_decode is on
        self._screenshot_worker_thread = None

        logger.info(
            f"Initialized scrcpy client for {self.config.host}:{self.config.port}"
        )

    def connect(self, timeout: Optional[float] = None) -> bool:
        """
        Connect to the scrcpy server following official initialization order.

        Initialization order (per scrcpy/src/scrcpy.c):
        1. Server connection
        2. VideoDemuxer
        3. AudioDemuxer
        4. VideoDecoder
        5. AudioDecoder
        6. Recorder (optional)
        7. Controller
        8. Screen
        9. AudioPlayer (optional)
        10. Start demuxers (LAST!)

        Args:
            timeout: Connection timeout in seconds

        Returns:
            True if connection successful
        """
        if self.state.connected:
            logger.warning("Already connected")
            return True

        timeout = timeout or self.config.connection_timeout

        try:
            # ========== STEP 1: SERVER CONNECTION ==========
            logger.info(f"Connecting to {self.config.host}:{self.config.port}")
            if not self._init_server(timeout):
                return False

            # Get socket references from state
            video_socket = self.state.video_socket
            control_socket = self.state.control_socket
            audio_socket = self.state.audio_socket  # Created during connection

            # ========== STEP 2-9: CREATE ALL COMPONENTS ==========
            self._component_factory = ComponentFactory(
                self.config, self.state, video_socket, control_socket, audio_socket
            )

            # Create control queue first
            self._control_queue = self._component_factory.create_control_queue()

            # ========== STEP 2: VIDEO DEMUXER ==========
            if self.config.video:
                self._video_demuxer = self._component_factory.create_video_demuxer()
                if self._video_demuxer is None:
                    self.disconnect()
                    return False

            # ========== STEP 3: AUDIO DEMUXER ==========
            if self.config.audio:
                self._audio_demuxer = self._component_factory.create_audio_demuxer()
                if self._audio_demuxer is None:
                    self.disconnect()
                    return False

            # ========== STEP 4: VIDEO DECODER ==========
            if self.config.video:
                self._video_decoder = self._component_factory.create_video_decoder()
                if self._video_decoder is None:
                    self.disconnect()
                    return False

            # ========== STEP 5: AUDIO DECODER ==========
            if self.config.audio:
                self._audio_decoder = self._component_factory.create_audio_decoder()

            # ========== STEP 6: RECORDER (optional) ==========
            self._recorder = self._component_factory.create_recorder()

            # ========== STEP 7: CONTROLLER ==========
            if self.config.control:
                self._control_thread = self._component_factory.create_controller(
                    self._control_loop
                )

            # ========== STEP 8: VIDEO WINDOW (create before SCREEN for callback) ==========
            if self.config.video:
                self._video_window = self._component_factory.create_video_window(
                    self._video_decoder, self._control_queue
                )

            # ========== STEP 8.5: SCREEN (after video window, so callback can reference it) ==========
            if self.config.video:
                self._screen = self._component_factory.create_screen(
                    self._video_decoder,
                    self._video_window,  # Now this will be the actual window (or None if not created)
                )
                if self._screen is None:
                    self.disconnect()
                    return False

            # ========== STEP 9: AUDIO PLAYER (optional) ==========
            if self.config.audio:
                self._audio_player = self._component_factory.create_audio_player(
                    self._audio_decoder
                )

            # ========== STEP 10: DEVICE RECEIVER ==========
            device_receiver_result = self._component_factory.create_device_receiver(
                self._on_clipboard_event
            )
            if device_receiver_result is not None:
                if isinstance(device_receiver_result, tuple):
                    # Reverse mode: returns (receiver, control_socket)
                    self._device_receiver, control_socket = device_receiver_result
                    self.state.control_socket = control_socket
                else:
                    self._device_receiver = device_receiver_result

            # ========== STEP 11: START DEMUXERS (LAST!) ==========
            if self.config.video and not self.start_video_demuxer():
                self.disconnect()
                return False

            if self.config.audio and not self.start_audio_demuxer():
                self.disconnect()
                return False

            # ========== STEP 12: LAZY DECODE (Energy-efficient mode) ==========
            # If lazy_decode is enabled, pause decoders immediately to save CPU
            # They will auto-resume when needed (screenshot, recording, etc.)
            # Note: lazy_decode is disabled when show_window=True
            effective_lazy_decode = self.config.lazy_decode and not self.config.show_window
            if effective_lazy_decode:
                if self.config.video:
                    self.disable_video()  # Pause video decoder
                    logger.info("Lazy decode mode: video paused (will auto-resume for screenshot)")
                if self.config.audio:
                    self.disable_audio()  # Pause audio decoder
                    logger.info("Lazy decode mode: audio paused (will auto-resume for recording)")

            # ========== SUCCESS ==========
            self.state.connected = True
            self.state.running = True
            logger.info("Client fully initialized and connected")

            # Start clipboard sync if enabled
            if self.config.clipboard_autosync:
                self._start_clipboard_monitor()

            return True

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self.disconnect()
            return False

    def disconnect(self) -> None:
        """
        Disconnect from the scrcpy server following official cleanup order.

        Cleanup order (reverse of initialization):
        1. Stop demuxers
        2. Stop audio player
        3. Stop screen
        4. Stop controller
        5. Stop recorder
        6. Stop decoders
        7. Close sockets
        """
        if not self.state.connected:
            return

        logger.info("Disconnecting...")
        self.state.running = False
        self.state.connected = False
        self._stop_event.set()

        # Stop clipboard monitor
        self._stop_clipboard_monitor()

        # ========== Cleanup in reverse order ==========

        # Stop demuxers first (reverse of start)
        if self._video_demuxer is not None:
            self._video_demuxer.stop()
            self._video_demuxer = None

        if self._audio_demuxer is not None:
            self._audio_demuxer.stop()
            self._audio_demuxer = None

        # Stop audio player
        if self._audio_player is not None:
            self._audio_player.stop()
            self._audio_player.close()
            self._audio_player = None

        # Stop screen
        if self._screen is not None:
            self._screen.close()
            self._screen = None

        # Stop video window
        if self._video_window is not None:
            try:
                self._video_window.close()
            except Exception:
                pass
            self._video_window = None

        # Stop controller
        if self._control_thread is not None:
            self._stop_event.set()
            self._control_thread.join(timeout=2.0)
            self._control_thread = None

        # Stop recorder
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder = None

        # Stop decoders
        if self._video_decoder is not None:
            self._video_decoder.stop()
            self._video_decoder = None

        if self._audio_decoder is not None:
            self._audio_decoder.stop()
            self._audio_decoder = None

        # Stop device receiver
        if self._device_receiver is not None:
            self._device_receiver.stop()
            self._device_receiver = None

        # Close sockets (last)
        if self.state.video_socket is not None:
            try:
                self.state.video_socket.close()
            except Exception:
                pass
            self.state.video_socket = None

        if self.state.audio_socket is not None:
            try:
                self.state.audio_socket.close()
            except Exception:
                pass
            self.state.audio_socket = None

        if self.state.control_socket is not None:
            try:
                self.state.control_socket.close()
            except Exception:
                pass
            self.state.control_socket = None

        # Clear queues
        self._clear_queue(self._video_packet_queue)
        self._clear_queue(self._audio_packet_queue)

        # Cleanup TCP/IP connection if enabled
        if self.state.tcpip_connected and self.config.tcpip_auto_disconnect:
            if self.state.tcpip_ip:
                from scrcpy_py_ddlx.core.adb import ADBManager

                adb = ADBManager()
                logger.info(
                    f"Auto-disconnecting TCP/IP: {self.state.tcpip_ip}:{self.state.tcpip_port}"
                )
                adb.disconnect_tcpip(self.state.tcpip_ip, self.state.tcpip_port)
                self.state.tcpip_connected = False
                self.state.tcpip_ip = None
                logger.info("TCP/IP disconnected")

        # Reset stop event
        self._stop_event.clear()

        logger.info("Disconnected")

    def _control_loop(self) -> None:
        """Controller thread main loop."""
        logger.info("Controller loop started")
        stop_event = self._stop_event

        while not stop_event.is_set():
            try:
                msg = self._control_queue.get(timeout=0.1)
                if msg is None:
                    # Timeout, continue waiting (don't exit!)
                    # This is normal behavior when no input events are occurring
                    continue

                logger.debug(f"← Got control message from queue: {msg.type.name}")
                # Serialize and send message
                data = msg.serialize()
                logger.debug(f"Control message serialized: {len(data)} bytes")

                # Use control socket if available, otherwise fallback to video socket
                target_socket = (
                    self.state.control_socket
                    if self.state.control_socket
                    else self.state.video_socket
                )

                if target_socket:
                    socket_type = (
                        "control"
                        if target_socket is self.state.control_socket
                        else "video"
                    )
                    logger.debug(f"Using socket: {socket_type}")
                    logger.debug(
                        f"Sending {len(data)} bytes to {socket_type} socket..."
                    )
                    target_socket.sendall(data)
                    logger.debug(
                        f"Successfully sent {len(data)} bytes to {socket_type} socket"
                    )
                else:
                    logger.warning("No socket available for control message")

            except Exception as e:
                if not stop_event.is_set():
                    logger.error(f"Controller loop error: {e}")
                    import traceback

                    traceback.print_exc()
                break

        logger.info("Controller loop ended")

    # ===== Lifecycle management methods =====

    def _clear_queue(self, queue):
        """Clear all items from a queue."""
        if queue is None:
            return
        while not queue.empty():
            try:
                queue.get_nowait()
            except Exception:
                break

    def start_video_demuxer(self) -> bool:
        """
        Start video demuxer (Step 10 - MUST BE LAST!).

        Returns:
            True if successful, False otherwise
        """
        try:
            if self._video_demuxer is not None:
                self._video_demuxer.start()
                logger.info("VideoDemuxer started")
                return True
            return False
        except Exception as e:
            logger.error(f"VideoDemuxer start failed: {e}")
            return False

    def start_audio_demuxer(self) -> bool:
        """
        Start audio demuxer (Step 10 - MUST BE LAST!).

        Returns:
            True if successful, False otherwise
        """
        try:
            if self._audio_demuxer is not None:
                self._audio_demuxer.start()
                logger.info("AudioDemuxer started")
                return True
            return False
        except Exception as e:
            logger.error(f"AudioDemuxer start failed: {e}")
            return False

    def _setup_tcpip_connection(
        self, adb: "ADBManager", device: "ADBDevice", timeout: float
    ) -> bool:
        """
        Setup TCP/IP wireless connection to device (seamless mode).

        This method enables TCP/IP as a parallel connection path WITHOUT disconnecting
        the existing USB connection. ADB will automatically migrate to TCP/IP when
        USB is disconnected, ensuring a truly seamless experience.

        Args:
            adb: ADBManager instance
            device: Selected ADB device (must be USB connected initially)
            timeout: Connection timeout

        Returns:
            True if TCP/IP setup successful, False otherwise

        Flow (Seamless Mode):
        1. Keep USB connection active
        2. Get device IP address
        3. Enable TCP/IP mode on device (adbd restarts but USB connection persists)
        4. Connect to TCP/IP (now BOTH USB and TCP/IP are active)
        5. ADB automatically uses the best path
        6. User can unplug USB anytime - connection automatically migrates to TCP/IP
        """
        from scrcpy_py_ddlx.core.adb import ADBDeviceType

        # Store original device type
        self.state.original_device_type = device.device_type.value

        # Check if device is already connected via TCP/IP
        if device.device_type == ADBDeviceType.TCPIP:
            ip_port = device.serial  # Format: "192.168.x.x:5555"
            ip = ip_port.split(":")[0]
            port = (
                int(ip_port.split(":")[1]) if ":" in ip_port else self.config.tcpip_port
            )
            logger.info(f"Device already connected via TCP/IP: {ip_port}")
            self.state.tcpip_connected = True
            self.state.tcpip_ip = ip
            self.state.tcpip_port = port
            return True

        # Specific IP provided - connect without enabling tcpip mode
        if self.config.tcpip_ip:
            ip = self.config.tcpip_ip
            port = self.config.tcpip_port
            logger.info(f"Adding TCP/IP connection to {ip}:{port} (parallel to USB)")

            if not adb.connect_tcpip(ip, port, timeout=timeout):
                logger.warning(f"Could not connect to {ip}:{port}, continuing with USB")
                # Don't fail - USB connection is still active
                return True

            self.state.tcpip_connected = True
            self.state.tcpip_ip = ip
            self.state.tcpip_port = port
            logger.info(
                f"TCP/IP connection added: {ip}:{port} - USB can now be unplugged!"
            )
            return True

        # Auto mode: enable TCP/IP while keeping USB connection
        logger.info("Setting up seamless TCP/IP connection (USB stays active)...")

        # Get device IP via USB connection (this still works!)
        ip = adb.get_device_ip(device.serial, timeout=timeout)
        if not ip:
            logger.warning(
                "Could not detect device IP address, continuing with USB only"
            )
            return False  # Return False to prevent TCP/IP setup

        port = self.config.tcpip_port
        logger.info(f"Detected device IP: {ip}")

        # Check if TCP/IP is already enabled
        current_port = adb.get_adb_tcp_port(device.serial, timeout=5.0)
        if current_port:
            if current_port == port:
                logger.info(f"TCP/IP already enabled on port {port}")
            else:
                logger.info(f"TCP/IP enabled on port {current_port}, using {port}")
                port = current_port  # Use existing port
        else:
            # Enable TCP/IP mode - THIS DOES NOT DISCONNECT USB!
            logger.info(f"Enabling TCP/IP mode on port {port} (USB stays active)...")
            if not adb.enable_tcpip(device.serial, port, timeout=timeout):
                logger.warning("Failed to enable TCP/IP mode, continuing with USB only")
                return True  # Don't fail - USB connection is still active

        # Wait for TCP/IP to be ready (USB connection still works!)
        if not adb.wait_for_tcpip_enabled(
            device.serial, port, max_attempts=40, delay=0.25
        ):
            logger.warning("Timeout waiting for TCP/IP mode, continuing with USB only")
            return True  # Don't fail - USB connection is still active

        # Connect to TCP/IP (now BOTH USB and TCP/IP are active!)
        logger.info(f"Adding TCP/IP connection to {ip}:{port}...")
        if not adb.connect_tcpip(ip, port, timeout=timeout):
            logger.warning(
                f"Could not connect to {ip}:{port}, continuing with USB only"
            )
            return True  # Don't fail - USB connection is still active

        # Store TCP/IP connection info
        self.state.tcpip_connected = True
        self.state.tcpip_ip = ip
        self.state.tcpip_port = port

        logger.info(f"=== SEAMLESS MODE READY ===")
        logger.info(f"USB connection: ACTIVE")
        logger.info(f"TCP/IP connection: {ip}:{port} - STANDBY")
        logger.info(f"You can now unplug USB - connection will migrate automatically!")

        return True

    # ===== Server connection methods =====

    def _init_server(self, timeout: float) -> bool:
        """
        Initialize server connection using ADB (Step 1).

        Uses official scrcpy connection flow:
        1. Create ADBManager
        2. Select device
        3. [TCP/IP] Enable TCP/IP mode if configured (seamless: USB stays active)
        4. Determine target device serial (TCP/IP if enabled, otherwise USB)
        5. Push server to target device
        6. Generate socket name and SCID
        7. Create tunnel (forward on Windows, reverse preferred on Linux/Mac)
        8. Start server on target device
        9. Connect to server socket
        10. Read device metadata

        Returns:
            True if connection successful, False otherwise
        """
        try:
            # 1-2. Initialize ADB and select device
            # Official scrcpy behavior: Auto-detect any available device
            # Priority: USB > TCP/IP > Error
            adb = ADBManager(timeout=timeout)
            from scrcpy_py_ddlx.core.adb import ADBDeviceType

            device = None
            tcpip_device_serial = None

            # List all ADB devices (both USB and TCP/IP)
            all_devices = adb.list_devices()

            # Debug: Show device count
            logger.info(f"ADB found {len(all_devices)} device(s)")

            if not all_devices:
                # No devices found (same behavior as official scrcpy)
                logger.error("No ADB devices found.")
                logger.info("")
                logger.info("Possible reasons:")
                logger.info("  1. Device not connected via USB")
                logger.info("  2. Device was in TCP/IP mode but went to sleep")
                logger.info("  3. Device disconnected from WiFi")
                logger.info("")
                logger.info("Solution:")
                logger.info("  - Connect device via USB to enable TCP/IP mode")
                logger.info("  - Or ensure device is awake and on WiFi")
                return False

            # Separate USB and TCP/IP devices
            usb_devices = [d for d in all_devices if d.device_type == ADBDeviceType.USB]
            tcpip_devices = [
                d for d in all_devices if d.device_type == ADBDeviceType.TCPIP
            ]

            logger.info(f"  - USB devices: {len(usb_devices)}")
            logger.info(f"  - TCP/IP devices: {len(tcpip_devices)}")

            # Priority 1: Use USB device if available
            if usb_devices:
                device = usb_devices[0]  # Use first USB device
                logger.info(f"Selected USB device: {device.serial}")
            # Priority 2: Use existing TCP/IP device if no USB
            elif tcpip_devices:
                device = tcpip_devices[0]  # Use first TCP/IP device
                tcpip_device_serial = device.serial  # Already in "ip:port" format
                # Extract IP and port from serial
                ip, port = device.serial.split(":")
                self.state.tcpip_connected = True
                self.state.tcpip_ip = ip
                self.state.tcpip_port = int(port)
                logger.info(f"Using existing TCP/IP connection: {device.serial}")
            else:
                # Shouldn't happen as we checked all_devices above
                logger.error("No available devices found")
                return False

            # 3. TCP/IP wireless connection setup (if enabled and USB device is selected)
            # Skip if already using TCP/IP device (no USB case)
            if self.config.tcpip and tcpip_device_serial is None:
                if not self._setup_tcpip_connection(adb, device, timeout):
                    logger.error("TCP/IP connection setup failed")
                    return False
                # Store the TCP/IP device serial for subsequent operations
                tcpip_device_serial = f"{self.state.tcpip_ip}:{self.state.tcpip_port}"
                logger.info(f"TCP/IP ready: {tcpip_device_serial}")
                logger.info(
                    f"Now will use TCP/IP device for all operations (USB can be unplugged anytime)"
                )

            # 4. Determine the device serial to use for all subsequent operations
            # When TCP/IP is enabled, use the TCP/IP device serial for all operations
            # This ensures that when both USB and TCP/IP are connected, ADB knows
            # which device to target (avoiding "more than one device" error)
            target_serial = (
                tcpip_device_serial if tcpip_device_serial else device.serial
            )
            if tcpip_device_serial:
                logger.info(f"Using TCP/IP device for all operations: {target_serial}")
                logger.info(f"Continuing with original device: {device.serial}")

            # Store device serial for later operations (like list_apps)
            self.state.device_serial = target_serial

            # 5. Push server (use target serial: TCP/IP if available, otherwise USB)
            server_jar = (
                self.config.server_jar
                if hasattr(self.config, "server_jar")
                else "scrcpy-server"
            )
            # Push to /data/local/tmp/scrcpy-server (no .jar extension - disguised APK)
            # Note: CLASSPATH will use .jar extension but actual file has no extension
            adb.push_server(
                target_serial, server_jar, remote_path="/data/local/tmp/scrcpy-server"
            )
            logger.info("Server pushed to device")

            # 6. Generate socket name and SCID
            # IMPORTANT: Use random 31-bit SCID (0 to 0x7FFFFFFF) for proper socket naming
            # Socket name MUST be "scrcpy_XXXXXXXX" format (8-digit hex lowercase)
            scid = random.randint(0, 0x7FFFFFFF)  # 31-bit non-negative integer
            socket_name = f"scrcpy_{scid:08x}"  # Format: "scrcpy_12345678"

            # 7. Detect platform - Windows has issues with reverse tunnel
            is_windows = platform.system() == "Windows"

            # Try reverse mode first (Linux/Mac), but use forward mode on Windows
            # due to ADB reverse tunnel issues on Windows
            local_port = 27183

            if is_windows:
                # Windows: Use forward mode directly (reverse tunnel has issues)
                logger.info(
                    "Windows detected: using forward mode (reverse tunnel has issues on Windows)"
                )
                use_forward = True
                self.state.is_forward_mode = True
            else:
                # Linux/Mac: Try reverse mode first, fallback to forward
                use_forward = False

            if use_forward:
                tunnel = self._connect_forward_mode(
                    adb, target_serial, scid, socket_name, local_port, timeout
                )
            else:
                tunnel = self._connect_reverse_mode(
                    adb, target_serial, scid, socket_name, local_port, timeout
                )

            # Store tunnel in state for creating additional sockets
            self.state.tunnel = tunnel

            # Read device metadata (after connection established)
            self._read_device_metadata()

            return True

        except Exception as e:
            logger.error(f"Server initialization failed: {e}")
            return False

    def _connect_forward_mode(
        self,
        adb,
        device_serial: str,
        scid: int,
        socket_name: str,
        local_port: int,
        timeout: float,
    ):
        """
        Connect using forward mode (Windows, or when reverse fails).

        FORWARD MODE: server listens, client connects
        CORRECT SEQUENCE (based on official scrcpy):
        1. Create forward tunnel FIRST (so socket path exists when server calls accept())
        2. Start server in background (server creates LocalServerSocket and waits for accept())
        3. Connect (triggers server's accept() to return)
        4. Immediately read dummy byte (server sends it after accept())

        Args:
            adb: ADBManager instance
            device_serial: Device serial to use (USB or TCP/IP)
            scid: Socket connection ID
            socket_name: Socket name
            local_port: Local port number
            timeout: Operation timeout
        """
        # Step 1: Create forward tunnel FIRST
        # IMPORTANT: Pass device_serial to avoid "more than one device" error
        adb._execute(
            ["forward", f"tcp:{local_port}", f"localabstract:{socket_name}"],
            device_serial=device_serial,
            timeout=5.0,
        )
        logger.info(
            f"Forward tunnel created: tcp:{local_port} <-> localabstract:{socket_name}"
        )

        # Step 2: Start server in background
        # IMPORTANT: Forward mode requires 3 separate connections by default (video, audio, control)
        # CRITICAL: Add max_fps to prevent frame rate mismatch (phone 90fps vs display 60fps)
        # Build server parameters (following official scrcpy logic)
        # Note: audio=true is the default, so we only pass audio=false when disabled
        # Note: audio_source=output is the default, so we don't pass it
        audio_params = "" if self.config.audio else "audio=false"
        stay_awake_params = "stay_awake=true" if self.config.stay_awake else ""
        clipboard_params = (
            "clipboard_autosync=true"
            if self.config.clipboard_autosync
            else "clipboard_autosync=false"
        )
        server_params = (
            f"scid={scid:08x} "
            f"tunnel_forward=true "
            f"{audio_params} "
            f"control=true "
            f"{clipboard_params} "
            f"log_level=info "
            f"video_bit_rate={self.config.bitrate} "
            f"max_fps={self.config.max_fps} "
            f"{stay_awake_params}"
        ).strip()
        adb.start_server(
            serial=device_serial,  # Use device_serial (may be TCP/IP)
            client_version="3.3.4",
            server_params=server_params,
            timeout=timeout,
            background=True,
        )
        logger.info("Server started on device (listening on localabstract:scrcpy)")

        # Step 3: Wait for server to initialize and call accept()
        logger.info("Waiting for server to be ready...")
        time.sleep(0.5)  # Short wait - rely on retry mechanism

        # Step 4: Connect with retry mechanism (official scrcpy retries 100 times)
        max_retries = 100  # Official scrcpy retries up to 100 times
        retry_delay = 0.1  # 100ms between retries
        connected = False

        for attempt in range(max_retries):
            self.state.video_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.state.video_socket.settimeout(10.0)
            try:
                logger.debug(f"Connection attempt {attempt + 1}/{max_retries}")
                self.state.video_socket.connect(("127.0.0.1", local_port))
                logger.info("Connected to server!")

                # Step 5: IMMEDIATELY read dummy byte (no extra delay!)
                self.state.video_socket.settimeout(
                    3.0
                )  # 3 second timeout for dummy byte
                dummy_byte = self.state.video_socket.recv(1)
                logger.debug(f"Received {len(dummy_byte)} bytes")

                if len(dummy_byte) == 0:
                    # Connection closed by server
                    raise ConnectionError(
                        "Connection closed by server - no data received"
                    )

                logger.info(f"✓ Dummy byte received: {dummy_byte[0]:02x}")

                connected = True
                break
            except Exception as e:
                logger.debug(f"Connection attempt {attempt + 1} failed: {e}")
                self.state.video_socket.close()
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)

        if not connected:
            raise ConnectionError("Failed to connect after multiple attempts")

        self.state.video_socket.settimeout(10.0)  # Restore timeout for subsequent reads

        # Step 6: Connect audio socket (if audio is enabled)
        # CRITICAL: Official scrcpy order is video -> audio -> control
        logger.info(
            f"[SOCKET ORDER] Video connected (1/3). Audio enabled: {self.config.audio}"
        )
        if self.config.audio:
            logger.info("[SOCKET ORDER] Connecting audio socket (2/3)...")
            self.state.audio_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.state.audio_socket.settimeout(5.0)

            try:
                self.state.audio_socket.connect(("127.0.0.1", local_port))
                # Audio socket does NOT receive dummy byte (only first socket does)
                logger.info("✓ Audio socket connected")

                # Disable Nagle's algorithm for audio socket
                self.state.audio_socket.setsockopt(
                    socket.IPPROTO_TCP, socket.TCP_NODELAY, 1
                )
            except Exception as e:
                logger.error(f"Audio socket connection failed: {e}")
                self.state.audio_socket = None  # Mark as not connected
                # Continue anyway - audio is optional

        # Step 7: Connect control socket (required for sending key events)
        # CRITICAL: Must be AFTER audio socket (official order: video -> audio -> control)
        logger.info("[SOCKET ORDER] Connecting control socket (3/3)...")
        self.state.control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.state.control_socket.settimeout(5.0)

        try:
            self.state.control_socket.connect(("127.0.0.1", local_port))
            # Control socket does NOT receive dummy byte (only first socket does)
            logger.info("✓ Control socket connected")

            # Disable Nagle's algorithm for control socket (official scrcpy does this)
            self.state.control_socket.setsockopt(
                socket.IPPROTO_TCP, socket.TCP_NODELAY, 1
            )
        except Exception as e:
            logger.error(f"Control socket connection failed: {e}")
            self.state.control_socket = None  # Mark as not connected
            # Continue anyway - control is optional for basic viewing

        tunnel = type(
            "obj",
            (object,),
            {"enabled": True, "forward": True, "local_port": local_port},
        )()
        return tunnel

    def _connect_reverse_mode(
        self,
        adb,
        device_serial: str,
        scid: int,
        socket_name: str,
        local_port: int,
        timeout: float,
    ):
        """
        Connect using reverse mode (Linux/Mac preferred).

        REVERSE MODE: client listens, server connects

        Note: scrcpy server creates multiple connections with different socket names:
        - scrcpy_{scid:08x} - Video socket
        - scrcpy_{scid:08x}_audio - Audio socket
        - scrcpy_{scid:08x}_control - Control socket

        Args:
            adb: ADBManager instance
            device_serial: Device serial to use (USB or TCP/IP)
            scid: Socket connection ID
            socket_name: Socket name
            local_port: Local port number
            timeout: Operation timeout
        """
        # Clean up any existing reverse tunnel
        try:
            adb._execute(
                ["reverse", "--remove", f"localabstract:{socket_name}"],
                device_serial=device_serial,
                timeout=2.0,
                capture_output=False,
            )
        except:
            pass

        # Create reverse tunnel for video socket
        adb._execute(
            ["reverse", f"localabstract:{socket_name}", f"tcp:{local_port}"],
            device_serial=device_serial,
            timeout=5.0,
        )
        logger.info(
            f"Reverse tunnel created: localabstract:{socket_name} <-> tcp:{local_port}"
        )

        # Create video server socket and listen
        self.state.video_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.state.video_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.state.video_socket.bind(("127.0.0.1", local_port))
        self.state.video_socket.listen(1)
        self.state.video_socket.settimeout(self.config.socket_timeout)
        logger.info(
            f"Client listening on 127.0.0.1:{local_port} for video connection..."
        )

        # Start server
        audio_params = (
            f"audio=true audio_source=output" if self.config.audio else "audio=false"
        )
        stay_awake_params = "stay_awake=true" if self.config.stay_awake else ""
        clipboard_params = (
            "clipboard_autosync=true"
            if self.config.clipboard_autosync
            else "clipboard_autosync=false"
        )
        server_params = (
            f"scid={scid:08x} "
            f"tunnel_forward=false "
            f"{audio_params} "
            f"control=true "
            f"{clipboard_params} "
            f"log_level=info "
            f"max_fps={self.config.max_fps} "
            f"{stay_awake_params}"
        ).strip()
        adb.start_server(
            serial=device_serial,  # Use device_serial (may be TCP/IP)
            client_version="3.3.4",
            server_params=server_params,
            timeout=timeout,
        )
        logger.info("Server started, waiting for connections via reverse tunnel...")

        # Accept video connection from server
        client_socket, addr = self.state.video_socket.accept()
        logger.info(f"Video socket connected from {addr[0]}:{addr[1]}")

        # Replace server socket with client socket
        self.state.video_socket.close()
        self.state.video_socket = client_socket

        # Create reverse tunnels and sockets for audio (if enabled)
        if self.config.audio:
            audio_socket_name = f"{socket_name}_audio"
            audio_local_port = local_port + 1  # Use different port

            # Clean up and create reverse tunnel for audio
            try:
                adb._execute(
                    ["reverse", "--remove", f"localabstract:{audio_socket_name}"],
                    device_serial=device_serial,
                    timeout=2.0,
                    capture_output=False,
                )
            except:
                pass

            adb._execute(
                [
                    "reverse",
                    f"localabstract:{audio_socket_name}",
                    f"tcp:{audio_local_port}",
                ],
                device_serial=device_serial,
                timeout=5.0,
            )
            logger.info(
                f"Audio reverse tunnel created: localabstract:{audio_socket_name} <-> tcp:{audio_local_port}"
            )

            # Create and listen for audio socket
            audio_server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            audio_server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            audio_server_socket.bind(("127.0.0.1", audio_local_port))
            audio_server_socket.listen(1)
            audio_server_socket.settimeout(5.0)

            try:
                audio_client_socket, audio_addr = audio_server_socket.accept()
                logger.info(
                    f"Audio socket connected from {audio_addr[0]}:{audio_addr[1]}"
                )
                audio_server_socket.close()
                self.state.audio_socket = audio_client_socket
                # Disable Nagle's algorithm for audio socket
                self.state.audio_socket.setsockopt(
                    socket.IPPROTO_TCP, socket.TCP_NODELAY, 1
                )
            except Exception as e:
                logger.error(f"Audio socket connection failed: {e}")
                self.state.audio_socket = None
                audio_server_socket.close()

        # Create reverse tunnel and socket for control
        control_socket_name = f"{socket_name}_control"
        control_local_port = local_port + 2  # Use different port

        # Clean up and create reverse tunnel for control
        try:
            adb._execute(
                ["reverse", "--remove", f"localabstract:{control_socket_name}"],
                device_serial=device_serial,
                timeout=2.0,
                capture_output=False,
            )
        except:
            pass

        adb._execute(
            [
                "reverse",
                f"localabstract:{control_socket_name}",
                f"tcp:{control_local_port}",
            ],
            device_serial=device_serial,
            timeout=5.0,
        )
        logger.info(
            f"Control reverse tunnel created: localabstract:{control_socket_name} <-> tcp:{control_local_port}"
        )

        # Create and listen for control socket
        control_server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        control_server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        control_server_socket.bind(("127.0.0.1", control_local_port))
        control_server_socket.listen(1)
        control_server_socket.settimeout(5.0)

        try:
            control_client_socket, control_addr = control_server_socket.accept()
            logger.info(
                f"Control socket connected from {control_addr[0]}:{control_addr[1]}"
            )
            control_server_socket.close()
            self.state.control_socket = control_client_socket
            # Disable Nagle's algorithm for control socket
            self.state.control_socket.setsockopt(
                socket.IPPROTO_TCP, socket.TCP_NODELAY, 1
            )
        except Exception as e:
            logger.error(f"Control socket connection failed: {e}")
            self.state.control_socket = None
            control_server_socket.close()

        tunnel = type(
            "obj",
            (object,),
            {"enabled": True, "forward": False, "local_port": local_port},
        )()
        return tunnel

    def _read_device_metadata(self):
        """Read device metadata (name, codec ID, video size) from server."""
        # IMPORTANT: Use MSG_WAITALL-like behavior to receive complete data
        # Python's recv() doesn't guarantee receiving all bytes, so we must loop

        # Read device name (64 bytes) - loop until all received
        logger.debug("Reading device name (64 bytes)...")
        self.state.video_socket.settimeout(5.0)  # 5 second timeout for complete read

        device_name_bytes = b""
        bytes_needed = 64
        while len(device_name_bytes) < bytes_needed:
            chunk = self.state.video_socket.recv(bytes_needed - len(device_name_bytes))
            if len(chunk) == 0:
                # Connection closed
                if len(device_name_bytes) == 0:
                    raise ConnectionError(
                        "Connection closed by server (no device name received)"
                    )
                logger.warning(
                    f"Connection closed after {len(device_name_bytes)} bytes, padding with zeros"
                )
                device_name_bytes += b"\x00" * (bytes_needed - len(device_name_bytes))
                break
            device_name_bytes += chunk

        logger.debug(f"Received {len(device_name_bytes)} bytes for device name")
        self.state.device_name = device_name_bytes.rstrip(b"\x00").decode(
            "utf-8", errors="ignore"
        )
        logger.info(f"Device name: {self.state.device_name}")

        if self.config.video:
            # Initialize stream parser locally
            stream_parser = StreamParser()

            # Read codec ID (4 bytes) - loop until all received
            self.state.video_socket.settimeout(5.0)
            codec_id_bytes = b""
            bytes_needed = 4
            while len(codec_id_bytes) < bytes_needed:
                chunk = self.state.video_socket.recv(bytes_needed - len(codec_id_bytes))
                if len(chunk) == 0:
                    raise ConnectionError(
                        f"Connection closed while reading codec ID (got {len(codec_id_bytes)}/4 bytes)"
                    )
                codec_id_bytes += chunk
            codec_id, _ = stream_parser.parse_codec_id(codec_id_bytes)
            self.state.codec_id = codec_id
            logger.info(f"Codec ID: 0x{codec_id:08x}")

            # Read video size (8 bytes: width + height) - loop until all received
            self.state.video_socket.settimeout(5.0)
            size_bytes = b""
            bytes_needed = 8
            while len(size_bytes) < bytes_needed:
                chunk = self.state.video_socket.recv(bytes_needed - len(size_bytes))
                if len(chunk) == 0:
                    raise ConnectionError(
                        f"Connection closed while reading video size (got {len(size_bytes)}/8 bytes)"
                    )
                size_bytes += chunk
            width, height, _ = stream_parser.parse_video_size(size_bytes)
            self.state.device_size = (width, height)
            logger.info(f"Video size: {width}x{height}")

    def _on_clipboard_event(self, text: str, sequence: int) -> None:
        """
        Handle clipboard event from device (device → PC sync).

        When the device clipboard changes, this callback is triggered.
        We update the PC clipboard to match.
        """
        logger.info(f"[Device → PC] Clipboard sync: {text[:50]}...")

        # Update PC clipboard
        try:
            import win32clipboard

            win32clipboard.SetClipboardText(text)
            logger.debug(f"PC clipboard updated: {text[:30]}...")
        except Exception as e:
            # Fallback to pyperclip if Windows API fails
            try:
                import pyperclip

                pyperclip.copy(text)
                logger.debug(f"PC clipboard updated (pyperclip): {text[:30]}...")
            except Exception as e2:
                logger.error(f"Failed to update PC clipboard: {e}, {e2}")

    def _start_clipboard_monitor(self) -> None:
        """
        Start clipboard monitor thread (PC → device sync).

        Monitors PC clipboard changes and automatically syncs to device.
        Uses Windows clipboard API for reliable access.
        """
        import threading
        import time

        self._clipboard_monitor_running = True
        self._last_clipboard = ""

        def monitor_thread():
            """Monitor PC clipboard and sync to device."""
            try:
                # Try Windows API first
                import win32clipboard
                import win32con

                logger.info(
                    f"[Clipboard Monitor] Started (PC → Device sync, using Windows API), connected={self.state.connected}"
                )

                poll_count = 0
                while self._clipboard_monitor_running and self.state.connected:
                    try:
                        poll_count += 1

                        # Get current PC clipboard (Windows API)
                        try:
                            win32clipboard.OpenClipboard()
                            try:
                                # Get CF_UNICODETEXT text from clipboard
                                text_data = win32clipboard.GetClipboardData(
                                    win32con.CF_UNICODETEXT
                                )
                                current = (
                                    text_data
                                    if isinstance(text_data, str)
                                    else str(text_data)
                                )
                            finally:
                                win32clipboard.CloseClipboard()

                            # Info log every 10 polls (5 seconds)
                            if poll_count % 10 == 0:
                                curr_preview = current[:20] if current else "(empty)"
                                last_preview = (
                                    self._last_clipboard[:20]
                                    if self._last_clipboard
                                    else "(empty)"
                                )
                                logger.info(
                                    f"[Clipboard Monitor] Poll #{poll_count}, current='{curr_preview}...', last='{last_preview}...'"
                                )
                        except Exception as e:
                            # Windows API sometimes fails on empty clipboard
                            logger.debug(
                                f"[Clipboard Monitor] GetClipboardData failed: {e}"
                            )
                            current = ""

                        # Check if changed (ignore empty clipboard)
                        if current and current != self._last_clipboard:
                            logger.info(
                                f"[PC → Device] Clipboard sync: {current[:50]}..."
                            )

                            # Send to device (sync only, don't auto-paste)
                            self.set_clipboard(current, paste=False)
                            self._last_clipboard = current

                    except Exception as e:
                        logger.warning(f"[Clipboard Monitor] Error: {e}")

                    # Poll every 500ms
                    time.sleep(0.5)

                logger.info(
                    f"[Clipboard Monitor] Loop ended: running={self._clipboard_monitor_running}, connected={self.state.connected}"
                )

            except ImportError:
                # Fallback to pyperclip if win32clipboard not available
                import pyperclip

                logger.info(
                    "[Clipboard Monitor] Started (PC → Device sync, using pyperclip)"
                )

                while self._clipboard_monitor_running and self.state.connected:
                    try:
                        current = pyperclip.paste()

                        if current and current != self._last_clipboard:
                            logger.info(
                                f"[PC → Device] Clipboard sync: {current[:50]}..."
                            )
                            self.set_clipboard(current, paste=False)
                            self._last_clipboard = current

                    except Exception as e:
                        logger.warning(f"[Clipboard Monitor] Error: {e}")

                    time.sleep(0.5)

            except Exception as e:
                logger.error(f"[Clipboard Monitor] Fatal error: {e}")

            logger.info("[Clipboard Monitor] Stopped")

        # Start daemon thread
        thread = threading.Thread(
            target=monitor_thread, daemon=True, name="ClipboardMonitor"
        )
        thread.start()

        logger.info("Clipboard monitor thread started")

    def _stop_clipboard_monitor(self) -> None:
        """Stop clipboard monitor thread."""
        self._clipboard_monitor_running = False

    # ========== Control methods (18 types - direct implementation) ==========

    # Core control methods

    def inject_keycode(
        self,
        keycode: int,
        action: int = AndroidKeyEventAction.DOWN,
        repeat: int = 0,
        metastate: int = 0,
    ) -> None:
        """Inject a keycode event."""
        msg = ControlMessage(ControlMessageType.INJECT_KEYCODE)
        msg.set_keycode(action, keycode, repeat, metastate)
        logger.info(
            f"-> Putting control message into queue: INJECT_KEYCODE, keycode={keycode}"
        )
        success = self._control_queue.put(msg)
        if not success:
            logger.warning(
                "Failed to put control message into queue (queue was full of non-droppable messages)"
            )

    def inject_text(self, text: str) -> None:
        """Inject text input."""
        msg = ControlMessage(ControlMessageType.INJECT_TEXT)
        msg.set_text(text)
        logger.info(f"-> Putting control message into queue: INJECT_TEXT, text={text[:50]}...")
        self._control_queue.put(msg)

    def inject_touch_event(
        self,
        action: int,
        pointer_id: int,
        position_x: int,
        position_y: int,
        screen_width: int,
        screen_height: int,
        pressure: float = 0.0,
    ) -> None:
        """Inject a touch event."""
        msg = ControlMessage(ControlMessageType.INJECT_TOUCH_EVENT)
        msg.set_touch_event(
            action,
            pointer_id,
            position_x,
            position_y,
            screen_width,
            screen_height,
            pressure,
        )
        # 解析 action 类型用于日志
        action_str = "DOWN/UP" if (action & AndroidMotionEventAction.DOWN and action & AndroidMotionEventAction.UP) else \
                   "DOWN" if action & AndroidMotionEventAction.DOWN else \
                   "UP" if action & AndroidMotionEventAction.UP else \
                   "MOVE" if action & AndroidMotionEventAction.MOVE else \
                   "CANCEL" if action & AndroidMotionEventAction.CANCEL else str(action)
        logger.info(
            f"-> Putting control message into queue: INJECT_TOUCH_EVENT, "
            f"action={action_str}, pos=({position_x}, {position_y}), pointer_id={pointer_id}"
        )
        success = self._control_queue.put(msg)
        if not success:
            logger.warning(
                "Failed to put touch control message into queue (queue was full of non-droppable messages)"
            )

    def inject_scroll_event(
        self,
        position_x: int,
        position_y: int,
        screen_width: int,
        screen_height: int,
        hscroll: float = 0.0,
        vscroll: float = 0.0,
    ) -> None:
        """Inject a scroll event."""
        msg = ControlMessage(ControlMessageType.INJECT_SCROLL_EVENT)
        msg.set_scroll_event(
            position_x, position_y, screen_width, screen_height, hscroll, vscroll
        )
        self._control_queue.put(msg)

    def back_or_screen_on(self, action: int = AndroidKeyEventAction.DOWN) -> None:
        """Press back or turn screen on."""
        msg = ControlMessage(ControlMessageType.BACK_OR_SCREEN_ON)
        msg.set_back_or_screen_on(action)
        self._control_queue.put(msg)

    def set_clipboard(self, text: str, paste: bool = False) -> None:
        """Set clipboard content."""
        msg = ControlMessage(ControlMessageType.SET_CLIPBOARD)
        msg.set_clipboard(self._clipboard_sequence, text, paste)
        logger.info(f"-> Putting control message into queue: SET_CLIPBOARD, text={text[:50]}..., paste={paste}")
        self._control_queue.put(msg)
        self._clipboard_sequence += 1

    # Convenience methods

    def tap(self, x: int, y: int) -> None:
        """Tap at screen coordinates."""
        width, height = self.state.device_size
        # 先发送 DOWN 事件
        self.inject_touch_event(
            AndroidMotionEventAction.DOWN,
            POINTER_ID_GENERIC_FINGER,
            x,
            y,
            width,
            height,
            1.0,  # pressure
        )
        # 立即发送 UP 事件
        self.inject_touch_event(
            AndroidMotionEventAction.UP,
            POINTER_ID_GENERIC_FINGER,
            x,
            y,
            width,
            height,
            0.0,  # pressure
        )

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        """Swipe from (x1,y1) to (x2,y2) over duration_ms."""
        width, height = self.state.device_size
        pointer_id = POINTER_ID_GENERIC_FINGER
        pressure = 1.0

        # DOWN event
        self.inject_touch_event(
            AndroidMotionEventAction.DOWN,
            pointer_id,
            x1,
            y1,
            width,
            height,
            pressure,
        )

        # MOVE events (simulate smooth swipe trajectory at ~15fps)
        steps = max(5, int(duration_ms / 60))  # At least 5 steps
        step_delay = duration_ms / 1000.0 / steps
        for i in range(1, steps + 1):
            progress = i / steps
            x = int(x1 + (x2 - x1) * progress)
            y = int(y1 + (y2 - y1) * progress)
            self.inject_touch_event(
                AndroidMotionEventAction.MOVE,
                pointer_id,
                x,
                y,
                width,
                height,
                pressure,
            )
            time.sleep(step_delay)

        # UP event
        pressure = 0.0
        self.inject_touch_event(
            AndroidMotionEventAction.UP,
            pointer_id,
            x2,
            y2,
            width,
            height,
            pressure,
        )

    def home(self) -> None:
        """Press home button."""
        self.inject_keycode(3, action=AndroidKeyEventAction.DOWN)  # KEYCODE_HOME
        self.inject_keycode(3, action=AndroidKeyEventAction.UP)

    def back(self) -> None:
        """Press back button."""
        self.inject_keycode(4, action=AndroidKeyEventAction.DOWN)  # KEYCODE_BACK
        self.inject_keycode(4, action=AndroidKeyEventAction.UP)

    def volume_up(self) -> None:
        """Press volume up button."""
        self.inject_keycode(KeyCode.VOLUME_UP, action=AndroidKeyEventAction.DOWN)
        self.inject_keycode(KeyCode.VOLUME_UP, action=AndroidKeyEventAction.UP)

    def volume_down(self) -> None:
        """Press volume down button."""
        self.inject_keycode(KeyCode.VOLUME_DOWN, action=AndroidKeyEventAction.DOWN)
        self.inject_keycode(KeyCode.VOLUME_DOWN, action=AndroidKeyEventAction.UP)

    def menu(self) -> None:
        """Press menu button."""
        self.inject_keycode(KeyCode.MENU, action=AndroidKeyEventAction.DOWN)
        self.inject_keycode(KeyCode.MENU, action=AndroidKeyEventAction.UP)

    def app_switch(self) -> None:
        """Press app switch button (show recent apps)."""
        self.inject_keycode(KeyCode.APP_SWITCH, action=AndroidKeyEventAction.DOWN)
        self.inject_keycode(KeyCode.APP_SWITCH, action=AndroidKeyEventAction.UP)

    def enter(self) -> None:
        """Press enter key."""
        self.inject_keycode(KeyCode.ENTER, action=AndroidKeyEventAction.DOWN)
        self.inject_keycode(KeyCode.ENTER, action=AndroidKeyEventAction.UP)

    def tab(self) -> None:
        """Press tab key."""
        self.inject_keycode(KeyCode.TAB, action=AndroidKeyEventAction.DOWN)
        self.inject_keycode(KeyCode.TAB, action=AndroidKeyEventAction.UP)

    def escape(self) -> None:
        """Press escape key."""
        self.inject_keycode(KeyCode.ESCAPE, action=AndroidKeyEventAction.DOWN)
        self.inject_keycode(KeyCode.ESCAPE, action=AndroidKeyEventAction.UP)

    def dpad_up(self) -> None:
        """Press D-pad up button."""
        self.inject_keycode(KeyCode.DPAD_UP, action=AndroidKeyEventAction.DOWN)
        self.inject_keycode(KeyCode.DPAD_UP, action=AndroidKeyEventAction.UP)

    def dpad_down(self) -> None:
        """Press D-pad down button."""
        self.inject_keycode(KeyCode.DPAD_DOWN, action=AndroidKeyEventAction.DOWN)
        self.inject_keycode(KeyCode.DPAD_DOWN, action=AndroidKeyEventAction.UP)

    def dpad_left(self) -> None:
        """Press D-pad left button."""
        self.inject_keycode(KeyCode.DPAD_LEFT, action=AndroidKeyEventAction.DOWN)
        self.inject_keycode(KeyCode.DPAD_LEFT, action=AndroidKeyEventAction.UP)

    def dpad_right(self) -> None:
        """Press D-pad right button."""
        self.inject_keycode(KeyCode.DPAD_RIGHT, action=AndroidKeyEventAction.DOWN)
        self.inject_keycode(KeyCode.DPAD_RIGHT, action=AndroidKeyEventAction.UP)

    def dpad_center(self) -> None:
        """Press D-pad center button."""
        self.inject_keycode(KeyCode.DPAD_CENTER, action=AndroidKeyEventAction.DOWN)
        self.inject_keycode(KeyCode.DPAD_CENTER, action=AndroidKeyEventAction.UP)

    # Panel control methods

    def expand_notification_panel(self) -> None:
        """Expand the notification panel (status bar)."""
        msg = ControlMessage(ControlMessageType.EXPAND_NOTIFICATION_PANEL)
        msg.set_expand_notification_panel()
        self._control_queue.put(msg)

    def expand_settings_panel(self) -> None:
        """Expand the settings panel (quick settings)."""
        msg = ControlMessage(ControlMessageType.EXPAND_SETTINGS_PANEL)
        msg.set_expand_settings_panel()
        self._control_queue.put(msg)

    def collapse_panels(self) -> None:
        """Collapse all expanded panels."""
        msg = ControlMessage(ControlMessageType.COLLAPSE_PANELS)
        msg.set_collapse_panels()
        self._control_queue.put(msg)

    # Display control methods

    def set_display_power(self, on: bool = True) -> None:
        """Turn display on or off."""
        msg = ControlMessage(ControlMessageType.SET_DISPLAY_POWER)
        msg.set_display_power(on)
        self._control_queue.put(msg)

    def turn_screen_on(self) -> None:
        """Turn screen on."""
        self.set_display_power(True)

    def turn_screen_off(self) -> None:
        """Turn screen off."""
        self.set_display_power(False)

    # ========== Runtime Video Control ==========
    def enable_video(self) -> None:
        """
        Enable video decoding (restore consumption).

        This method is called when video is enabled at runtime.
        """
        if not self._video_enabled:
            self._video_enabled = True
            if self._video_demuxer:
                self._video_demuxer.resume()
            if self._video_decoder:
                self._video_decoder.resume()
            logger.info("Video enabled")

    def disable_video(self) -> None:
        """
        Disable video decoding (save CPU).

        This method is called when video is disabled at runtime.
        """
        if self._video_enabled:
            self._video_enabled = False
            if self._video_demuxer:
                self._video_demuxer.pause()
            if self._video_decoder:
                self._video_decoder.pause()
            logger.info("Video disabled")

    # ========== Runtime Audio Control ==========
    def enable_audio(self) -> None:
        """
        Enable audio decoding and playback.

        This method is called when audio is enabled at runtime.
        """
        if not self._audio_enabled:
            self._audio_enabled = True
            if self._audio_demuxer:
                self._audio_demuxer.resume()
            if self._audio_decoder:
                self._audio_decoder.resume()
            if self._audio_player:
                self._audio_player.start()
            logger.info("Audio enabled")

    def disable_audio(self) -> None:
        """
        Disable audio decoding and playback.

        This method is called when audio is disabled at runtime.
        """
        if self._audio_enabled:
            self._audio_enabled = False
            if self._audio_demuxer:
                self._audio_demuxer.pause()
            if self._audio_decoder:
                self._audio_decoder.pause()
            if self._audio_player:
                self._audio_player.stop()
            logger.info("Audio disabled")

    # Device control methods

    def rotate_device(self) -> None:
        """Rotate device portrait/landscape."""
        msg = ControlMessage(ControlMessageType.ROTATE_DEVICE)
        msg.set_rotate_device()
        self._control_queue.put(msg)

    def open_hard_keyboard_settings(self) -> None:
        """Open hard keyboard settings (physical keyboard settings)."""
        msg = ControlMessage(ControlMessageType.OPEN_HARD_KEYBOARD_SETTINGS)
        msg.set_open_hard_keyboard_settings()
        self._control_queue.put(msg)

    def list_apps(self, timeout: float = 30.0) -> List[Dict[str, Any]]:
        """
        Get list of installed applications from device.

        When connected (mirroring session active), this method uses a control
        message to request the app list from the server, which works without
        file pushing and supports wireless ADB.

        When not connected (standalone usage), falls back to ADB-based method
        which pushes the server file temporarily.

        Args:
            timeout: Maximum time to wait for response when connected (default: 30s)

        Returns:
            List[dict]: [{"name": "Firefox", "package": "org.mozilla.firefox", "system": False}, ...]

        Raises:
            RuntimeError: If no ADB device is found (standalone mode)
            TimeoutError: If no response within timeout period (connected mode)

        Example:
            >>> # Can be called standalone (uses ADB file push)
            >>> apps = client.list_apps()
            >>>
            >>> # Or after client.connect() (uses control message, no file push!)
            >>> client.connect()
            >>> apps = client.list_apps()
            >>>
            >>> # Filter for user apps only
            >>> user_apps = [app for app in apps if not app["system"]]
        """
        # Preferred method: Use control message when connected
        # This works without file push and supports wireless ADB!
        if self.state.connected and self._device_receiver is not None:
            return self._list_apps_via_control_message(timeout)

        # Fallback: Use ADB-based method (standalone usage)
        return self._list_apps_via_adb()

    def _list_apps_via_control_message(self, timeout: float) -> List[Dict[str, Any]]:
        """
        Get app list via control message (requires active connection).

        This is the preferred method as it:
        - Works without file pushing
        - Supports wireless ADB (even without tcpip mode)
        - Uses the already-running server

        Args:
            timeout: Maximum time to wait for response

        Returns:
            List of app dictionaries

        Raises:
            TimeoutError: If no response within timeout period
        """
        # Use threading.Event to wait for response
        result_holder = []
        event = threading.Event()

        def on_app_list(apps):
            result_holder.append(apps)
            event.set()

        # Register temporary callback
        old_callback = self._device_receiver._callbacks.on_app_list
        self._device_receiver._callbacks.on_app_list = on_app_list

        try:
            # Send control message
            msg = ControlMessage(ControlMessageType.GET_APP_LIST)
            self._control_queue.put(msg)
            logger.info("[list_apps] Sent GET_APP_LIST control message")

            # Wait for response
            if not event.wait(timeout=timeout):
                raise TimeoutError(
                    f"No app list response received within {timeout} seconds"
                )

            apps = result_holder[0]
            logger.info(f"[list_apps] Received {len(apps)} apps")
            return apps

        finally:
            # Restore original callback
            self._device_receiver._callbacks.on_app_list = old_callback

    def _list_apps_via_adb(self) -> List[Dict[str, Any]]:
        """
        Get app list via ADB (fallback for standalone usage).

        This method pushes the server file temporarily to query apps.
        It requires USB connection or wireless ADB with tcpip mode.

        Returns:
            List of app dictionaries

        Raises:
            RuntimeError: If no ADB device is found
        """
        # Create ADB manager
        adb = ADBManager(timeout=60.0)
        from scrcpy_py_ddlx.core.adb import ADBDeviceType

        # If client is connected, use the existing device serial
        if self.state.connected and self.state.device_serial:
            return adb.list_apps(serial=self.state.device_serial)

        # Otherwise, select device (standalone usage)
        all_devices = adb.list_devices()
        if not all_devices:
            raise RuntimeError("No ADB devices found")

        usb_devices = [d for d in all_devices if d.device_type == ADBDeviceType.USB]
        tcpip_devices = [d for d in all_devices if d.device_type == ADBDeviceType.TCPIP]

        device = usb_devices[0] if usb_devices else tcpip_devices[0]
        serial = device.serial

        return adb.list_apps(serial=serial)

    def start_app(self, app_name: str) -> None:
        """Start an application by name."""
        msg = ControlMessage(ControlMessageType.START_APP)
        msg.set_start_app(app_name)
        self._control_queue.put(msg)

    def reset_video(self) -> None:
        """Reset video stream (useful if video freezes)."""
        msg = ControlMessage(ControlMessageType.RESET_VIDEO)
        msg.set_reset_video()
        self._control_queue.put(msg)

    def get_clipboard(self, copy_key: int = CopyKey.COPY) -> None:
        """Request clipboard content from device."""
        msg = ControlMessage(ControlMessageType.GET_CLIPBOARD)
        msg.set_copy_key(copy_key)
        self._control_queue.put(msg)

    def sync_clipboard_to_device(self, paste: bool = True) -> bool:
        """
        Sync PC clipboard to device (PC → Device).

        This is a window-independent method that sends control messages directly.
        It gets the current PC clipboard content and sends it to the device,
        then optionally injects Ctrl+v to paste.

        Based on official scrcpy PC→Device clipboard mechanism:
        1. Get PC clipboard content
        2. Send SET_CLIPBOARD control message
        3. If paste=True, inject Ctrl+v key event

        Args:
            paste: If True, inject Ctrl+v after setting clipboard (default: True)

        Returns:
            True if successful, False otherwise

        Example:
            >>> client.sync_clipboard_to_device()  # Sync and paste
            >>> client.sync_clipboard_to_device(paste=False)  # Sync only, don't paste
        """
        try:
            # Get PC clipboard content (prefer Windows API)
            text = None
            try:
                import win32clipboard

                win32clipboard.OpenClipboard()
                try:
                    text = win32clipboard.GetClipboardText()
                finally:
                    win32clipboard.CloseClipboard()
                logger.debug(
                    f"[PC → Device] Got clipboard from win32clipboard: {text[:50] if text else 'None'}..."
                )
            except ImportError:
                # Fallback to pyperclip on non-Windows platforms
                try:
                    import pyperclip

                    text = pyperclip.paste()
                    logger.debug(
                        f"[PC → Device] Got clipboard from pyperclip: {text[:50] if text else 'None'}..."
                    )
                except ImportError:
                    logger.error(
                        "[PC → Device] Neither win32clipboard nor pyperclip available"
                    )
                    return False

            if not text:
                logger.warning("[PC → Device] PC clipboard is empty, nothing to sync")
                return False

            # Send SET_CLIPBOARD control message to device
            # scrcpy 协议的 paste 参数可以自动触发粘贴
            if paste:
                self.set_clipboard(text, paste=True)
                # 等待剪贴板同步和粘贴完成
                import time
                time.sleep(0.2)
            else:
                self.set_clipboard(text, paste=False)

            logger.info(f"[PC → Device] Clipboard synced: {text[:50]}...")
            return True

        except Exception as e:
            logger.error(f"[PC → Device] Failed to sync clipboard: {e}")
            import traceback

            logger.debug(traceback.format_exc())
            return False

    # ========== Screenshot methods ==========

    def _save_frame_async(self, frame: "np.ndarray", filename: str) -> None:
        """
        Save a frame to a file asynchronously (non-blocking).

        Uses a background thread to avoid blocking the Qt event loop.

        Args:
            frame: RGB numpy array from video decoder (VideoDecoder returns RGB)
            filename: Output filename
        """
        import threading
        import time

        def save_in_background():
            try:
                import time

                start = time.time()

                # Use OpenCV
                import cv2

                # Convert RGB to BGR for OpenCV (decoder returns RGB, cv2.imwrite expects BGR)
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

                # Determine format from extension
                if filename.lower().endswith(".jpg") or filename.lower().endswith(
                    ".jpeg"
                ):
                    # JPEG is much faster than PNG!
                    # Quality 95 is high quality but faster than 100
                    cv2.imwrite(filename, frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
                else:
                    # Default to PNG for quality
                    cv2.imwrite(filename, frame_bgr)

                elapsed = time.time() - start
                logger.info(f"Screenshot saved: {filename} ({elapsed * 1000:.1f}ms)")
            except ImportError:
                # Fallback to Pillow (PIL expects RGB, which is what we have)
                try:
                    from PIL import Image
                    import numpy as np

                    img = Image.fromarray(frame)  # frame is already RGB
                    img.save(filename)
                    logger.info(f"Screenshot saved: {filename} (via Pillow)")
                except ImportError:
                    logger.error("Cannot save: neither cv2 nor PIL available")

        # Start background thread (daemon so it doesn't prevent exit)
        thread = threading.Thread(target=save_in_background, daemon=True)
        thread.start()

    def screenshot(
        self, filename: Optional[str] = None, timeout: float = 1.0
    ) -> Optional["np.ndarray"]:
        """
        Capture current frame from the video stream (fast, no server communication).

        This method retrieves the most recent frame from the video decoder's buffer.
        It does NOT request a new frame from the server - it just returns what's
        currently being displayed.

        LAZY DECODE MODE (default):
        - If video is paused (lazy mode), it auto-resumes, waits for frames, captures, then auto-pauses
        - This saves CPU when not actively taking screenshots

        IMPORTANT: This method is NON-BLOCKING and safe to call from Qt event loop.
        It uses get_frame_nowait() to avoid blocking the UI thread.
        File saving is done asynchronously in a background thread.
        Rate limited to ~3 screenshots per second to avoid performance degradation.

        Args:
            filename: If provided, save the screenshot to this file (e.g., "screenshot.png")
                      Saving happens asynchronously, so this call returns immediately.
                      Use .jpg extension for faster encoding (recommended).
            timeout: DEPRECATED (kept for compatibility), not used in non-blocking mode

        Returns:
            numpy array (BGR format) with shape (height, width, 3), or None if no frame available

        Example:
            >>> # Get frame as numpy array (non-blocking)
            >>> frame = client.screenshot()
            >>> if frame is not None:
            ...     print(f"Frame shape: {frame.shape}")  # (height, width, 3)
            >>>
            >>> # Save directly to file (async, non-blocking!)
            >>> client.screenshot("screenshot.png")
            >>>
            >>> # Use JPEG for faster encoding (recommended)
            >>> client.screenshot("screenshot.jpg")
        """
        import time

        if self._video_decoder is None:
            logger.warning("Video decoder not available, cannot take screenshot")
            return None

        # ========== LAZY DECODE: Auto-resume if paused ==========
        was_paused = not self._video_enabled
        effective_lazy = self.config.lazy_decode and not self.config.show_window
        if was_paused and effective_lazy:
            logger.info("Screenshot: auto-resuming video for capture")
            self.enable_video()
            # Wait a bit for frames to arrive
            time.sleep(0.2)

        # Rate limiting: check if enough time has passed since last screenshot
        current_time = time.time()
        time_since_last = current_time - self._screenshot_last_time

        if time_since_last < self._screenshot_min_interval:
            # Rate limited: skip saving but still return the frame
            logger.debug(
                f"Screenshot rate limited: {time_since_last * 1000:.0f}ms since last"
            )
            frame = self._video_decoder.get_frame_nowait()
            if frame is not None and filename:
                logger.debug(f"Screenshot '{filename}' skipped due to rate limit")
            return frame

        # Update last screenshot time
        self._screenshot_last_time = current_time

        # Use get_frame_nowait() to avoid blocking the Qt event loop!
        frame = self._video_decoder.get_frame_nowait()
        if frame is not None and filename:
            # Save asynchronously in background thread (non-blocking!)
            self._save_frame_async(frame, filename)

        # ========== LAZY DECODE: Auto-pause after capture ==========
        if was_paused and effective_lazy:
            logger.info("Screenshot: auto-pausing video (lazy mode)")
            self.disable_video()

        return frame

    def screenshot_device(
        self, filename: Optional[str] = None, timeout: float = 5.0
    ) -> Optional["np.ndarray"]:
        """
        Request a fresh screenshot from the device (requires server support).

        This method sends a SCREENSHOT control message to the server, which should
        respond with a single video frame. The client then decodes this frame.

        NOTE: This requires server-side support for TYPE_SCREENSHOT control message.
        If the server doesn't support it, this method will timeout.

        Args:
            filename: If provided, save the screenshot to this file (async, non-blocking)
            timeout: Maximum time to wait for screenshot frame (seconds)

        Returns:
            numpy array (BGR format) with shape (height, width, 3), or None if failed

        Example:
            >>> # Request fresh screenshot from device
            >>> frame = client.screenshot_device("device.png")
        """
        if not self.state.connected:
            logger.warning("Not connected, cannot request device screenshot")
            return None

        # Send screenshot control message
        msg = ControlMessage(ControlMessageType.SCREENSHOT)
        logger.info("Requesting screenshot from device...")
        self._control_queue.put(msg)

        # Try to get the next frame (which should be the screenshot response)
        # Note: get_frame() blocks, but this is expected for device screenshot
        if self._video_decoder is None:
            logger.warning("Video decoder not available")
            return None

        frame = self._video_decoder.get_frame(timeout=timeout)
        if frame is not None:
            logger.info("Screenshot received from device")
            if filename:
                # Save asynchronously (non-blocking!)
                self._save_frame_async(frame, filename)
        else:
            logger.warning("No frame received (server may not support SCREENSHOT)")

        return frame

    def screenshot_standalone(
        self, filename: Optional[str] = None, timeout: float = 10.0
    ) -> Optional["np.ndarray"]:
        """
        Take a screenshot by establishing a temporary video connection (no server modification needed).

        This method works even when the client is NOT connected:
        - If already connected with video, reuses existing connection
        - If NOT connected, establishes temporary connection, captures one frame, then disconnects

        This is ideal for scenarios where you only need screenshots without the video stream.

        Connection flow (temporary mode):
        1. Start server via ADB
        2. Create forward tunnel (with random port to avoid conflicts)
        3. Connect to video socket
        4. Read device metadata (name, codec, resolution)
        5. Wait for first key frame
        6. Decode and save screenshot
        7. Disconnect and cleanup

        Performance:
        - Cold start (server not running): ~500-1500ms (ADB + server startup)
        - Warm start (server already running): ~200-500ms (connection + first frame)

        Args:
            filename: Save path (if provided)
            timeout: Maximum wait time for first frame (seconds), default 10s

        Returns:
            numpy array (BGR format) with shape (height, width, 3), or None if failed

        Example:
            >>> # Client not connected - will establish temporary connection
            >>> client = ScrcpyClient(ClientConfig(show_window=False))
            >>> frame = client.screenshot_standalone("screenshot.jpg")
            >>> # Connection automatically closed after screenshot
        """
        import socket
        import time
        import random

        was_connected = self.state.connected
        temp_socket = None
        temp_decoder = None
        temp_demuxer = None
        adb = None
        local_port = None
        device_serial = None

        try:
            # If already connected with video enabled, reuse existing connection
            # But if video is paused (lazy_decode mode), establish temporary connection
            if was_connected and self._video_decoder is not None and self._video_enabled:
                logger.info("Using existing video connection for screenshot")
                frame = self._video_decoder.get_frame(timeout=timeout)
                if frame is not None and filename:
                    self._save_frame_async(frame, filename)
                return frame

            # Need to establish temporary connection
            logger.info("Establishing temporary video connection for screenshot...")

            # Import ADB manager
            adb = ADBManager(timeout=timeout)
            from scrcpy_py_ddlx.core.adb import ADBDeviceType

            # Select device
            all_devices = adb.list_devices()
            if not all_devices:
                logger.error("No ADB devices found")
                return None

            usb_devices = [d for d in all_devices if d.device_type == ADBDeviceType.USB]
            tcpip_devices = [
                d for d in all_devices if d.device_type == ADBDeviceType.TCPIP
            ]

            device = usb_devices[0] if usb_devices else tcpip_devices[0]
            device_serial = device.serial

            # Push server (this is quick if already running)
            adb.push_server(device_serial, "scrcpy-server")
            logger.info("Server ready (or already running)")

            # Generate random SCID and LOCAL PORT to avoid conflicts
            scid = random.randint(0, 0x7FFFFFFF)
            socket_name = f"scrcpy_{scid:08x}"

            # Use RANDOM local port to avoid conflicts with port 27183
            local_port = random.randint(30000, 40000)
            logger.info(f"Using dynamic local port: {local_port}")

            # Create forward tunnel
            adb._execute(
                ["forward", f"tcp:{local_port}", f"localabstract:{socket_name}"],
                device_serial=device_serial,
                timeout=5.0,
            )
            logger.info(f"Forward tunnel created: tcp:{local_port} -> {socket_name}")

            # Start server in background
            server_params = f"scid={scid:08x} tunnel_forward=true audio=false control=true log_level=info video_bit_rate={self.config.bitrate} max_fps={self.config.max_fps}"
            adb.start_server(
                serial=device_serial,
                client_version="3.3.4",
                server_params=server_params,
                timeout=timeout,
                background=True,
            )

            # Wait for server to be ready
            logger.info("Waiting for server to initialize...")
            time.sleep(1.0)

            # Connect to video socket
            temp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            temp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            max_retries = 100  # Increased from 50
            connected = False

            for attempt in range(max_retries):
                try:
                    logger.debug(f"Connection attempt {attempt + 1}/{max_retries}")
                    temp_socket.connect(("127.0.0.1", local_port))
                    # Read dummy byte (use configurable timeout)
                    temp_socket.settimeout(timeout)
                    dummy = temp_socket.recv(1)
                    if len(dummy) == 0:
                        raise ConnectionError("No dummy byte received")
                    logger.info(f"✓ Connected to server (attempt {attempt + 1})")
                    connected = True
                    break
                except Exception as e:
                    logger.debug(f"Connection attempt {attempt + 1} failed: {e}")
                    temp_socket.close()
                    if attempt < max_retries - 1:
                        time.sleep(0.05)  # Faster retry (50ms instead of 100ms)
                    else:
                        raise ConnectionError(
                            f"Failed to connect after {max_retries} attempts"
                        )

            if not connected:
                raise ConnectionError("Failed to connect to server")

            # Connect audio and control sockets (server expects all 3 connections before sending metadata)
            # Note: We don't use these sockets, but server requires them
            logger.info("Connecting additional sockets (audio, control)...")

            # Audio socket
            try:
                audio_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                audio_socket.settimeout(timeout)
                audio_socket.connect(("127.0.0.1", local_port))
                audio_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                logger.debug("Audio socket connected (not used in standalone mode)")
            except Exception as e:
                logger.warning(
                    f"Audio socket connection failed (continuing anyway): {e}"
                )
                audio_socket = None

            # Control socket
            try:
                control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                control_socket.settimeout(timeout)
                control_socket.connect(("127.0.0.1", local_port))
                control_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                logger.debug("Control socket connected (not used in standalone mode)")
            except Exception as e:
                logger.warning(
                    f"Control socket connection failed (continuing anyway): {e}"
                )
                control_socket = None

            # Now read device metadata (server should send it after all sockets connected)
            logger.info("Reading device metadata...")

            # Device name (64 bytes)
            device_name_bytes = b""
            while len(device_name_bytes) < 64:
                chunk = temp_socket.recv(64 - len(device_name_bytes))
                if len(chunk) == 0:
                    raise ConnectionError("Connection closed while reading device name")
                device_name_bytes += chunk

            # Codec ID (4 bytes)
            codec_id_bytes = b""
            while len(codec_id_bytes) < 4:
                chunk = temp_socket.recv(4 - len(codec_id_bytes))
                if len(chunk) == 0:
                    raise ConnectionError("Connection closed while reading codec ID")
                codec_id_bytes += chunk
            codec_id = int.from_bytes(codec_id_bytes, byteorder="big")

            # Video size (8 bytes: width + height)
            size_bytes = b""
            while len(size_bytes) < 8:
                chunk = temp_socket.recv(8 - len(size_bytes))
                if len(chunk) == 0:
                    raise ConnectionError("Connection closed while reading video size")
                size_bytes += chunk
            width = int.from_bytes(size_bytes[:4], byteorder="big")
            height = int.from_bytes(size_bytes[4:8], byteorder="big")

            logger.info(
                f"Device: {device_name_bytes.rstrip(b'\\x00').decode('utf-8', errors='ignore').strip()}"
            )
            logger.info(f"Resolution: {width}x{height}, Codec: 0x{codec_id:08x}")

            # Create temporary decoder and demuxer
            from scrcpy_py_ddlx.core.demuxer import StreamingVideoDemuxer
            from scrcpy_py_ddlx.core.decoder import VideoDecoder
            from queue import Queue

            # Create packet queue for demuxer
            packet_queue = Queue(maxsize=3)

            temp_demuxer = StreamingVideoDemuxer(temp_socket, packet_queue, codec_id)
            temp_decoder = VideoDecoder(
                width=height, height=width, codec_id=codec_id, packet_queue=packet_queue
            )  # Scrcpy uses rotated

            # Start demuxer to begin reading frames
            temp_demuxer.start()
            temp_decoder.start()  # Start decoder thread

            # Wait for first frame
            logger.info(f"Waiting for first frame (timeout={timeout}s)...")
            frame = temp_decoder.get_frame(timeout=timeout)

            if frame is not None:
                logger.info(f"✓ Frame captured: {frame.shape}")
                if filename:
                    # Save synchronously for this standalone method
                    import cv2

                    start = time.time()
                    # Convert RGB to BGR for OpenCV (decoder returns RGB, cv2.imwrite expects BGR)
                    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    if filename.lower().endswith(".jpg") or filename.lower().endswith(
                        ".jpeg"
                    ):
                        cv2.imwrite(filename, frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
                    else:
                        cv2.imwrite(filename, frame_bgr)
                    elapsed = time.time() - start
                    logger.info(
                        f"✓ Screenshot saved: {filename} ({elapsed * 1000:.1f}ms)"
                    )
            else:
                logger.error("✗ Failed to capture frame (timeout)")

            return frame

        except Exception as e:
            logger.error(f"Standalone screenshot failed: {e}")
            import traceback

            traceback.print_exc()
            return None

        finally:
            # Cleanup temporary resources
            logger.info("Cleaning up temporary connection...")

            # Step 1: Close sockets FIRST (this will cause demuxer recv() to fail/return, stopping the thread)
            if temp_socket is not None:
                try:
                    temp_socket.close()
                except:
                    pass
                temp_socket = None

            # Close audio and control sockets
            if "audio_socket" in locals() and audio_socket is not None:
                try:
                    audio_socket.close()
                except:
                    pass

            if "control_socket" in locals() and control_socket is not None:
                try:
                    control_socket.close()
                except:
                    pass

            # Step 2: Stop demuxer (will exit quickly since socket is closed)
            if temp_demuxer is not None:
                try:
                    temp_demuxer.stop()
                except:
                    pass
                temp_demuxer = None

            # Step 3: Stop decoder
            if temp_decoder is not None:
                try:
                    temp_decoder.stop()
                except:
                    pass
                temp_decoder = None

            # Remove forward tunnel
            if device_serial and local_port:
                try:
                    adb._execute(
                        ["forward", "--remove", f"tcp:{local_port}"],
                        device_serial=device_serial,
                        timeout=2.0,
                        capture_output=False,
                    )
                    logger.info(f"Forward tunnel removed: tcp:{local_port}")
                except Exception as e:
                    logger.debug(f"Failed to remove forward tunnel: {e}")

            # If we were not connected before, don't keep anything
            if not was_connected:
                logger.info("Temporary connection closed (client remains disconnected)")

    # ========== Audio Recording ==========

    def start_audio_recording(
        self,
        filename: str,
        max_duration: Optional[float] = None,
        play_while_recording: bool = True,
        auto_convert_to: Optional[str] = None,
    ) -> bool:
        """
        Start recording audio from the device.

        Records audio to a WAV file (float32 IEEE format). The audio can be played
        through the output while recording (default), or recorded silently.

        LAZY DECODE MODE (default):
        - If audio is paused (lazy mode), it auto-resumes for recording
        - Audio stays enabled while recording
        - Call stop_audio_recording() to auto-pause when done

        This implementation has zero performance impact during recording because
        it simply copies the decoded audio data that is already being produced
        for playback.

        Args:
            filename: Output filename (e.g., "recording.wav" or "recording.opus")
            max_duration: Maximum recording duration in seconds (None for unlimited)
            play_while_recording: If True, play audio while recording (default)
            auto_convert_to: Target format for auto-conversion ('opus', 'mp3'). Default None.
                           If filename ends with .opus or .mp3, auto-converts to that format.

        Returns:
            True if recording started successfully

        Example:
            >>> # Record as Opus
            >>> client.start_audio_recording("voice.opus", max_duration=10)
            >>> time.sleep(10)
            >>> client.stop_audio_recording()

            >>> # Record as MP3
            >>> client.start_audio_recording("voice.mp3", auto_convert_to='mp3')
        """
        if not self.state.connected:
            logger.warning("Cannot start audio recording: not connected")
            return False

        if self._audio_decoder is None:
            logger.warning("Cannot start audio recording: audio decoder not available")
            return False

        # ========== LAZY DECODE: Auto-resume if paused ==========
        effective_lazy = self.config.lazy_decode and not self.config.show_window
        self._audio_was_paused_before_recording = not self._audio_enabled
        if self._audio_was_paused_before_recording and effective_lazy:
            logger.info("Audio recording: auto-resuming audio for capture")
            self.enable_audio()
            # Wait for audio stream to stabilize (increased from 0.1s)
            import time
            time.sleep(0.5)

        # Check if already recording OR cleanup old finished recorder
        if hasattr(self, "_audio_recorder") and self._audio_recorder is not None:
            if self._audio_recorder.is_recording():
                logger.warning("Audio recording already in progress")
                return False
            else:
                # Old recorder exists but is stopped - cleanup first
                # This prevents file append issues when reusing the same filename
                logger.info("Cleaning up old audio recorder before starting new one")

                # CRITICAL: Also restore frame_sink if it's still a TeeAudioRecorder
                # This can happen if stop_audio_recording() wasn't called properly
                if hasattr(self, "_original_audio_player") and self._original_audio_player is not None:
                    from scrcpy_py_ddlx.core.audio.recorder import TeeAudioRecorder
                    if isinstance(self._audio_decoder._frame_sink, TeeAudioRecorder):
                        logger.info("Cleaning up: restoring frame_sink from old TeeAudioRecorder")
                        self._audio_decoder._frame_sink = self._original_audio_player
                    self._original_audio_player = None

                try:
                    self._audio_recorder.close()
                except Exception as e:
                    logger.debug(f"Error closing old recorder: {e}")
                self._audio_recorder = None

        from scrcpy_py_ddlx.core.audio.recorder import AudioRecorder, TeeAudioRecorder

        # Create recorder with auto_convert_to parameter
        recorder = AudioRecorder(
            filename, max_duration=max_duration, auto_convert_to=auto_convert_to
        )

        # Get current player
        current_player = self._audio_decoder._frame_sink

        if play_while_recording and current_player is not None:
            # Use tee to duplicate audio to both player and recorder
            tee_recorder = TeeAudioRecorder(recorder, current_player)
            self._audio_decoder._frame_sink = tee_recorder

            # Open both recorder and player (player already open, just open recorder)
            recorder.open(
                codec_context=self._audio_decoder,
                sample_rate=self._audio_decoder._sample_rate,
                channels=self._audio_decoder._channels,
            )
        else:
            # Replace player with recorder
            self._audio_decoder._frame_sink = recorder
            if not recorder.open(
                codec_context=self._audio_decoder,
                sample_rate=self._audio_decoder._sample_rate,
                channels=self._audio_decoder._channels,
            ):
                logger.error("Failed to start audio recorder")
                return False

        self._audio_recorder = recorder
        self._original_audio_player = current_player

        logger.info(f"Audio recording started: {filename}")
        if max_duration:
            logger.info(f"  Max duration: {max_duration} seconds")
        if not play_while_recording:
            logger.info(f"  Mode: Silent recording (no playback)")

        return True

    def stop_audio_recording(self) -> Optional[str]:
        """
        Stop audio recording and save the file.

        LAZY DECODE MODE (default):
        - If audio was auto-resumed for recording, it auto-pauses after recording stops

        Returns:
            Filename if recording was stopped, None if no recording was active

        Example:
            >>> filename = client.stop_audio_recording()
            >>> print(f"Recording saved: {filename}")
        """
        # Check lazy decode state BEFORE checking if recording stopped
        # This ensures auto-pause works even when recording stopped automatically (max_duration)
        effective_lazy = self.config.lazy_decode and not self.config.show_window
        was_auto_resumed = hasattr(self, "_audio_was_paused_before_recording") and self._audio_was_paused_before_recording

        if not hasattr(self, "_audio_recorder") or self._audio_recorder is None:
            logger.warning("No audio recording in progress")
            # Still auto-pause if needed
            if was_auto_resumed and effective_lazy:
                logger.info("Audio recording: auto-pausing audio (lazy mode, no recorder)")
                self.disable_audio()
            return None

        if not self._audio_recorder.is_recording():
            logger.warning("Audio recording already stopped")
            # IMPORTANT: Still auto-pause even if recording stopped automatically!
            filename = self._audio_recorder._filename if hasattr(self._audio_recorder, '_filename') else None

            # CRITICAL: Restore frame_sink even if recording auto-stopped!
            # Otherwise decoder continues pushing to old TeeAudioRecorder
            if (
                hasattr(self, "_original_audio_player")
                and self._original_audio_player is not None
            ):
                from scrcpy_py_ddlx.core.audio.recorder import TeeAudioRecorder
                if isinstance(self._audio_decoder._frame_sink, TeeAudioRecorder):
                    logger.info("Restoring frame_sink after auto-stopped recording")
                    self._audio_decoder._frame_sink = self._original_audio_player
                self._original_audio_player = None

            self._audio_recorder = None

            if was_auto_resumed and effective_lazy:
                logger.info("Audio recording: auto-pausing audio (lazy mode, auto-stopped)")
                self.disable_audio()
            return filename

        filename = self._audio_recorder._filename

        # Stop recording
        self._audio_recorder.close()

        # Restore original player if it was a tee setup
        if (
            hasattr(self, "_original_audio_player")
            and self._original_audio_player is not None
        ):
            # Check if current frame_sink is a TeeAudioRecorder
            from scrcpy_py_ddlx.core.audio.recorder import TeeAudioRecorder

            if isinstance(self._audio_decoder._frame_sink, TeeAudioRecorder):
                self._audio_decoder._frame_sink = self._original_audio_player
            self._original_audio_player = None

        self._audio_recorder = None

        logger.info(f"Audio recording stopped: {filename}")

        # ========== LAZY DECODE: Auto-pause after recording ==========
        effective_lazy = self.config.lazy_decode and not self.config.show_window
        if hasattr(self, "_audio_was_paused_before_recording") and self._audio_was_paused_before_recording and effective_lazy:
            logger.info("Audio recording: auto-pausing audio (lazy mode)")
            self.disable_audio()

        return filename

    def record_audio(
        self, filename: str, duration: float, play_while_recording: bool = True
    ) -> bool:
        """
        Record audio for a specific duration.

        This is a convenience method that combines start_audio_recording(),
        wait, and stop_audio_recording().

        Args:
            filename: Output filename (e.g., "recording.wav")
            duration: Recording duration in seconds
            play_while_recording: If True, play audio while recording (default)

        Returns:
            True if recording completed successfully

        Example:
            >>> # Record 5 seconds of audio
            >>> client.record_audio("voice.wav", duration=5.0)
        """
        import time

        if not self.start_audio_recording(
            filename, max_duration=duration, play_while_recording=play_while_recording
        ):
            return False

        # Wait for recording duration
        try:
            time.sleep(duration)
        except KeyboardInterrupt:
            logger.info("Recording interrupted by user")

        return self.stop_audio_recording() is not None

    def is_recording_audio(self) -> bool:
        """
        Check if audio recording is currently active.

        Returns:
            True if recording audio
        """
        if not hasattr(self, "_audio_recorder") or self._audio_recorder is None:
            return False
        return self._audio_recorder.is_recording()

    def get_recording_duration(self) -> float:
        """
        Get the current recording duration in seconds.

        Returns:
            Recording duration in seconds, 0.0 if not recording
        """
        if not self.is_recording_audio():
            return 0.0
        return self._audio_recorder.get_duration()

    # ========== Opus Recording (simplified - uses existing AudioRecorder) ==========

    def start_opus_recording(self, filename: str) -> bool:
        """
        Start recording audio and save as OGG Opus file.

        This records decoded audio (zero CPU overhead during recording)
        and converts to Opus format using FFmpeg after recording stops.

        Args:
            filename: Output filename (e.g., "recording.opus")

        Returns:
            True if recording started successfully

        Example:
            >>> client.start_opus_recording("voice.opus")
            >>> time.sleep(10)  # Record for 10 seconds
            >>> filename = client.stop_opus_recording()
            >>> print(f"Saved: {filename}")
        """
        # Use existing start_audio_recording with auto-convert to Opus
        return self.start_audio_recording(filename, auto_convert_to="opus")

    def stop_opus_recording(self) -> Optional[str]:
        """
        Stop Opus recording and save the file.

        Returns:
            Final filename if successful, None otherwise

        Example:
            >>> filename = client.stop_opus_recording()
            >>> print(f"Recording saved: {filename}")
        """
        # Use existing stop_audio_recording
        return self.stop_audio_recording()

    def is_recording_opus(self) -> bool:
        """
        Check if Opus recording is currently active.

        Returns:
            True if recording
        """
        return self.is_recording_audio()

    # ========== Context manager ==========

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()

    # ========== Qt integration ==========

    def run_with_qt(self) -> None:
        """
        Run the Qt event loop while the client is connected.

        This method starts the Qt event loop and keeps it running until:
        - The client disconnects
        - The video window is closed
        - An error occurs

        This is the recommended way to use the client with the video window.
        The Qt event loop processes paint events and user input.

        Example:
            client = ScrcpyClient(show_window=True)
            if client.connect():
                client.run_with_qt()  # Blocks until window closed or disconnected

        Note:
            This method blocks until the Qt event loop exits.
            The client continues running in background threads.
        """
        if not self.state.connected:
            logger.error("Cannot run Qt event loop: client not connected")
            return

        if self._video_window is None:
            logger.warning("No video window available, use wait() instead")
            # Fall back to simple wait
            self.wait()
            return

        try:
            from PySide6.QtCore import QTimer, QCoreApplication
            from PySide6.QtWidgets import QApplication
        except ImportError:
            logger.error("PySide6 not available, cannot run Qt event loop")
            self.wait()
            return

        app = QApplication.instance()
        if app is None:
            logger.error("QApplication not initialized. Video window should create it.")
            self.wait()
            return

        logger.info("Starting Qt event loop...")

        # Create a timer to check if client is still running
        # This allows Qt to process events while checking connection status
        check_timer = QTimer()
        check_timer.setInterval(100)  # Check every 100ms

        # Track if window has had time to show (show() is async in Qt)
        _window_shown_time = None
        _check_count = 0
        _demuxer_dead_notified = False  # Track if we've notified about demuxer death

        def check_connection():
            """Check if client is still connected and window is still open."""
            nonlocal _window_shown_time, _check_count, _demuxer_dead_notified
            _check_count += 1

            # Initialize shown time on first check
            if _window_shown_time is None:
                _window_shown_time = time.time()
                # Don't check window visibility on first iteration - give Qt time to show it
                logger.debug(
                    f"Check #{_check_count}: Initial check, giving window time to show..."
                )
                return

            # Only check window visibility after 500ms have passed
            elapsed = time.time() - _window_shown_time
            if elapsed < 0.5:
                logger.debug(
                    f"Check #{_check_count}: Waiting for window (elapsed={elapsed:.2f}s)"
                )
                return

            # CRITICAL: Check if VideoDemuxer thread is still running
            # If demuxer dies but client is "connected", it means video socket closed unexpectedly
            if (
                self._video_demuxer is not None
                and self._video_demuxer._thread is not None
            ):
                if not self._video_demuxer._thread.is_alive() and self.state.connected:
                    if not _demuxer_dead_notified:
                        _demuxer_dead_notified = True
                        logger.error("=" * 60)
                        logger.error("VIDEO CONNECTION LOST!")
                        logger.error("VideoDemuxer thread has exited unexpectedly.")
                        logger.error("The video socket was closed by the server.")
                        logger.error(
                            "Control connection may still be active (touch/keyboard work)."
                        )
                        logger.error(
                            "The video will freeze, but you can still control the device."
                        )
                        logger.error("Close the window to exit.")
                        logger.error("=" * 60)
                        # Mark as disconnected but DON'T quit - let user decide when to close
                        self.state.connected = False
                    return

            # Log visibility status for debugging
            if self._video_window is not None:
                logger.debug(
                    f"Check #{_check_count}: window.visible={self._video_window.isVisible()}, "
                    f"connected={self.state.connected}, elapsed={elapsed:.2f}s"
                )

            # Only quit when window is closed (NOT when connection is lost)
            # This allows user to see error message and close manually
            if self._video_window is not None and not self._video_window.isVisible():
                logger.info(
                    f"Video window closed (check #{_check_count}, elapsed={elapsed:.2f}s), stopping Qt event loop"
                )
                QCoreApplication.quit()
                check_timer.stop()
            elif not self.state.connected and _demuxer_dead_notified:
                # Connection lost but window still open - just log, don't quit
                logger.debug(
                    "Video connection lost but window still open, waiting for user to close"
                )

        check_timer.timeout.connect(check_connection)
        check_timer.start()

        # Run Qt event loop (blocks until quit() is called)
        # Handle KeyboardInterrupt to allow clean exit with Ctrl+C
        try:
            app.exec()
        except KeyboardInterrupt:
            logger.info("Interrupted by user, stopping Qt event loop")
            QCoreApplication.quit()
            # Ensure timer is stopped
            check_timer.stop()

        logger.info("Qt event loop stopped")

    def wait(self) -> None:
        """
        Wait for the client to disconnect.

        This method blocks until the client is disconnected.
        Use this when you don't need the Qt event loop (e.g., no video window).

        Example:
            client = ScrcpyClient()
            if client.connect():
                client.wait()  # Blocks until disconnected
        """
        while self.state.connected:
            try:
                time.sleep(0.1)
            except KeyboardInterrupt:
                logger.info("Interrupted by user")
                break

    # ========== Properties ==========

    @property
    def device_name(self) -> str:
        """Get device name."""
        return self.state.device_name

    @property
    def device_size(self) -> tuple:
        """Get device screen size."""
        return self.state.device_size

    @property
    def is_connected(self) -> bool:
        """Check if connected."""
        return self.state.connected

    @property
    def is_running(self) -> bool:
        """Check if running."""
        return self.state.running


# Convenience function
def connect_to_device(
    host: str = "localhost", port: int = 27183, **kwargs
) -> ScrcpyClient:
    """
    Connect to a scrcpy device.

    Args:
        host: Server host
        port: Server port
        **kwargs: Additional config options

    Returns:
        Connected ScrcpyClient instance

    Example:
        >>> with connect_to_device(host="192.168.1.100") as client:
        ...     client.tap(500, 1000)
        ...     client.swipe(500, 1000, 500, 500)
    """
    config = ClientConfig(host=host, port=port, **kwargs)
    client = ScrcpyClient(config)
    if not client.connect():
        raise ConnectionError(f"Failed to connect to {host}:{port}")
    return client


def main():
    """Console entry point for scrcpy-connect command."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="scrcpy-py-ddlx - Connect to Android device"
    )
    parser.add_argument("--host", default="localhost", help="Server host")
    parser.add_argument("--port", type=int, default=27183, help="Server port")
    parser.add_argument(
        "--server", default="scrcpy-server", help="Path to scrcpy-server jar"
    )
    parser.add_argument("--device", help="Specific device serial")
    parser.add_argument("--no-audio", action="store_true", help="Disable audio")
    args = parser.parse_args()

    config = ClientConfig(
        host=args.host,
        port=args.port,
        server_jar=args.server,
        device_serial=args.device,
        audio=not args.no_audio,
    )

    try:
        client = ScrcpyClient(config)
        if client.connect():
            print(f"Connected to {client.device_name}")
            print(f"Device size: {client.device_size}")
            print("Press Ctrl+C to disconnect...")

            # Keep running until interrupted
            import signal

            signal.sigwaitinfo({signal.SIGINT})

        client.disconnect()
    except KeyboardInterrupt:
        print("\nDisconnected")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


__all__ = [
    "ScrcpyClient",
    "connect_to_device",
    "main",
]
