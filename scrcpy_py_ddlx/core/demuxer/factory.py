"""
Factory functions for creating demuxer instances.

This module provides convenience functions for creating demuxers
with appropriate packet queues.
"""

import socket
from queue import Queue
from typing import Callable, Optional, TYPE_CHECKING

from .base import DEFAULT_PACKET_QUEUE_SIZE
from .video import VideoDemuxer, StreamingVideoDemuxer

# Use TYPE_CHECKING for type hints to avoid circular import
if TYPE_CHECKING:
    from scrcpy_py_ddlx.core.audio.demuxer import AudioDemuxer, StreamingAudioDemuxer


# =============================================================================
# CONVENIENCE FUNCTIONS FOR BUFFER-BASED DEMUXER
# =============================================================================

def create_video_demuxer(
    sock: socket.socket,
    codec_id: int,
    packet_queue_size: int = DEFAULT_PACKET_QUEUE_SIZE
) -> tuple[VideoDemuxer, Queue]:
    """
    Convenience function to create a video demuxer with packet queue.

    Args:
        sock: Video socket
        codec_id: Video codec ID
        packet_queue_size: Size of packet queue

    Returns:
        Tuple of (VideoDemuxer, Queue)
    """
    packet_queue = Queue(maxsize=packet_queue_size)
    demuxer = VideoDemuxer(sock, packet_queue, codec_id)
    return demuxer, packet_queue


def create_audio_demuxer(
    sock: socket.socket,
    audio_codec: int = 1,  # OPUS
    packet_queue_size: int = DEFAULT_PACKET_QUEUE_SIZE
):
    """
    Convenience function to create an audio demuxer with packet queue.

    Args:
        sock: Audio socket
        audio_codec: Audio codec type
        packet_queue_size: Size of packet queue

    Returns:
        Tuple of (AudioDemuxer, Queue)
    """
    # Import here to avoid circular dependency
    from scrcpy_py_ddlx.core.audio.demuxer import AudioDemuxer

    packet_queue = Queue(maxsize=packet_queue_size)
    demuxer = AudioDemuxer(sock, packet_queue, audio_codec)
    return demuxer, packet_queue


# =============================================================================
# CONVENIENCE FUNCTIONS FOR STREAMING DEMUXER
# =============================================================================

def create_streaming_video_demuxer(
    sock: socket.socket,
    codec_id: int,
    packet_queue_size: int = DEFAULT_PACKET_QUEUE_SIZE
) -> tuple[StreamingVideoDemuxer, Queue]:
    """
    Create a streaming video demuxer with packet queue.

    Args:
        sock: Video socket
        codec_id: Video codec ID
        packet_queue_size: Size of packet queue (default: 1 for minimal latency)

    Returns:
        Tuple of (StreamingVideoDemuxer, Queue)
    """
    packet_queue = Queue(maxsize=packet_queue_size)
    demuxer = StreamingVideoDemuxer(sock, packet_queue, codec_id)
    return demuxer, packet_queue


def create_streaming_audio_demuxer(
    sock: socket.socket,
    audio_codec: int = 1,  # OPUS
    packet_queue_size: int = DEFAULT_PACKET_QUEUE_SIZE,
    stats_callback: Optional[Callable] = None
):
    """
    Create a streaming audio demuxer with packet queue.

    Args:
        sock: Audio socket
        audio_codec: Audio codec type
        packet_queue_size: Size of packet queue
        stats_callback: Optional statistics callback

    Returns:
        Tuple of (StreamingAudioDemuxer, Queue)
    """
    # Import here to avoid circular dependency
    from scrcpy_py_ddlx.core.audio.demuxer import StreamingAudioDemuxer

    packet_queue = Queue(maxsize=packet_queue_size)
    demuxer = StreamingAudioDemuxer(
        sock, packet_queue, audio_codec,
        stats_callback=stats_callback
    )
    return demuxer, packet_queue
