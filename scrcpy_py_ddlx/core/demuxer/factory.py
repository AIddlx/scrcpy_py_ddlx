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


# =============================================================================
# MODE-AWARE FACTORY FUNCTIONS (NEW)
# =============================================================================

def create_video_demuxer_for_mode(
    mode: str,
    sock: socket.socket,
    codec_id: int,
    packet_queue_size: int = DEFAULT_PACKET_QUEUE_SIZE,
    **kwargs
) -> tuple:
    """
    Create appropriate video demuxer based on connection mode.

    This is the recommended factory function for creating video demuxers.
    It automatically selects the correct demuxer type based on the
    connection mode.

    Args:
        mode: Connection mode
            - 'adb': ADB tunnel mode (TCP via ADB forward)
            - 'tcp': Network TCP mode
            - 'udp': Network UDP mode (uses UdpVideoDemuxer)
        sock: Socket (TCP or UDP depending on mode)
        codec_id: Video codec ID (H264/H265/AV1)
        packet_queue_size: Size of packet queue (default: 1 for minimal latency)
        **kwargs: Additional arguments for UDP mode:
            - control_channel: Control channel for PLI requests
            - fec_decoder: FEC decoder instance
            - pli_enabled: Enable PLI requests (default: True)
            - pli_threshold: Consecutive drops before PLI (default: 10)
            - pli_cooldown: Seconds between PLI requests (default: 1.0)
            - stats_callback: Statistics callback

    Returns:
        Tuple of (demuxer, packet_queue)

    Examples:
        # ADB tunnel mode
        demuxer, queue = create_video_demuxer_for_mode('adb', tcp_sock, codec_id)

        # UDP network mode with PLI
        demuxer, queue = create_video_demuxer_for_mode(
            'udp', udp_sock, codec_id,
            control_channel=control_socket,
            pli_enabled=True,
            pli_threshold=10
        )
    """
    packet_queue = Queue(maxsize=packet_queue_size)

    if mode == 'udp':
        # UDP mode: use specialized UdpVideoDemuxer
        from .udp_video import UdpVideoDemuxer

        demuxer = UdpVideoDemuxer(
            udp_socket=sock,
            packet_queue=packet_queue,
            codec_id=codec_id,
            control_channel=kwargs.get('control_channel'),
            fec_decoder=kwargs.get('fec_decoder'),
            pli_enabled=kwargs.get('pli_enabled', True),
            pli_threshold=kwargs.get('pli_threshold', 10),
            pli_cooldown=kwargs.get('pli_cooldown', 1.0),
            stats_callback=kwargs.get('stats_callback'),
        )
    else:
        # ADB and TCP modes: use streaming demuxer
        demuxer = StreamingVideoDemuxer(sock, packet_queue, codec_id)

    return demuxer, packet_queue


def create_audio_demuxer_for_mode(
    mode: str,
    sock: socket.socket,
    audio_codec: int = 1,  # OPUS
    packet_queue_size: int = DEFAULT_PACKET_QUEUE_SIZE,
    **kwargs
) -> tuple:
    """
    Create appropriate audio demuxer based on connection mode.

    Args:
        mode: Connection mode ('adb', 'tcp', 'udp')
        sock: Socket (TCP or UDP)
        audio_codec: Audio codec ID (default: OPUS)
        packet_queue_size: Size of packet queue
        **kwargs: Additional arguments (for future UDP audio support)

    Returns:
        Tuple of (demuxer, packet_queue)
    """
    packet_queue = Queue(maxsize=packet_queue_size)

    if mode == 'udp':
        # UDP mode: use specialized UdpAudioDemuxer
        from .udp_audio import UdpAudioDemuxer

        demuxer = UdpAudioDemuxer(
            sock,
            packet_queue,
            codec_id=audio_codec,
            fec_decoder=kwargs.get('fec_decoder'),
            stats_callback=kwargs.get('stats_callback')
        )
    else:
        # ADB and TCP modes: use streaming demuxer
        from scrcpy_py_ddlx.core.audio.demuxer import StreamingAudioDemuxer

        demuxer = StreamingAudioDemuxer(
            sock, packet_queue, audio_codec,
            stats_callback=kwargs.get('stats_callback')
        )

    return demuxer, packet_queue
