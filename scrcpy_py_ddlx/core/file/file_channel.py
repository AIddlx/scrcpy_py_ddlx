"""Independent TCP file channel client."""
import json
import logging
import socket
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, List
from queue import Queue

from .file_commands import FileCommand, CHUNK_SIZE

logger = logging.getLogger(__name__)


@dataclass
class FileInfo:
    """File information."""
    name: str
    type: str  # "file" or "directory"
    size: int
    mtime: int


class FileChannelError(Exception):
    """File channel error."""
    pass


class FileChannel:
    """
    Independent TCP file channel client.

    Connects to the file server on the Android device and provides
    file operations like list, pull, push, delete, mkdir, stat.

    Supports two connection modes:
    1. connect() - Active connection to server (legacy mode)
    2. set_connected_socket() - Use pre-established socket (network mode with 4th socket)
    """

    def __init__(self):
        self._socket: Optional[socket.socket] = None
        self._connected = False
        self._lock = threading.Lock()
        self._response_queue = Queue()
        self._reader_thread: Optional[threading.Thread] = None

    def connect(self, host: str, port: int, session_id: int) -> bool:
        """
        Connect to the file server.

        Args:
            host: Server host address
            port: Server port
            session_id: Session ID for authentication

        Returns:
            True if connected successfully
        """
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(30.0)
            self._socket.connect((host, port))
            self._connected = True

            # Send session_id for authentication
            self._socket.sendall(struct.pack(">I", session_id))

            # Start reader thread
            self._start_reader_thread()

            logger.info(f"FileChannel connected to {host}:{port}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect file channel: {e}")
            self._connected = False
            return False

    def set_connected_socket(self, sock: socket.socket) -> None:
        """
        Set a pre-connected socket (for network mode with 4th socket).

        Args:
            sock: Already connected socket
        """
        self._socket = sock
        self._connected = True

        # Start reader thread
        self._start_reader_thread()

        logger.info("FileChannel using pre-connected socket")

    def _start_reader_thread(self):
        """Start the reader thread."""
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            daemon=True,
            name="FileChannelReader"
        )
        self._reader_thread.start()

    def close(self):
        """Close the connection."""
        self._connected = False

        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        logger.info("FileChannel closed")

    def is_connected(self) -> bool:
        """Check if the channel is connected."""
        return self._connected and self._socket is not None

    # === File operations ===

    def list_dir(self, path: str, timeout: float = 10.0) -> List[FileInfo]:
        """
        List directory contents.

        Args:
            path: Directory path on device
            timeout: Response timeout in seconds

        Returns:
            List of FileInfo objects

        Raises:
            FileChannelError: If operation fails
        """
        with self._lock:
            self._send_command(FileCommand.LIST, path.encode('utf-8'))

        response = self._response_queue.get(timeout=timeout)
        self._check_error(response)

        data = json.loads(response['data'].decode('utf-8'))
        return [
            FileInfo(
                name=e['name'],
                type=e['type'],
                size=e['size'],
                mtime=e['mtime']
            )
            for e in data.get('entries', [])
        ]

    def pull_file(self, device_path: str, local_path: str,
                  on_progress: Optional[Callable[[int, int], None]] = None,
                  timeout: float = 60.0):
        """
        Download a file from the device.

        Args:
            device_path: File path on device
            local_path: Local file path to save
            on_progress: Progress callback (received_bytes, total_bytes)
            timeout: Response timeout in seconds

        Raises:
            FileChannelError: If operation fails
        """
        with self._lock:
            self._send_command(FileCommand.PULL, device_path.encode('utf-8'))

        total_size = 0
        received = 0

        with open(local_path, 'wb') as f:
            while True:
                response = self._response_queue.get(timeout=timeout)
                self._check_error(response)

                if response['cmd'] == FileCommand.PULL_DATA:
                    chunk_id, total, data = self._parse_pull_data(response['data'])
                    total_size = total
                    f.write(data)
                    received += len(data)

                    if on_progress:
                        on_progress(received, total_size)

                    # Check if complete
                    if received >= total_size:
                        break

        logger.info(f"Pull complete: {device_path} -> {local_path} ({received} bytes)")

    def push_file(self, local_path: str, device_path: str,
                  on_progress: Optional[Callable[[int, int], None]] = None,
                  timeout: float = 30.0):
        """
        Upload a file to the device.

        Args:
            local_path: Local file path
            device_path: Target path on device
            on_progress: Progress callback (sent_bytes, total_bytes)
            timeout: Response timeout in seconds

        Raises:
            FileChannelError: If operation fails
        """
        path = Path(local_path)
        if not path.exists():
            raise FileChannelError(f"Local file not found: {local_path}")

        total_size = path.stat().st_size

        # Send PUSH start frame
        with self._lock:
            path_bytes = device_path.encode('utf-8')
            header = struct.pack(">QH", total_size, len(path_bytes)) + path_bytes
            self._send_command(FileCommand.PUSH, header)

        # Wait for ACK
        ack = self._response_queue.get(timeout=timeout)
        self._check_error(ack)

        # Send data chunks
        sent = 0
        chunk_id = 0

        with open(local_path, 'rb') as f:
            while sent < total_size:
                data = f.read(CHUNK_SIZE)
                if not data:
                    break

                frame = struct.pack(">I", chunk_id) + data
                with self._lock:
                    self._send_command(FileCommand.PUSH_DATA, frame)

                # Wait for ACK
                ack = self._response_queue.get(timeout=timeout)
                self._check_error(ack)

                sent += len(data)
                chunk_id += 1

                if on_progress:
                    on_progress(sent, total_size)

        logger.info(f"Push complete: {local_path} -> {device_path} ({sent} bytes)")

    def delete(self, device_path: str, timeout: float = 30.0) -> bool:
        """
        Delete a file or directory.

        Args:
            device_path: Path on device
            timeout: Response timeout in seconds

        Returns:
            True if deleted successfully

        Raises:
            FileChannelError: If operation fails
        """
        with self._lock:
            self._send_command(FileCommand.DELETE, device_path.encode('utf-8'))

        response = self._response_queue.get(timeout=timeout)
        self._check_error(response)

        data = json.loads(response['data'].decode('utf-8'))
        return data.get('success', False)

    def mkdir(self, device_path: str, timeout: float = 30.0) -> bool:
        """
        Create a directory.

        Args:
            device_path: Path on device
            timeout: Response timeout in seconds

        Returns:
            True if created successfully

        Raises:
            FileChannelError: If operation fails
        """
        with self._lock:
            self._send_command(FileCommand.MKDIR, device_path.encode('utf-8'))

        response = self._response_queue.get(timeout=timeout)
        self._check_error(response)

        data = json.loads(response['data'].decode('utf-8'))
        return data.get('success', False)

    def stat(self, device_path: str, timeout: float = 30.0) -> Optional[dict]:
        """
        Get file information.

        Args:
            device_path: Path on device
            timeout: Response timeout in seconds

        Returns:
            File info dict or None if not exists

        Raises:
            FileChannelError: If operation fails
        """
        with self._lock:
            self._send_command(FileCommand.STAT, device_path.encode('utf-8'))

        response = self._response_queue.get(timeout=timeout)
        self._check_error(response)

        data = json.loads(response['data'].decode('utf-8'))
        return data if data.get('exists') else None

    # === Internal methods ===

    def _send_command(self, cmd: int, data: bytes):
        """Send a command frame."""
        if not self._connected:
            raise FileChannelError("Not connected")

        frame = struct.pack(">BI", cmd, len(data)) + data
        self._socket.sendall(frame)

    def _read_loop(self):
        """Reader thread loop."""
        try:
            while self._connected:
                # Read frame header: [cmd:1B][length:4B]
                header = self._recv_exactly(5)
                cmd, length = struct.unpack(">BI", header)

                # Read payload
                data = self._recv_exactly(length) if length > 0 else b''

                self._response_queue.put({
                    'cmd': cmd,
                    'data': data
                })

        except Exception as e:
            if self._connected:
                logger.error(f"File channel read error: {e}")
                self._response_queue.put({
                    'cmd': FileCommand.ERROR,
                    'data': str(e).encode('utf-8')
                })

    def _recv_exactly(self, n: int) -> bytes:
        """Receive exactly n bytes."""
        data = b''
        while len(data) < n:
            chunk = self._socket.recv(n - len(data))
            if not chunk:
                raise ConnectionError("Connection closed")
            data += chunk
        return data

    def _parse_pull_data(self, data: bytes) -> tuple:
        """Parse PULL_DATA frame."""
        chunk_id, total = struct.unpack(">IQ", data[:12])
        return chunk_id, total, data[12:]

    def _check_error(self, response: dict):
        """Check if response is an error and raise exception."""
        if response['cmd'] == FileCommand.ERROR:
            message = response['data'].decode('utf-8')
            raise FileChannelError(message)
