"""
Unified file operations interface.

Provides file transfer functionality with different implementations:
- ADB mode: Uses adb push/pull/shell commands
- Network mode: Uses independent file socket channel
"""
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FileInfo:
    """File information."""
    name: str
    type: str  # "file" or "directory"
    size: int
    mtime: int


class FileOpsError(Exception):
    """File operation error."""
    pass


class FileOps:
    """
    ADB-based file operations.

    Uses adb push/pull/shell commands for file transfer.
    Simple and reliable for USB/tcpip ADB connections.
    """

    def __init__(self, adb_manager, device_serial: str):
        """
        Initialize ADB file operations.

        Args:
            adb_manager: ADBManager instance
            device_serial: Device serial number
        """
        self._adb = adb_manager
        self._serial = device_serial

    def list_dir(self, path: str) -> List[FileInfo]:
        """
        List directory contents using adb shell.

        Args:
            path: Directory path on device

        Returns:
            List of FileInfo objects
        """
        # Use ls -laL to follow symlinks and get detailed listing
        result = self._adb._execute(
            ["shell", "ls", "-laL", path],
            device_serial=self._serial,
            timeout=10.0
        )

        if result.returncode != 0:
            raise FileOpsError(f"Failed to list directory: {result.stderr}")

        return self._parse_ls_output(result.stdout, path)

    def _parse_ls_output(self, output: str, base_path: str) -> List[FileInfo]:
        """Parse ls -la output into FileInfo list."""
        entries = []

        for line in output.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith('total '):
                continue

            # Parse ls -la format: drwxrwxr-x 2 root root 4096 2024-01-01 12:00 dirname
            # or: -rw-rw-r-- 1 root root 12345 2024-01-01 12:00 filename
            match = re.match(
                r'^([dldrwxst-]+)\s+\d+\s+\S+\s+\S+\s+(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(.+)$',
                line
            )
            if match:
                perms, size, date, time_str, name = match.groups()

                # Skip . and ..
                if name in ('.', '..'):
                    continue

                # Determine type
                file_type = "directory" if perms.startswith('d') else "file"

                # Parse mtime (simplified: use current time as we don't have exact timestamp)
                import time
                try:
                    mtime = int(time.mktime(time.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M")))
                except ValueError:
                    mtime = 0

                entries.append(FileInfo(
                    name=name,
                    type=file_type,
                    size=int(size),
                    mtime=mtime
                ))

        return entries

    def pull_file(self, device_path: str, local_path: str,
                  on_progress: Optional[Callable[[int, int], None]] = None):
        """
        Download a file from the device using adb pull.

        Args:
            device_path: File path on device
            local_path: Local file path to save
            on_progress: Progress callback (received_bytes, total_bytes) - not supported for ADB
        """
        # Ensure parent directory exists
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)

        result = self._adb._execute(
            ["pull", device_path, local_path],
            device_serial=self._serial,
            timeout=120.0  # 2 minutes for large files
        )

        if result.returncode != 0:
            raise FileOpsError(f"Failed to pull file: {result.stderr}")

        logger.info(f"Pulled: {device_path} -> {local_path}")

    def push_file(self, local_path: str, device_path: str,
                  on_progress: Optional[Callable[[int, int], None]] = None):
        """
        Upload a file to the device using adb push.

        Args:
            local_path: Local file path
            device_path: Target path on device
            on_progress: Progress callback (sent_bytes, total_bytes) - not supported for ADB
        """
        if not os.path.exists(local_path):
            raise FileOpsError(f"Local file not found: {local_path}")

        result = self._adb._execute(
            ["push", local_path, device_path],
            device_serial=self._serial,
            timeout=120.0  # 2 minutes for large files
        )

        if result.returncode != 0:
            raise FileOpsError(f"Failed to push file: {result.stderr}")

        logger.info(f"Pushed: {local_path} -> {device_path}")

    def delete(self, device_path: str) -> bool:
        """
        Delete a file or directory.

        Args:
            device_path: Path on device

        Returns:
            True if deleted successfully
        """
        # Use rm -rf for both files and directories (simpler and more reliable)
        result = self._adb._execute(
            ["shell", "rm", "-rf", device_path],
            device_serial=self._serial,
            timeout=10.0
        )
        return result.returncode == 0

    def mkdir(self, device_path: str) -> bool:
        """
        Create a directory.

        Args:
            device_path: Directory path to create

        Returns:
            True if created successfully
        """
        result = self._adb._execute(
            ["shell", "mkdir", "-p", device_path],
            device_serial=self._serial,
            timeout=5.0
        )
        return result.returncode == 0

    def stat(self, device_path: str) -> Optional[dict]:
        """
        Get file information.

        Args:
            device_path: File or directory path

        Returns:
            File info dict or None if not exists
        """
        # Use a single shell command with proper quoting
        result = self._adb._execute(
            ["shell", f"stat -c '%F %s %Y' '{device_path}' 2>/dev/null || echo 'NOT_FOUND'"],
            device_serial=self._serial,
            timeout=5.0
        )

        if result.returncode != 0 or "NOT_FOUND" in result.stdout:
            return None

        # Parse output: "regular file 12345 1704067200"
        output = result.stdout.strip()
        if not output:
            return None

        parts = output.split()
        if len(parts) >= 3:
            file_type = "directory" if "directory" in parts[0].lower() else "file"
            size = int(parts[-2])
            mtime = int(parts[-1])

            return {
                "path": device_path,
                "exists": True,
                "type": file_type,
                "size": size,
                "mtime": mtime
            }

        return None
