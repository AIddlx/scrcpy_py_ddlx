"""
TCP control channel heartbeat mechanism.

This module provides a heartbeat manager that sends PING messages to the server
and detects connection timeout if PONG responses are not received.

The heartbeat mechanism solves the following problems:
1. Server has no timeout detection - if client is killed, server waits forever
2. Network interruption (not physical disconnect) is not detected
3. Client may disconnect during 1-2 second network fluctuations

Workflow:
- Client sends PING every 2 seconds
- Server immediately responds with PONG (echoing timestamp)
- If no PONG received within 5 seconds, connection is considered dead
"""

import time
import threading
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class HeartbeatManager:
    """
    TCP control channel heartbeat manager.

    Sends periodic PING messages and detects timeout if PONG responses
    are not received within the timeout period.

    Usage:
        >>> def send_ping(timestamp):
        ...     # Send PING control message
        ...     pass
        >>> def on_timeout():
        ...     # Handle timeout - disconnect
        ...     pass
        >>>
        >>> heartbeat = HeartbeatManager(
        ...     ping_sender=send_ping,
        ...     on_timeout=on_timeout
        ... )
        >>> heartbeat.start()
        >>> # ... on PONG received ...
        >>> heartbeat.on_pong_received(timestamp)
        >>> # ... when done ...
        >>> heartbeat.stop()
    """

    # Default intervals (in seconds)
    DEFAULT_PING_INTERVAL = 2.0  # Send PING every 2 seconds
    DEFAULT_TIMEOUT = 5.0        # 5 seconds without PONG = timeout

    def __init__(
        self,
        ping_sender: Callable[[int], None],
        on_timeout: Callable[[], None],
        ping_interval: float = DEFAULT_PING_INTERVAL,
        timeout: float = DEFAULT_TIMEOUT
    ):
        """
        Initialize heartbeat manager.

        Args:
            ping_sender: Function to send PING message (takes timestamp in microseconds)
            on_timeout: Function to call when heartbeat timeout occurs
            ping_interval: Interval between PING messages (seconds)
            timeout: Timeout for PONG response (seconds)
        """
        self._ping_sender = ping_sender
        self._on_timeout = on_timeout
        self._ping_interval = ping_interval
        self._timeout = timeout

        # State
        self._last_pong_time = time.time()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Statistics
        self._pings_sent = 0
        self._pongs_received = 0

        logger.debug(
            f"HeartbeatManager initialized: interval={ping_interval}s, timeout={timeout}s"
        )

    def start(self) -> None:
        """Start the heartbeat thread."""
        if self._running:
            logger.warning("HeartbeatManager already running")
            return

        self._running = True
        self._last_pong_time = time.time()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name="Heartbeat",
            daemon=True
        )
        self._thread.start()
        logger.info("Heartbeat thread started")

    def stop(self) -> None:
        """Stop the heartbeat thread."""
        if not self._running:
            return

        logger.info("Stopping heartbeat thread...")
        self._running = False

        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

        logger.info(
            f"Heartbeat stopped: pings_sent={self._pings_sent}, "
            f"pongs_received={self._pongs_received}"
        )

    def on_pong_received(self, timestamp: int) -> None:
        """
        Called when a PONG message is received.

        Args:
            timestamp: The timestamp echoed from PING (microseconds)
        """
        with self._lock:
            self._last_pong_time = time.time()
            self._pongs_received += 1

        logger.debug(f"PONG received: timestamp={timestamp}")

    def _heartbeat_loop(self) -> None:
        """Main heartbeat loop (runs in dedicated thread)."""
        logger.debug("Heartbeat loop started")

        while self._running:
            # Send PING with current timestamp (microseconds)
            timestamp = int(time.time() * 1_000_000)
            try:
                self._ping_sender(timestamp)
                self._pings_sent += 1
                logger.debug(f"PING sent: timestamp={timestamp}")
            except Exception as e:
                logger.error(f"Failed to send PING: {e}")
                # Don't break - maybe transient error

            # Check timeout
            with self._lock:
                time_since_pong = time.time() - self._last_pong_time

            if time_since_pong > self._timeout:
                logger.warning(
                    f"Heartbeat timeout: no PONG for {time_since_pong:.1f}s "
                    f"(threshold: {self._timeout}s)"
                )
                try:
                    self._on_timeout()
                except Exception as e:
                    logger.error(f"Timeout callback error: {e}")
                break

            # Sleep until next PING
            # Use small sleep intervals to allow quick stop()
            sleep_end = time.time() + self._ping_interval
            while self._running and time.time() < sleep_end:
                time.sleep(0.1)  # 100ms check interval

        logger.debug("Heartbeat loop ended")

    @property
    def is_running(self) -> bool:
        """Check if heartbeat is running."""
        return self._running

    @property
    def last_pong_time(self) -> float:
        """Get the time of last PONG received (epoch seconds)."""
        with self._lock:
            return self._last_pong_time

    @property
    def stats(self) -> dict:
        """Get heartbeat statistics."""
        with self._lock:
            return {
                "pings_sent": self._pings_sent,
                "pongs_received": self._pongs_received,
                "last_pong_time": self._last_pong_time,
                "running": self._running,
            }
