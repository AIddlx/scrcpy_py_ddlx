"""
UDP wake and discovery client for scrcpy network mode.

Provides UDP discovery and wake functionality for connecting to
persistent scrcpy servers (stay-alive mode).

Usage:
    from scrcpy_py_ddlx.client.udp_wake import discover_devices, wake_device

    # Discover servers on the network
    devices = discover_devices()

    # Wake a specific server
    success = wake_device("192.168.5.4")
"""

import socket
import logging
import time
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Protocol constants
DISCOVERY_PORT = 27183
DISCOVER_REQUEST = b"SCRCPY_DISCOVER"
DISCOVER_RESPONSE_PREFIX = b"SCRCPY_HERE "
WAKE_REQUEST = b"WAKE_UP"
WAKE_RESPONSE = b"WAKE_ACK"

# Timeouts
DISCOVERY_TIMEOUT = 2.0
WAKE_TIMEOUT = 5.0


class UdpWakeClient:
    """UDP client for waking up scrcpy server on device."""

    DEFAULT_PORT = DISCOVERY_PORT
    DEFAULT_TIMEOUT = WAKE_TIMEOUT

    def __init__(self, device_ip: str, port: int = DEFAULT_PORT, timeout: float = DEFAULT_TIMEOUT):
        self.device_ip = device_ip
        self.port = port
        self.timeout = timeout

    def wake(self) -> Tuple[bool, Optional[str]]:
        """
        Send wake request to device.

        Returns:
            (success, error_message)
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)

        try:
            # Send wake request
            sock.sendto(WAKE_REQUEST, (self.device_ip, self.port))
            logger.debug(f"Sent WAKE_UP to {self.device_ip}:{self.port}")

            # Wait for response
            response, addr = sock.recvfrom(1024)

            if response == WAKE_RESPONSE:
                logger.info(f"Device {self.device_ip} woke up successfully")
                return True, None
            else:
                error = f"Unexpected response: {response}"
                logger.warning(error)
                return False, error

        except socket.timeout:
            error = f"Wake timeout for {self.device_ip}:{self.port}"
            logger.warning(error)
            return False, error
        except Exception as e:
            error = str(e)
            logger.error(f"Wake failed: {error}")
            return False, error
        finally:
            sock.close()


def discover_devices(timeout: float = DISCOVERY_TIMEOUT,
                      port: int = DISCOVERY_PORT) -> List[dict]:
    """
    Discover all scrcpy servers on the network.

    Args:
        timeout: Discovery timeout in seconds
        port: UDP discovery port

    Returns:
        List of discovered devices, each with 'name', 'ip'
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)

    devices = []

    try:
        # Send broadcast discovery
        sock.sendto(DISCOVER_REQUEST, ('<broadcast>', port))
        logger.debug(f"Sent discovery broadcast to port {port}")

        # Collect responses
        while True:
            try:
                data, addr = sock.recvfrom(1024)
                if data.startswith(DISCOVER_RESPONSE_PREFIX):
                    # Parse: "SCRCPY_HERE <device_name> <ip>"
                    payload = data[len(DISCOVER_RESPONSE_PREFIX):].decode('utf-8', errors='ignore')
                    parts = payload.strip().split(None, 1)

                    if len(parts) >= 2:
                        device_name = parts[0]
                        device_ip = parts[1]
                    elif len(parts) == 1:
                        device_name = parts[0]
                        device_ip = addr[0]
                    else:
                        device_name = "unknown"
                        device_ip = addr[0]

                    # Avoid duplicates
                    if not any(d['ip'] == device_ip for d in devices):
                        devices.append({
                            'name': device_name,
                            'ip': device_ip,
                        })
                        logger.info(f"Discovered: {device_name} at {device_ip}")

            except socket.timeout:
                break

    except Exception as e:
        logger.warning(f"Discovery error: {e}")
    finally:
        sock.close()

    return devices


def wake_device(device_ip: str, port: int = DISCOVERY_PORT, timeout: float = WAKE_TIMEOUT) -> bool:
    """
    Wake a sleeping scrcpy server.

    Args:
        device_ip: Device IP address
        port: Discovery port (default 27183)
        timeout: Request timeout in seconds

    Returns:
        True if successful
    """
    client = UdpWakeClient(device_ip, port, timeout)
    success, _ = client.wake()
    return success


def wake_and_wait(device_ip: str,
                   port: int = DISCOVERY_PORT,
                   wait_time: float = 0.5) -> Tuple[bool, Optional[str]]:
    """
    Wake server and wait for it to be ready.

    Args:
        device_ip: Device IP address
        port: UDP discovery port
        wait_time: Time to wait after wake for server to be ready

    Returns:
        (success, error_message)
    """
    # 1. Wake the server
    client = UdpWakeClient(device_ip, port)
    success, error = client.wake()
    if not success:
        return False, error

    # 2. Wait for server to be ready
    time.sleep(wait_time)

    return True, None


def check_server_alive(device_ip: str,
                        port: int = DISCOVERY_PORT,
                        timeout: float = 1.0) -> bool:
    """
    Check if a server is alive and in stay-alive mode.

    Sends a discovery request and checks for response.

    Args:
        device_ip: Device IP address
        port: UDP discovery port
        timeout: Check timeout in seconds

    Returns:
        True if server responds
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    try:
        # Send discovery request
        sock.sendto(DISCOVER_REQUEST, (device_ip, port))

        # Wait for response
        data, addr = sock.recvfrom(1024)
        return data.startswith(DISCOVER_RESPONSE_PREFIX)

    except socket.timeout:
        return False
    except Exception:
        return False
    finally:
        sock.close()


__all__ = [
    'UdpWakeClient',
    'discover_devices',
    'wake_device',
    'wake_and_wait',
    'check_server_alive',
    'DISCOVERY_PORT',
]
