"""
ADB Connection Management Module

This module provides ADB (Android Debug Bridge) connection management functionality,
including device discovery, server deployment, tunnel establishment, and cleanup.

Based on scrcpy's ADB implementation
"""

import os
import platform
import re
import subprocess
import shutil
import time
import logging
from typing import Optional, List, Dict, Tuple, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ADBDeviceType(Enum):
    """ADB device connection type"""

    USB = "usb"
    TCPIP = "tcpip"
    EMULATOR = "emulator"


class ADBDeviceState(Enum):
    """ADB device state"""

    UNKNOWN = "unknown"
    OFFLINE = "offline"
    DEVICE = "device"
    UNAUTHORIZED = "unauthorized"
    NO_PERMISSION = "no permissions"


@dataclass
class ADBDevice:
    """
    Represents an ADB device

    Attributes:
        serial: Device serial number or IP:port
        state: Device connection state
        model: Device model name
        device_type: Type of device connection
        selected: Whether this device is selected for connection
    """

    serial: str
    state: str
    model: Optional[str] = None
    device_type: ADBDeviceType = ADBDeviceType.USB
    selected: bool = False

    def is_ready(self) -> bool:
        """Check if device is ready for connection"""
        return self.state == ADBDeviceState.DEVICE.value

    def is_unauthorized(self) -> bool:
        """Check if device is unauthorized"""
        return self.state == ADBDeviceState.UNAUTHORIZED.value


@dataclass
class ADBTunnel:
    """
    Represents an ADB tunnel configuration

    Attributes:
        enabled: Whether tunnel is enabled
        forward: True if using adb forward, False for adb reverse
        local_port: Local port number
        server_socket: Server socket (for reverse connections)
    """

    enabled: bool = False
    forward: bool = False
    local_port: int = 0
    server_socket: Optional["socket.socket"] = None


class ADBError(Exception):
    """Base exception for ADB operations"""

    pass


class ADBCommandError(ADBError):
    """Exception raised when ADB command fails"""

    pass


class ADBDeviceNotFoundError(ADBError):
    """Exception raised when device is not found"""

    pass


class ADBConnectionError(ADBError):
    """Exception raised when connection fails"""

    pass


class ADBManager:
    """
    ADB Connection Manager

    Manages ADB operations including:
    - Device discovery and selection
    - Server jar deployment
    - Tunnel establishment (adb reverse/forward)
    - Process management
    - Connection cleanup

    Example:
        >>> adb = ADBManager()
        >>> devices = adb.list_devices()
        >>> adb.push_server(devices[0].serial, "/path/to/scrcpy-server.jar")
        >>> tunnel = adb.create_tunnel(devices[0].serial, "scrcpy", 27183)
    """

    # Default constants
    DEFAULT_SERVER_REMOTE_PATH = "/data/local/tmp/scrcpy-server"  # No .jar extension (disguised APK)
    SOCKET_NAME_PREFIX = "scrcpy_"
    DEFAULT_PORT_RANGE = (27183, 27299)
    TCP_PORT_DEFAULT = 5555

    def __init__(
        self,
        adb_path: Optional[str] = None,
        timeout: float = 30.0,
        log_level: int = logging.INFO,
    ):
        """
        Initialize ADB manager

        Args:
            adb_path: Path to adb executable (default: auto-detect)
            timeout: Default timeout for ADB commands
            log_level: Logging level
        """
        self.adb_path = adb_path or self._find_adb_executable()
        self.timeout = timeout
        self._device_info: Optional[Dict[str, Any]] = None
        self._server_processes: Dict[str, subprocess.Popen] = {}

        # Setup logging
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

    def _find_adb_executable(self) -> str:
        """
        Find ADB executable in system PATH or common locations

        Returns:
            Path to adb executable

        Raises:
            ADBError: If adb not found
        """
        # Check environment variable first
        adb_from_env = os.environ.get("ADB")
        if adb_from_env and os.path.isfile(adb_from_env):
            logger.debug(f"Using adb from environment: {adb_from_env}")
            return adb_from_env

        # Try to find in PATH
        adb_path = shutil.which("adb")
        if adb_path:
            logger.debug(f"Using adb from PATH: {adb_path}")
            return adb_path

        # Check Android SDK common locations
        possible_paths = [
            # macOS
            os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
            # Linux
            os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
            "/usr/bin/adb",
            "/usr/local/bin/adb",
            # Windows
            os.path.expanduser("~/AppData/Local/Android/Sdk/platform-tools/adb.exe"),
            "C:\\Android\\sdk\\platform-tools\\adb.exe",
        ]

        for path in possible_paths:
            if os.path.isfile(path):
                logger.debug(f"Using adb from: {path}")
                return path

        raise ADBError(
            "ADB executable not found. Please install Android Platform Tools "
            "or set ADB environment variable."
        )

    def push_server(
        self,
        serial: str,
        server_path: str,
        remote_path: str = DEFAULT_SERVER_REMOTE_PATH,
        timeout: Optional[float] = None,
    ) -> bool:
        """
        Push scrcpy-server file to device.

        Args:
            serial: Device serial number
            server_path: Path to scrcpy-server file (APK)
            remote_path: Remote path on device (default: /data/local/tmp/scrcpy-server)
            timeout: Command timeout (uses default if None)

        Returns:
            True if push successful, False otherwise

        Raises:
            ADBCommandError: If push fails
        """
        args = ["-s", serial, "push", server_path, remote_path]
        result = self._execute(args, device_serial=serial, timeout=timeout)
        return True

    def start_server(
        self,
        serial: str,
        package_name: str = "com.genymobile.scrcpy.Server",
        main_class: str = "com.genymobile.scrcpy.Server",
        timeout: Optional[float] = None,
        client_version: Optional[str] = None,
        server_params: Optional[str] = None,
        background: bool = False,
    ) -> bool:
        """
        Start scrcpy-server on device using app_process.

        Args:
            serial: Device serial number
            package_name: Package name (default: com.genymobile.scrcpy.Server)
            main_class: Main class (default: com.genymobile.scrcpy.Server)
            timeout: Command timeout (uses default if None)
            client_version: Client version string (required by server)
            server_params: Additional server parameters (optional)
            background: Start server in background without waiting (for forward mode)

        Returns:
            True if server started successfully, False otherwise

        Raises:
            ADBCommandError: If start command fails

        Note:
            This starts the server using adb shell app_process.
            The app_process command executes Java code from pushed server file.
            CLASSPATH points to /data/local/tmp/scrcpy-server (the remote file path, no .jar extension).

            IMPORTANT: Server REQUIRES at least one parameter (client_version).
            Without it, server will fail with "Missing client version" error.

            Command equivalent:
            adb -s <serial> shell CLASSPATH=/data/local/tmp/scrcpy-server app_process / <package_name> [params...]

            Examples:
            - Basic: ... app_process / com.genymobile.scrcpy.Server --version 3.0
            - List: ... app_process / com.genymobile.scrcpy.Server --list-displays
        """
        # Build app_process command
        # Usage: CLASSPATH=/data/local/tmp/scrcpy-server app_process / <package_name> [params...]
        # Note: File is scrcpy-server (no .jar extension) to avoid Android detection
        args = [
            "-s",
            serial,
            "shell",
            f"CLASSPATH=/data/local/tmp/scrcpy-server",
            "app_process",
            "/",
            package_name,
        ]

        # Add required parameters
        # The first parameter is the client version (not --version flag)
        if client_version:
            args.extend([client_version])
            logger.debug(f"Adding client version: {client_version}")

        if server_params:
            # Additional parameters can be passed as a single string
            args.extend(
                server_params.split()
                if isinstance(server_params, str)
                else server_params
            )
            logger.debug(f"Adding server params: {server_params}")

        # Build full command
        cmd = [self.adb_path] + args

        if background:
            # Start server in background (don't wait)
            # Used for forward mode where server waits for client connection
            # Note: We capture stderr to log server startup errors
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,  # Capture stderr for debugging
                creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
            )
            logger.info("Server started in background")
        else:
            # Execute command normally (waits for server to complete)
            result = self._execute(
                args, device_serial=None, timeout=timeout, capture_output=False
            )

        return True

    def _execute(
        self,
        args: List[str],
        device_serial: Optional[str] = None,
        timeout: Optional[float] = None,
        capture_output: bool = True,
    ) -> "subprocess.CompletedProcess[Any]":
        """
        Execute an ADB command

        Args:
            args: ADB command arguments (without 'adb' prefix)
            device_serial: Optional device serial to target
            timeout: Command timeout (uses default if None)
            capture_output: Whether to capture stdout/stderr (default: True)

        Returns:
            CompletedProcess with stdout, stderr, returncode

        Raises:
            ADBCommandError: If command fails
        """
        # Build command
        cmd = [self.adb_path]

        # Add device targeting if specified
        if device_serial:
            cmd.extend(["-s", device_serial])

        # Add command arguments
        cmd.extend(args)

        # Set timeout
        timeout = timeout if timeout is not None else self.timeout

        logger.debug(f"Executing ADB command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=capture_output,
                text=capture_output,
                timeout=timeout,
                check=False,
            )

            # Log output at debug level
            if result.stdout:
                logger.debug(f"ADB stdout: {result.stdout[:200]}")
            if result.stderr:
                logger.debug(f"ADB stderr: {result.stderr[:200]}")

            # Check for errors
            if result.returncode != 0:
                raise ADBCommandError(
                    f"ADB command failed: {' '.join(cmd)}\n"
                    f"Return code: {result.returncode}\n"
                    f"Stderr: {result.stderr}"
                )

            return result

        except subprocess.TimeoutExpired as e:
            raise ADBCommandError(
                f"ADB command timed out after {timeout}s: {' '.join(cmd)}"
            )
        except FileNotFoundError as e:
            raise ADBError(f"ADB executable not found: {self.adb_path}")
        except Exception as e:
            raise ADBCommandError(f"ADB command error: {e}")

    def list_devices(self, long_format: bool = True) -> List[ADBDevice]:
        """
        List all connected ADB devices

        Args:
            long_format: Use long format (includes model info)

        Returns:
            List of ADBDevice objects

        Raises:
            ADBCommandError: If command fails
        """
        args = ["devices", "-l"] if long_format else ["devices"]
        result = self._execute(args)

        devices = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("List of devices"):
                continue

            parts = line.split()
            if len(parts) < 2:
                continue

            serial = parts[0]
            state = parts[1]

            # Parse additional info
            model = None
            if len(parts) > 2:
                for part in parts[2:]:
                    if part.startswith("model:"):
                        model = part.split(":", 1)[1]
                    elif part.startswith("product:"):
                        # Could extract product name here
                        pass

            # Determine device type
            device_type = self._get_device_type(serial)
            device = ADBDevice(
                serial=serial,
                state=state,
                model=model,
                device_type=device_type,
            )
            devices.append(device)
            logger.debug(f"Found device: {serial} ({state}) - {model}")

        return devices

    def _get_device_type(self, serial: str) -> ADBDeviceType:
        """Determine device type from serial"""
        if serial.startswith("emulator-"):
            return ADBDeviceType.EMULATOR
        elif ":" in serial:
            return ADBDeviceType.TCPIP
        else:
            return ADBDeviceType.USB

    def select_device(
        self,
        serial: Optional[str] = None,
        device_type: Optional[ADBDeviceType] = None,
    ) -> ADBDevice:
        """
        Select a device for connection

        Args:
            serial: Specific device serial to select
            device_type: Filter by device type (USB/TCPIP/EMULATOR)

        Returns:
            Selected ADBDevice

        Raises:
            ADBDeviceNotFoundError: If no device found
            ADBConnectionError: If device not ready
        """
        devices = self.list_devices()
        if not devices:
            raise ADBDeviceNotFoundError("No ADB devices found")

        # Filter by serial if specified
        if serial:
            for device in devices:
                if device.serial == serial:
                    if not device.is_ready():
                        if device.is_unauthorized():
                            raise ADBConnectionError(
                                f"Device {serial} is unauthorized. "
                                "Please accept debugging prompt on device."
                            )
                        else:
                            raise ADBConnectionError(
                                f"Device {serial} not ready (state={device.state})"
                            )
                    return device
            raise ADBDeviceNotFoundError(f"Device not found: {serial}")

        # Filter by type if specified
        if device_type:
            candidates = [d for d in devices if d.device_type == device_type]
            if not candidates:
                raise ADBDeviceNotFoundError(f"No {device_type.value} devices found")
            if len(candidates) > 1:
                raise ADBDeviceNotFoundError(
                    f"Multiple {device_type.value} devices found. "
                    "Please specify serial."
                )
            if not candidates[0].is_ready():
                raise ADBConnectionError(
                    f"Device not ready (state={candidates[0].state})"
                )
            return candidates[0]

        # Single device available
        if len(devices) == 1:
            if not devices[0].is_ready():
                raise ADBConnectionError(f"Device not ready (state={devices[0].state})")
            return devices[0]

        # Multiple devices, need selection
        raise ADBDeviceNotFoundError(
            f"Multiple devices found: {', '.join(d.serial for d in devices)}. "
            "Please specify device serial."
        )

    def cleanup(self, serial: Optional[str] = None) -> None:
        """
        Cleanup ADB connections and processes

        Args:
            serial: Device serial to cleanup (None for all)
        """
        # Terminate all server processes
        if serial is None:
            processes_to_clean = list(self._server_processes.values())
        else:
            processes_to_clean = [self._server_processes.get(serial)]

        for process in processes_to_clean:
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=5.0)
                except Exception as e:
                    logger.warning(f"Failed to terminate server process: {e}")
                    try:
                        process.kill()
                    except Exception:
                        pass

        # Clear process tracking
        if serial is None:
            self._server_processes.clear()
        elif serial in self._server_processes:
            del self._server_processes[serial]

        logger.info("ADB cleanup completed")

    def create_tunnel(
        self,
        serial: str,
        socket_name: str,
        port_range: Tuple[int, int] = DEFAULT_PORT_RANGE,
        force_forward: bool = False,
    ) -> "ADBTunnel":
        """
        Create ADB tunnel (reverse or forward).

        Args:
            serial: Device serial number
            socket_name: Socket name on device (e.g., "scrcpy_xxxxxxxx")
            port_range: Port range to try (start, end) for reverse
            force_forward: Force using forward mode (default: try reverse first)

        Returns:
            ADBTunnel object with tunnel configuration

        Raises:
            ADBCommandError: If tunnel creation fails

        Note:
            Based on scrcpy source (adb_tunnel.c):
            - Try 'adb reverse' first (preferred: device connects to client)
            - Fallback to 'adb forward' if reverse fails (e.g., over 'adb connect')
            - For forward: client listens, server connects
            - For reverse: device listens, client connects
        """
        tunnel = ADBTunnel()

        if not force_forward:
            # Try reverse mode first (preferred)
            try:
                # Command: adb -s <serial> reverse localabstract:<socket_name> tcp:<port>
                for port in range(port_range[0], port_range[1] + 1):
                    args = [
                        "-s",
                        serial,
                        "reverse",
                        f"localabstract:{socket_name}",
                        f"tcp:{port}",
                    ]

                    result = self._execute(args, device_serial=serial, timeout=5.0)
                    if result.returncode == 0:
                        tunnel.enabled = True
                        tunnel.forward = False
                        tunnel.local_port = port
                        logger.info(
                            f"Tunnel created (reverse): localabstract:{socket_name} <-> tcp:{port}"
                        )
                        return tunnel
            except ADBCommandError:
                logger.warning(f"ADB reverse failed, trying forward mode...")
                # Fall through to forward mode

        # Fallback to forward mode
        try:
            # Command: adb -s <serial> forward tcp:<port> localabstract:<socket_name>
            for port in range(port_range[0], port_range[1] + 1):
                args = [
                    "-s",
                    serial,
                    "forward",
                    f"tcp:{port}",
                    f"localabstract:{socket_name}",
                ]

                result = self._execute(args, device_serial=serial, timeout=5.0)
                if result.returncode == 0:
                    tunnel.enabled = True
                    tunnel.forward = True
                    tunnel.local_port = port
                    logger.info(
                        f"Tunnel created (forward): tcp:{port} <-> localabstract:{socket_name}"
                    )
                    return tunnel
        except ADBCommandError:
            raise ADBCommandError(
                f"Failed to create ADB tunnel (both reverse and forward failed)"
            )

        raise ADBCommandError(f"No available port in range {port_range}")

    def remove_tunnel(
        self,
        serial: str,
        tunnel: "ADBTunnel",
        socket_name: str,
    ) -> bool:
        """
        Remove ADB tunnel.

        Args:
            serial: Device serial number
            tunnel: ADBTunnel object from create_tunnel()
            socket_name: Socket name on device

        Returns:
            True if tunnel removed successfully, False otherwise

        Raises:
            ADBCommandError: If tunnel removal fails

        Note:
            Based on scrcpy source (adb_tunnel.c):
            For reverse: adb -s <serial> reverse --remove localabstract:<socket_name>
            For forward: adb -s <serial> forward --remove tcp:<port>
        """
        if not tunnel.enabled:
            logger.warning("Tunnel not enabled, nothing to remove")
            return True

        if tunnel.forward:
            # Remove forward tunnel
            args = ["-s", serial, "forward", "--remove", f"tcp:{tunnel.local_port}"]
        else:
            # Remove reverse tunnel
            args = ["-s", serial, "reverse", "--remove", f"localabstract:{socket_name}"]

        result = self._execute(args, device_serial=serial, timeout=5.0)
        if result.returncode == 0:
            logger.info(f"Tunnel removed: {'forward' if tunnel.forward else 'reverse'}")
            return True
        else:
            raise ADBCommandError(f"Failed to remove ADB tunnel")

    def __enter__(self):
        """Context manager entry"""
        return self

    # ===== TCP/IP Wireless Connection Methods =====

    def get_device_ip(self, serial: str, timeout: Optional[float] = None) -> Optional[str]:
        """
        Get the WiFi IP address of a device (prioritize wlan0 interface).

        Args:
            serial: Device serial number
            timeout: Command timeout

        Returns:
            IP address as string, or None if not found

        Raises:
            ADBCommandError: If command fails

        Note:
            First tries to get IP from wlan0 interface using 'ip addr show wlan0'.
            Falls back to 'ip route' if wlan0 is not available.
            Filters out emulator IPs (10.0.2.x, 10.10.10.x) and VPN/tunnel interfaces.
        """
        # Method 1: Try wlan0 interface first (most reliable for real devices)
        try:
            result = self._execute(
                ["-s", serial, "shell", "ip", "addr", "show", "wlan0"],
                device_serial=serial,
                timeout=timeout or 5.0,
            )

            # Parse wlan0 output to find inet address (skip inet6)
            import re
            for line in result.stdout.strip().splitlines():
                if 'inet ' in line and 'inet6' not in line:
                    # Format: inet 192.168.1.100/24 brd ...
                    match = re.search(r'inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', line)
                    if match:
                        ip_candidate = match.group(1)
                        # Filter out emulator IPs
                        if ip_candidate.startswith('10.0.2.') or ip_candidate.startswith('10.10.10.'):
                            logger.warning(f"Detected emulator/VPN IP {ip_candidate}, skipping")
                            continue
                        if self._is_valid_ip(ip_candidate):
                            logger.info(f"Device IP from wlan0: {ip_candidate}")
                            return ip_candidate
        except Exception as e:
            logger.debug(f"Failed to get IP from wlan0: {e}")

        # Method 2: Fall back to ip route (may return VPN interface IP)
        try:
            result = self._execute(
                ["-s", serial, "shell", "ip", "route"],
                device_serial=serial,
                timeout=timeout or 10.0,
            )

            # Parse output to find IP from wlan0 interface
            import re
            for line in result.stdout.strip().splitlines():
                # Only consider wlan0 interface routes
                if 'wlan0' in line:
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part == "src" and i + 1 < len(parts):
                            ip_candidate = parts[i + 1]
                            # Filter out emulator IPs
                            if ip_candidate.startswith('10.0.2.') or ip_candidate.startswith('10.10.10.'):
                                logger.debug(f"Skipping emulator/VPN IP: {ip_candidate}")
                                continue
                            if self._is_valid_ip(ip_candidate):
                                logger.info(f"Device IP from ip route (wlan0): {ip_candidate}")
                                return ip_candidate

            # Last resort: check column 9 (common format)
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 9 and 'wlan0' in line:
                    ip_candidate = parts[8]
                    if self._is_valid_ip(ip_candidate):
                        logger.info(f"Device IP from column 9 (wlan0): {ip_candidate}")
                        return ip_candidate

            logger.warning("Could not find device IP address from wlan0")
            return None

        except ADBCommandError as e:
            logger.error(f"Failed to get device IP: {e}")
            return None

    def _is_valid_ip(self, ip: str) -> bool:
        """Validate IP address format"""
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        try:
            return all(0 <= int(p) <= 255 for p in parts)
        except ValueError:
            return False

    def get_adb_tcp_port(self, serial: str, timeout: Optional[float] = None) -> Optional[int]:
        """
        Check if ADB TCP/IP mode is enabled and get the port.

        Args:
            serial: Device serial number
            timeout: Command timeout

        Returns:
            Port number if TCP/IP is enabled, None otherwise
        """
        try:
            result = self._execute(
                ["-s", serial, "shell", "getprop", "service.adb.tcp.port"],
                device_serial=serial,
                timeout=timeout or 5.0,
            )

            port_str = result.stdout.strip()
            if not port_str:
                return None

            try:
                port = int(port_str)
                if 0 <= port <= 65535:
                    logger.info(f"ADB TCP port: {port}")
                    return port
            except ValueError:
                pass

            return None

        except ADBCommandError:
            return None

    def enable_tcpip(
        self, serial: str, port: int = TCP_PORT_DEFAULT, timeout: Optional[float] = None
    ) -> bool:
        """
        Enable ADB TCP/IP mode on device.

        Args:
            serial: Device serial number
            port: TCP port to use (default: 5555)
            timeout: Command timeout

        Returns:
            True if successful, False otherwise

        Raises:
            ADBCommandError: If command fails

        Note:
            Command: adb -s <serial> tcpip <port>
            This restarts adbd in TCP/IP mode on the device.
        """
        logger.info(f"Enabling TCP/IP mode on port {port}...")

        try:
            result = self._execute(
                ["-s", serial, "tcpip", str(port)],
                device_serial=serial,
                timeout=timeout or 30.0,
            )

            # Wait for adbd to restart
            time.sleep(2.0)

            # Verify TCP/IP is enabled
            current_port = self.get_adb_tcp_port(serial, timeout=10.0)
            if current_port == port:
                logger.info(f"TCP/IP mode enabled on port {port}")
                return True
            else:
                logger.warning(f"TCP/IP enable requested, but current port is {current_port}")
                return False

        except ADBCommandError as e:
            logger.error(f"Failed to enable TCP/IP mode: {e}")
            return False

    def connect_tcpip(
        self, ip: str, port: int = TCP_PORT_DEFAULT, timeout: Optional[float] = None
    ) -> bool:
        """
        Connect to a device via TCP/IP.

        Args:
            ip: Device IP address
            port: TCP port (default: 5555)
            timeout: Command timeout

        Returns:
            True if connection successful, False otherwise

        Raises:
            ADBCommandError: If command fails

        Note:
            Command: adb connect <ip>:<port>
            The device must have TCP/IP mode enabled first.
        """
        ip_port = f"{ip}:{port}"
        logger.info(f"Connecting to {ip_port}...")

        try:
            result = self._execute(
                ["connect", ip_port],
                device_serial=None,
                timeout=timeout or 30.0,
            )

            # Check if connection was successful
            if "connected" in result.stdout.lower():
                logger.info(f"Connected to {ip_port}")
                return True
            else:
                logger.warning(f"Connection response: {result.stdout}")
                return False

        except ADBCommandError as e:
            logger.error(f"Failed to connect to {ip_port}: {e}")
            return False

    def disconnect_tcpip(
        self, ip: str, port: int = TCP_PORT_DEFAULT, timeout: Optional[float] = None
    ) -> bool:
        """
        Disconnect from a TCP/IP device.

        Args:
            ip: Device IP address
            port: TCP port (default: 5555)
            timeout: Command timeout

        Returns:
            True if disconnection successful, False otherwise

        Note:
            Command: adb disconnect <ip>:<port>
        """
        ip_port = f"{ip}:{port}"
        logger.info(f"Disconnecting from {ip_port}...")

        try:
            result = self._execute(
                ["disconnect", ip_port],
                device_serial=None,
                timeout=timeout or 10.0,
            )

            logger.info(f"Disconnected from {ip_port}")
            return True

        except ADBCommandError:
            # Disconnect might fail if already disconnected, that's ok
            logger.debug(f"Disconnect from {ip_port} (may already be disconnected)")
            return True

    def wait_for_tcpip_enabled(
        self, serial: str, expected_port: int, max_attempts: int = 40, delay: float = 0.25
    ) -> bool:
        """
        Wait for TCP/IP mode to be enabled on device.

        Args:
            serial: Device serial number
            expected_port: Expected TCP port
            max_attempts: Maximum number of attempts (default: 40)
            delay: Delay between attempts in seconds (default: 0.25)

        Returns:
            True if TCP/IP mode is enabled, False if timeout

        Note:
            After 'adb tcpip' command, adbd takes time to restart.
            This method waits for the service.adb.tcp.port property to be set.
        """
        logger.info("Waiting for TCP/IP mode to be enabled...")

        for attempt in range(max_attempts):
            current_port = self.get_adb_tcp_port(serial, timeout=5.0)
            if current_port == expected_port:
                logger.info(f"TCP/IP mode enabled on port {expected_port}")
                return True

            if attempt < max_attempts - 1:
                time.sleep(delay)

        logger.error(f"Timeout waiting for TCP/IP mode on port {expected_port}")
        return False

    def list_apps(
        self,
        serial: str,
        timeout: Optional[float] = 60.0,
    ) -> List[Dict[str, Any]]:
        """
        Get list of installed applications on the device.

        Uses scrcpy-server's list_apps parameter to query applications
        via Android PackageManager API.

        Args:
            serial: Device serial number
            timeout: Command timeout in seconds (default: 60, as app listing may take time)

        Returns:
            List[dict]: Application list with keys:
                - "name": App display name (e.g., "Firefox")
                - "package": Package name (e.g., "org.mozilla.firefox")
                - "system": Boolean, True if system app

        Raises:
            ADBCommandError: If command fails

        Note:
            Output format from server:
                [server] INFO: List of apps:
                [server] INFO:  * Camera                     com.android.camera
                [server] INFO:  - Firefox                    org.mozilla.firefox
            (* = system app, - = user app)

            Command equivalent:
            adb -s <serial> shell CLASSPATH=/data/local/tmp/scrcpy-server \\
                app_process / com.genymobile.scrcpy.Server 3.3.4 list_apps=true
        """
        logger.info(f"Getting application list from device {serial}...")

        # Push server to device (always needed - file may be deleted after connection)
        logger.debug("Pushing scrcpy-server to device...")
        self.push_server(serial, "scrcpy-server", remote_path="/data/local/tmp/scrcpy-server")

        # Build command
        # IMPORTANT: CLASSPATH must match the actual file path (without .jar extension)
        # even though the local file might be named differently
        cmd = [
            self.adb_path,
            "-s", serial,
            "shell",
            "CLASSPATH=/data/local/tmp/scrcpy-server",
            "app_process",
            "/",
            "com.genymobile.scrcpy.Server",
            "3.3.4",  # client version (required)
            "list_apps=true",
        ]

        logger.debug(f"Executing ADB command: {' '.join(cmd)}")

        try:
            # Use Popen with unbuffered pipes to avoid JVM crash due to buffer issues
            # The scrcpy server writes a lot of output to stdout which can cause
            # broken pipe errors if the buffer fills up
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,  # Unbuffered
                text=False,  # Read as bytes for proper decoding
            )

            # Read all output with timeout
            try:
                stdout_bytes, stderr_bytes = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                raise ADBCommandError(f"Timeout getting app list (>{timeout}s)")

            # Decode output
            stdout = stdout_bytes.decode('utf-8', errors='ignore')
            stderr = stderr_bytes.decode('utf-8', errors='ignore')

            if process.returncode != 0:
                raise ADBCommandError(
                    f"ADB command failed: {' '.join(cmd)}\n"
                    f"Return code: {process.returncode}\n"
                    f"Stderr: {stderr}"
                )

            # Parse server output
            apps = []
            lines = stdout.split("\n")

            in_app_list = False
            prefix = "[server] INFO:"

            for line in lines:
                # Look for "List of apps:" marker
                if "List of apps:" in line:
                    in_app_list = True
                    continue

                if not in_app_list:
                    continue

                # Strip the line for processing
                stripped = line.strip()

                # Skip empty lines
                if not stripped:
                    continue

                # Skip lines that still have the prefix (shouldn't happen after header)
                if stripped.startswith(prefix):
                    continue

                # Parse format: " * AppName                     package.name"
                # or: " - AppName                     package.name"
                if len(stripped) < 4:
                    continue

                # First char is the marker (* or -)
                marker = stripped[0]
                if marker not in ("*", "-"):
                    continue

                # Find package name (last word)
                parts = stripped[1:].strip().rsplit(None, 1)
                if len(parts) != 2:
                    continue

                name = parts[0].strip()
                package = parts[1].strip()

                apps.append({
                    "name": name,
                    "package": package,
                    "system": marker == "*",
                })

            logger.info(f"Found {len(apps)} applications")
            return apps

        except FileNotFoundError:
            raise ADBError(f"ADB executable not found: {self.adb_path}")
        except Exception as e:
            if isinstance(e, ADBCommandError):
                raise
            raise ADBCommandError(f"ADB command error: {e}")

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.cleanup()
