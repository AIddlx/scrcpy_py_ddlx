"""File channel command constants."""
from enum import IntEnum


class FileCommand(IntEnum):
    """File channel command types."""
    # Client -> Server commands
    LIST = 1           # List directory
    PULL = 3           # Download file
    PUSH = 5           # Start upload
    PUSH_DATA = 6      # Upload data chunk
    DELETE = 8         # Delete file/directory
    MKDIR = 9          # Create directory
    STAT = 10          # Get file info

    # Server -> Client responses
    LIST_RESP = 2      # Directory list response
    PULL_DATA = 4      # File data chunk
    PUSH_ACK = 7       # Upload acknowledgment
    STAT_RESP = 11     # File info response
    ERROR = 255        # Error response


# Chunk size for file transfer (must match Java side)
CHUNK_SIZE = 64 * 1024  # 64KB
