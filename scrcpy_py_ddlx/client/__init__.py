"""
scrcpy_py_ddlx/client

Client package for scrcpy-py-ddlx.

This package contains the complete client implementation:
- client: Main ScrcpyClient class with integrated connection and control management
- config: Configuration and state dataclasses
- components: Component factory for subsystem initialization
- lifecycle: Lifecycle management (start/stop/cleanup)
- runtime: Runtime management (Qt event loop, waiting)

Usage:
    from scrcpy_py_ddlx.client import ScrcpyClient, ClientConfig

    config = ClientConfig(host="localhost", port=27183)
    client = ScrcpyClient(config)
    if client.connect():
        client.run_with_qt()
"""

from .client import ScrcpyClient, connect_to_device, main
from .config import ClientConfig, ClientState, ConnectionMode
from .components import ComponentFactory, USE_STREAMING_DEMUXER
from .connection import ConnectionManager, NetworkConnection
from .udp_wake import UdpWakeClient, wake_device
from .udp_discovery import UdpDiscovery, discover_devices

__all__ = [
    # Main client
    "ScrcpyClient",
    "connect_to_device",
    "main",

    # Configuration
    "ClientConfig",
    "ClientState",
    "ConnectionMode",

    # Connection
    "ConnectionManager",
    "NetworkConnection",

    # UDP utilities
    "UdpWakeClient",
    "wake_device",
    "UdpDiscovery",
    "discover_devices",

    # Components
    "ComponentFactory",
    "USE_STREAMING_DEMUXER",
]
