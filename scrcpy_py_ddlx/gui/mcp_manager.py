"""
MCP Server manager for scrcpy-py-ddlx GUI.

Manages the HTTP MCP server in a separate thread to avoid blocking Qt main thread.
"""

import threading
import logging
from typing import Optional, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Check dependencies
try:
    import uvicorn
    from starlette.applications import Starlette
    UVICORN_AVAILABLE = True
except ImportError:
    UVICORN_AVAILABLE = False


@dataclass
class MCPServerStatus:
    """MCP server status."""
    running: bool = False
    host: str = "127.0.0.1"
    port: int = 3359
    error: Optional[str] = None


class MCPManager:
    """
    Manager for MCP HTTP server.

    Runs the server in a background thread to avoid blocking Qt main thread.
    """

    def __init__(self,
                 host: str = "127.0.0.1",
                 port: int = 3359,
                 on_status_change: Optional[Callable[[MCPServerStatus], None]] = None):
        """
        Initialize MCP manager.

        Args:
            host: Server host address
            port: Server port
            on_status_change: Callback for status changes
        """
        self.host = host
        self.port = port
        self.on_status_change = on_status_change

        self._server_thread: Optional[threading.Thread] = None
        self._server: Optional[uvicorn.Server] = None
        self._status = MCPServerStatus(host=host, port=port)
        self._stop_event = threading.Event()

    @property
    def status(self) -> MCPServerStatus:
        """Get current server status."""
        return self._status

    def _notify_status_change(self):
        """Notify status change callback."""
        if self.on_status_change:
            self.on_status_change(self._status)

    def start(self) -> bool:
        """
        Start the MCP server in a background thread.

        Returns:
            True if started successfully
        """
        if not UVICORN_AVAILABLE:
            self._status.error = "uvicorn and starlette are required"
            self._notify_status_change()
            logger.error("uvicorn and starlette are not installed")
            return False

        if self._status.running:
            logger.warning("MCP server is already running")
            return True

        self._stop_event.clear()

        def run_server():
            try:
                # Import here to avoid circular imports
                from scrcpy_http_mcp_server import app

                # Create uvicorn server
                config = uvicorn.Config(
                    app,
                    host=self.host,
                    port=self.port,
                    log_level="warning",
                    access_log=False
                )
                self._server = uvicorn.Server(config)

                # Update status
                self._status.running = True
                self._status.error = None
                self._notify_status_change()
                logger.info(f"MCP server started on http://{self.host}:{self.port}")

                # Run server
                self._server.run()

            except Exception as e:
                self._status.running = False
                self._status.error = str(e)
                self._notify_status_change()
                logger.error(f"MCP server error: {e}")
            finally:
                self._status.running = False
                self._server = None
                self._notify_status_change()
                logger.info("MCP server stopped")

        self._server_thread = threading.Thread(target=run_server, daemon=True)
        self._server_thread.start()

        # Wait a bit for server to start
        import time
        for _ in range(20):  # 2 seconds max
            time.sleep(0.1)
            if self._status.running:
                return True
            if self._status.error:
                return False

        return self._status.running

    def stop(self) -> bool:
        """
        Stop the MCP server.

        Returns:
            True if stopped successfully
        """
        if not self._status.running:
            logger.warning("MCP server is not running")
            return True

        self._stop_event.set()

        if self._server:
            self._server.should_exit = True

        # Wait for thread to finish
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=5)

        self._status.running = False
        self._notify_status_change()
        logger.info("MCP server stop requested")
        return True

    def is_running(self) -> bool:
        """Check if server is running."""
        return self._status.running

    def get_url(self) -> str:
        """Get the MCP server URL."""
        return f"http://{self.host}:{self.port}/mcp"

    def get_health_url(self) -> str:
        """Get the health check URL."""
        return f"http://{self.host}:{self.port}/health"


# Singleton instance
_mcp_manager: Optional[MCPManager] = None


def get_mcp_manager(
    host: str = "127.0.0.1",
    port: int = 3359,
    on_status_change: Optional[Callable[[MCPServerStatus], None]] = None
) -> MCPManager:
    """
    Get the singleton MCPManager instance.

    Args:
        host: Server host address (only used on first call)
        port: Server port (only used on first call)
        on_status_change: Callback for status changes (only used on first call)

    Returns:
        MCPManager singleton instance
    """
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPManager(
            host=host,
            port=port,
            on_status_change=on_status_change
        )
    return _mcp_manager
