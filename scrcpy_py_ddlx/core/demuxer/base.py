"""
Base classes for demuxer implementations.

This module contains the base classes for both buffer-based and streaming
demuxers, along with their exception types.
"""

import logging
import socket
import threading
from typing import Optional, Callable
from queue import Queue

logger = logging.getLogger(__name__)


# Default buffer sizes
DEFAULT_DEMUXER_BUFFER_SIZE = (
    2 * 1024 * 1024
)  # 2MB (increased from 256KB to handle large keyframes)
DEFAULT_PACKET_QUEUE_SIZE = 1  # Minimal latency (1 packet ≈ 16ms at 60fps)


class DemuxerError(Exception):
    """Base exception for demuxer errors."""

    pass


class DemuxerStoppedError(DemuxerError):
    """Raised when demuxer is stopped."""

    pass


class BaseDemuxer:
    """
    Base class for stream demuxers.

    A demuxer runs in a dedicated thread, reads data from a socket,
    parses packets, and passes them to a callback/queue for processing.

    Based on official scrcpy demuxer design (app/src/demuxer.h).

    Performance optimizations:
    - Uses read/write offsets instead of moving data every time
    - Lazy compression: only compacts buffer when 75% full
    - Uses memoryview to avoid temporary bytes objects
    """

    # Threshold for lazy compression (75% of buffer size)
    COMPRESSION_THRESHOLD = 0.75

    def __init__(
        self,
        sock: socket.socket,
        packet_queue: Queue,
        buffer_size: int = DEFAULT_DEMUXER_BUFFER_SIZE,
    ):
        """
        Initialize the demuxer.

        Args:
            sock: Socket to read stream data from
            packet_queue: Queue to pass parsed packets to decoder
            buffer_size: Size of receive buffer (default: 256KB)
        """
        self._socket = sock
        self._packet_queue = packet_queue
        self._buffer_size = buffer_size

        # Threading
        self._thread: Optional[threading.Thread] = None
        self._stopped = threading.Event()
        self._lock = threading.Lock()

        # Pause/Resume state
        self._paused = False
        self._pause_event = threading.Event()
        self._pause_event.set()  # Start unpaused

        # Buffer management with read/write offsets
        self._read_offset = 0  # Offset of data to be parsed
        self._write_offset = 0  # Offset of next write position

        # Statistics
        self._bytes_received = 0
        self._packets_parsed = 0
        self._parse_errors = 0
        self._compression_count = 0  # Track buffer compressions
        self._bytes_dropped = 0  # Bytes dropped while paused

    def pause(self) -> None:
        """
        Pause parsing (stop CPU), but keep draining socket.

        This method is called when video/audio is disabled at runtime.
        The demuxer continues reading from socket to prevent TCP buffer
        buildup on the server, but discards packets instead of parsing.

        This ensures:
        - TCP connection stays alive
        - Server encoder doesn't block
        - Can quickly resume when needed
        """
        if self._paused:
            return

        self._paused = True
        logger.info(f"{self.__class__.__name__} paused (draining socket to prevent buildup)")

    def resume(self) -> None:
        """
        Resume reading from socket.

        This method is called when video/audio is enabled at runtime.
        """
        if not self._paused:
            return

        self._paused = False
        self._pause_event.set()  # Unblock read loop
        logger.info(f"{self.__class__.__name__} resumed")

    def start(self) -> None:
        """
        Start the demuxer thread.

        The thread will continuously read from the socket, parse packets,
        and place them in the output queue.
        """
        if self._thread is not None:
            logger.warning("Demuxer already started")
            return

        self._stopped.clear()
        self._thread = threading.Thread(
            target=self._run_demuxer_loop, name=self._get_thread_name(), daemon=True
        )
        self._thread.start()
        logger.info(f"{self._get_thread_name()} started")

    def stop(self) -> None:
        """Stop the demuxer and wait for thread to finish."""
        if self._thread is None:
            return

        logger.info(f"Stopping {self._get_thread_name()}...")
        self._stopped.set()

        # Close socket to interrupt blocking recv
        try:
            self._socket.close()
        except Exception as e:
            logger.debug(f"Error closing socket: {e}")

        # Wait for thread to finish
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            logger.warning(f"{self._get_thread_name()} did not stop gracefully")

        self._thread = None
        logger.info(f"{self._get_thread_name()} stopped")

    def _run_demuxer_loop(self) -> None:
        """
        Main demuxer loop (runs in dedicated thread).

        This loop:
        1. Receives data from socket
        2. Parses packet headers and payloads
        3. Places complete packets in output queue

        Performance optimizations:
        - Uses read/write offsets instead of moving data
        - Lazy compression: only compacts when buffer is 75% full
        - Uses memoryview to avoid temporary bytes objects
        """
        buffer = bytearray(self._buffer_size)
        self._read_offset = 0
        self._write_offset = 0

        try:
            while not self._stopped.is_set():
                # When paused: drain socket to prevent TCP buffer buildup
                # Read into buffer but don't parse (keep buffer flowing)
                if self._paused:
                    try:
                        # Keep reading but reset buffer to avoid parsing
                        chunk = self._socket.recv(65536)
                        if chunk:
                            self._bytes_dropped += len(chunk)
                            self._bytes_received += len(chunk)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    # Reset offsets to avoid parsing old data on resume
                    self._read_offset = 0
                    self._write_offset = 0
                    continue

                # Calculate available space in buffer
                available_space = self._buffer_size - self._write_offset

                # Receive data from socket
                try:
                    chunk = self._socket.recv(min(available_space, 65536))
                    if not chunk:
                        # Connection closed
                        break
                    buffer[self._write_offset:self._write_offset + len(chunk)] = chunk
                    self._write_offset += len(chunk)
                    self._bytes_received += len(chunk)
                except socket.timeout:
                    continue
                except OSError:
                    if not self._stopped.is_set():
                        logger.warning("Socket error in demuxer loop")
                    break

                # Calculate remaining data size
                remaining_size = self._write_offset - self._read_offset

                if remaining_size == 0:
                    # Buffer is empty, reset offsets
                    self._read_offset = 0
                    self._write_offset = 0
                    continue

                # Parse packets using memoryview for performance
                view = memoryview(buffer)
                while remaining_size > 0:
                    consumed = self._parse_buffer_with_offset(
                        view[self._read_offset:self._write_offset],
                        remaining_size
                    )
                    if consumed == 0:
                        # Incomplete packet, wait for more data
                        break
                    self._read_offset += consumed
                    remaining_size -= consumed

                # Check if buffer is getting full - trigger lazy compression
                available_space = self._buffer_size - self._write_offset
                if available_space < self._buffer_size * (1 - self.COMPRESSION_THRESHOLD):
                    self._compact_buffer(buffer)

        except Exception as e:
            logger.error(f"Demuxer loop error: {e}", exc_info=True)
        finally:
            logger.debug(f"{self._get_thread_name()} loop ended")

    def _compact_buffer(self, buffer: bytearray) -> None:
        """
        Compact buffer by moving remaining data to the beginning.

        This is called when buffer is 75% full to avoid running out of space.
        """
        remaining_size = self._write_offset - self._read_offset
        if remaining_size > 0:
            # Move remaining data to beginning of buffer
            buffer[:remaining_size] = buffer[self._read_offset:self._write_offset]
        self._read_offset = 0
        self._write_offset = remaining_size

    def _parse_buffer(self, buffer: bytearray, size: int) -> int:
        """
        Parse packets from buffer.

        Args:
            buffer: Data buffer
            size: Number of bytes in buffer

        Returns:
            Number of bytes consumed (0 if packet incomplete)
        """
        raise NotImplementedError("Subclasses must implement _parse_buffer")

    def _parse_buffer_with_offset(self, view: memoryview, size: int) -> int:
        """
        Parse packets from buffer using memoryview (optimized version).

        This method receives a memoryview instead of creating a temporary bytes object.
        Default implementation calls _parse_buffer for backward compatibility.

        Args:
            view: Memoryview of data buffer
            size: Number of bytes in buffer

        Returns:
            Number of bytes consumed (0 if packet incomplete)
        """
        # Default: convert to bytes for backward compatibility
        # Subclasses should override this for better performance
        return self._parse_buffer(bytearray(view), size)

    def _get_thread_name(self) -> str:
        """Get the thread name for this demuxer."""
        return "Demuxer"

    def get_stats(self) -> dict:
        """Get demuxer statistics."""
        return {
            "bytes_received": self._bytes_received,
            "packets_parsed": self._packets_parsed,
            "parse_errors": self._parse_errors,
            "compression_count": self._compression_count,
            "bytes_dropped": self._bytes_dropped,
        }


import struct
import queue


class StreamingDemuxerError(DemuxerError):
    """Base exception for streaming demuxer errors."""

    pass


class IncompleteReadError(StreamingDemuxerError):
    """Raised when exact byte count cannot be read."""

    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(f"Incomplete read: expected {expected} bytes, got {actual}")


class StreamingDemuxerBase:
    """
    Base class for streaming demuxers.

    Key features:
    - No fixed buffer allocation
    - Header-first reading strategy
    - Exact payload reading with _recv_exact()
    - Thread-safe operation
    - Graceful partial read handling

    Based on official scrcpy demuxer design (app/src/demuxer.c).
    """

    # Socket timeout for recv operations (seconds)
    RECV_TIMEOUT = 5.0

    # Maximum packet size to prevent memory exhaustion attacks
    MAX_PACKET_SIZE = 16 * 1024 * 1024  # 16MB

    # Optimal chunk size for _recv_exact (balances syscall overhead vs memory)
    RECV_CHUNK_SIZE = 65536  # 64KB

    def __init__(
        self,
        sock: socket.socket,
        packet_queue: Queue,
        stats_callback: Optional[Callable] = None,
    ):
        """
        Initialize the streaming demuxer.

        Args:
            sock: Connected socket to read from
            packet_queue: Queue for parsed packets
            stats_callback: Optional callback for statistics updates
        """
        self._socket = sock
        self._socket.settimeout(self.RECV_TIMEOUT)
        self._packet_queue = packet_queue

        # Threading
        self._thread: Optional[threading.Thread] = None
        self._stopped = threading.Event()
        self._lock = threading.Lock()

        # Pause/Resume state (for runtime control without reconnecting)
        self._paused = False
        self._pause_event = threading.Event()
        self._pause_event.set()  # Start unpaused

        # Statistics
        self._bytes_received = 0
        self._packets_parsed = 0
        self._parse_errors = 0
        self._incomplete_reads = 0
        self._bytes_dropped = 0  # Bytes dropped while paused
        self._stats_callback = stats_callback

    def start(self) -> None:
        """Start the demuxer thread."""
        if self._thread is not None:
            logger.warning("Demuxer already started")
            return

        self._stopped.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name=self._get_thread_name(), daemon=True
        )
        self._thread.start()
        logger.info(f"{self._get_thread_name()} started")

    def stop(self) -> None:
        """Stop the demuxer and wait for thread completion."""
        if self._thread is None:
            return

        logger.info(f"Stopping {self._get_thread_name()}...")
        self._stopped.set()

        # Close socket to interrupt blocking recv
        try:
            self._socket.close()
        except Exception as e:
            logger.debug(f"Error closing socket: {e}")

        # Wait for thread to finish
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            logger.warning(f"{self._get_thread_name()} did not stop gracefully")

        self._thread = None
        logger.info(f"{self._get_thread_name()} stopped")

    def pause(self) -> None:
        """
        Pause parsing (stop CPU), but keep draining socket.

        This method is called when video/audio is disabled at runtime.
        The demuxer continues reading from socket to prevent TCP buffer
        buildup on the server, but discards packets instead of parsing.

        This ensures:
        - TCP connection stays alive
        - Server encoder doesn't block
        - Can quickly resume when needed
        """
        if self._paused:
            return

        self._paused = True
        logger.info(f"{self.__class__.__name__} paused (draining socket to prevent buildup)")

    def resume(self) -> None:
        """
        Resume reading from socket.

        This method is called when video/audio is enabled at runtime.
        """
        if not self._paused:
            return

        self._paused = False
        self._pause_event.set()  # Unblock read loop
        logger.info(f"{self.__class__.__name__} resumed")

    def _run_loop(self) -> None:
        """
        Main demuxer loop.

        Continuously reads packets from socket and places them in queue.
        Uses streaming approach: read header, get size, read exact payload.
        """
        try:
            while not self._stopped.is_set():
                # When paused: continue reading and parsing to keep stream synchronized,
                # but don't put packets in queue (just discard them)
                # This uses more CPU but ensures stream stays in sync
                if self._paused:
                    try:
                        packet = self._recv_packet()
                        if packet:
                            self._bytes_dropped += packet.header.size + 12
                            self._bytes_received += packet.header.size + 12
                    except socket.timeout:
                        continue
                    except (OSError, IncompleteReadError):
                        break
                    # Parse errors are ignored - just keep draining
                    continue

                try:
                    packet = self._recv_packet()
                    if packet is None:
                        # Packet parse failed - skip and continue
                        # Don't break the loop, just log and try next packet
                        continue

                    # Put packet in queue (blocking with timeout)
                    try:
                        self._packet_queue.put(packet, timeout=1.0)
                        self._packets_parsed += 1

                        # Optional: report statistics
                        if self._stats_callback:
                            self._stats_callback(self.get_stats())

                    except queue.Full:
                        logger.warning("Packet queue full, dropping packet")

                except socket.timeout:
                    continue

                except IncompleteReadError as e:
                    self._incomplete_reads += 1
                    logger.error(f"Incomplete read: {e}")
                    break

                except OSError as e:
                    if not self._stopped.is_set():
                        logger.error(f"Socket error: {e}")
                    break

        except Exception as e:
            logger.error(f"Demuxer loop error: {e}", exc_info=True)

        finally:
            logger.info(f"{self._get_thread_name()} loop ended")

    def _recv_exact(self, size: int) -> bytes:
        """
        Receive exactly the specified number of bytes from socket.

        This is the CORE method that replaces buffer-based reading.
        It loops until all bytes are received or connection closes.

        Args:
            size: Exact number of bytes to read

        Returns:
            bytes: Exactly 'size' bytes

        Raises:
            IncompleteReadError: If connection closes before reading all bytes
        """
        buffer = bytearray()
        remaining = size

        while remaining > 0:
            # Read in reasonable chunks
            chunk_size = min(remaining, self.RECV_CHUNK_SIZE)
            chunk = self._socket.recv(chunk_size)

            if len(chunk) == 0:
                # Connection closed
                raise IncompleteReadError(size, len(buffer))

            buffer.extend(chunk)
            remaining -= len(chunk)
            self._bytes_received += len(chunk)

        return bytes(buffer)

    def _recv_packet(self):
        """
        Receive a complete packet from socket.

        Must be implemented by subclasses.

        Returns:
            Packet object or None if connection closed
        """
        raise NotImplementedError("Subclasses must implement _recv_packet")

    def _get_thread_name(self) -> str:
        """Get thread name for logging."""
        return "StreamingDemuxer"

    def get_stats(self) -> dict:
        """Get demuxer statistics."""
        return {
            "bytes_received": self._bytes_received,
            "packets_parsed": self._packets_parsed,
            "parse_errors": self._parse_errors,
            "incomplete_reads": self._incomplete_reads,
            "bytes_dropped": self._bytes_dropped,
        }
