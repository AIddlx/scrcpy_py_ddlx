"""
UDP device discovery for scrcpy network mode.

Broadcasts discovery request and collects responses from available devices.
"""

import socket
import logging
import time
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)


class UdpDiscovery:
    """UDP device discovery client."""

    DISCOVER_REQUEST = "SCRCPY_DISCOVER"
    DISCOVER_RESPONSE_PREFIX = "SCRCPY_HERE "
    DEFAULT_PORT = 27183
    DEFAULT_TIMEOUT = 3.0

    def __init__(self, port: int = DEFAULT_PORT, timeout: float = DEFAULT_TIMEOUT):
        self.port = port
        self.timeout = timeout

    def discover(self) -> List[Tuple[str, str]]:
        """
        Discover available scrcpy devices on the network.

        Returns:
            List of (device_name, ip_address) tuples
        """
        devices = []

        # Create UDP socket for broadcasting
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.5)  # Short timeout for each recv

        try:
            # Send broadcast
            message = self.DISCOVER_REQUEST.encode()
            sock.sendto(message, ("<broadcast>", self.port))
            # Also try direct broadcast addresses
            sock.sendto(message, ("255.255.255.255", self.port))

            logger.debug(f"Sent discovery broadcast to port {self.port}")

            # Collect responses
            start_time = time.time()
            while time.time() - start_time < self.timeout:
                try:
                    response, addr = sock.recvfrom(1024)
                    response_str = response.decode().strip()

                    if response_str.startswith(self.DISCOVER_RESPONSE_PREFIX):
                        # Parse response: "SCRCPY_HERE <device_name> <ip>"
                        parts = response_str[len(self.DISCOVER_RESPONSE_PREFIX):].split(" ", 1)
                        if len(parts) == 2:
                            device_name, device_ip = parts
                            devices.append((device_name, device_ip))
                            logger.debug(f"Found device: {device_name} at {device_ip}")
                        elif len(parts) == 1:
                            # Only device name, use sender IP
                            devices.append((parts[0], addr[0]))
                            logger.debug(f"Found device: {parts[0]} at {addr[0]}")

                except socket.timeout:
                    continue

        except Exception as e:
            logger.error(f"Discovery failed: {e}")
        finally:
            sock.close()

        # Remove duplicates
        seen = set()
        unique_devices = []
        for device in devices:
            if device[1] not in seen:
                seen.add(device[1])
                unique_devices.append(device)

        logger.info(f"Discovered {len(unique_devices)} device(s)")
        return unique_devices


def discover_devices(timeout: float = 3.0, port: int = 27183) -> List[Tuple[str, str]]:
    """
    Convenience function to discover devices.

    Args:
        timeout: Discovery timeout in seconds
        port: Discovery port (default 27183)

    Returns:
        List of (device_name, ip_address) tuples
    """
    discovery = UdpDiscovery(port, timeout)
    return discovery.discover()
