"""
PacketTee: 分发数据包到多个消费者

用于录制功能：将 Demuxer 输出的包同时发送给 Decoder 和 Recorder。

设计思路（正确方案）：
    录制应该接收编码后的包（VideoPacket），而不是解码后的帧。
    这避免了重新编码，性能最优。

    数据流：
    VideoDemuxer ──→ VideoPacket ──→ VideoDecoder → Preview
                         ↓
                    Recorder (编码后直接写入)

使用方式：
    # Demuxer 已经支持 add_packet_sink
    demuxer.add_packet_sink(recorder_queue)

    # Demuxer 会将包同时放入主队列和所有 sink 队列
"""

import logging
import queue
import threading
from typing import List, Optional, Any
from queue import Queue

logger = logging.getLogger(__name__)


class PacketTee:
    """
    包分发器：将数据包同时发送到多个队列。

    线程安全，支持动态添加/移除 sink。

    注意：此类现在主要用于兼容旧代码。
    新代码应该使用 StreamingDemuxerBase 的 add_packet_sink 方法。
    """

    def __init__(self, primary_queue: Queue):
        """
        初始化分发器。

        Args:
            primary_queue: 主队列（通常是 Decoder 的队列）
        """
        self._primary_queue = primary_queue
        self._secondary_queues: List[Queue] = []
        self._lock = threading.Lock()

    def add_sink(self, queue: Queue) -> None:
        """添加一个 sink 队列。"""
        with self._lock:
            if queue not in self._secondary_queues:
                self._secondary_queues.append(queue)
                logger.debug(f"PacketTee: added sink, total={len(self._secondary_queues)}")

    def remove_sink(self, queue: Queue) -> None:
        """移除一个 sink 队列。"""
        with self._lock:
            if queue in self._secondary_queues:
                self._secondary_queues.remove(queue)
                logger.debug(f"PacketTee: removed sink, total={len(self._secondary_queues)}")

    def clear_sinks(self) -> None:
        """清除所有 sink 队列。"""
        with self._lock:
            self._secondary_queues.clear()
            logger.debug("PacketTee: cleared all sinks")

    def put(self, packet: Any, timeout: Optional[float] = None) -> bool:
        """
        将包放入主队列和所有 sink 队列。

        Args:
            packet: 数据包
            timeout: 超时时间（仅对主队列有效）

        Returns:
            True 如果成功放入主队列
        """
        # 首先放入主队列（阻塞）
        try:
            self._primary_queue.put(packet, timeout=timeout, block=timeout is not None)
        except queue.Full:
            logger.warning("PacketTee: primary queue full")
            return False

        # 然后复制到所有 sink 队列（非阻塞）
        with self._lock:
            for q in self._secondary_queues:
                try:
                    q.put_nowait(packet)
                except queue.Full:
                    # sink 队列满了就丢弃（不影响主流程）
                    pass

        return True

    def put_nowait(self, packet: Any) -> bool:
        """非阻塞方式放入包。"""
        return self.put(packet, timeout=0)

    @property
    def sink_count(self) -> int:
        """返回 sink 数量。"""
        with self._lock:
            return len(self._secondary_queues)


class RecordingManager:
    """
    录制管理器：管理动态录制的开始/停止。

    正确的录制方法：
    1. 使用 Demuxer 的 add_packet_sink 添加录制队列
    2. 从队列接收编码后的包（VideoPacket）
    3. 将包直接传给 Recorder 写入文件

    这样无需重新编码，性能最优。
    """

    def __init__(self, client):
        """
        初始化录制管理器。

        Args:
            client: ScrcpyClient 实例
        """
        self._client = client
        self._recorder = None
        self._recorder_queue: Optional[Queue] = None
        self._forwarder_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def start_recording(
        self,
        filename: str,
        format: str = "mp4",
        video: bool = True,
        audio: bool = True
    ) -> bool:
        """
        开始录制。

        Args:
            filename: 输出文件名
            format: 容器格式 (mp4, mkv)
            video: 是否录制视频
            audio: 是否录制音频

        Returns:
            True 如果成功开始
        """
        from scrcpy_py_ddlx.core.av_player import Recorder

        with self._lock:
            if self._recorder is not None:
                logger.warning("Recording already in progress")
                return False

            try:
                # 创建 Recorder
                self._recorder = Recorder(
                    filename=filename,
                    format=format,
                    video=video,
                    audio=audio
                )

                # 初始化视频流
                if video and self._client.state.device_size:
                    width, height = self._client.state.device_size
                    codec_id = self._client.state.codec_id

                    video_ctx = {
                        "codec_type": "video",
                        "width": width,
                        "height": height,
                        "codec_id": codec_id
                    }
                    self._recorder.open(video_ctx)

                # 初始化音频流
                if audio:
                    audio_ctx = {
                        "codec_type": "audio",
                        "sample_rate": 48000,
                        "channels": 2
                    }
                    self._recorder.open(audio_ctx)

                # 创建录制队列
                self._recorder_queue = Queue(maxsize=300)  # ~10秒缓冲
                self._stop_event.clear()

                # 启动 Recorder
                self._recorder.start()

                # 添加 sink 到 Demuxer（使用新的 add_packet_sink 方法）
                self._setup_sinks(video, audio)

                # 启动包转发线程
                self._start_forwarder()

                logger.info(f"Recording started: {filename}")
                return True

            except Exception as e:
                logger.error(f"Failed to start recording: {e}", exc_info=True)
                self._cleanup()
                return False

    def _setup_sinks(self, video: bool, audio: bool) -> None:
        """设置包分发到录制队列。"""
        # 添加视频 sink
        if video and self._client._video_demuxer is not None:
            demuxer = self._client._video_demuxer
            if hasattr(demuxer, 'add_packet_sink'):
                demuxer.add_packet_sink(self._recorder_queue)
                logger.info("Video sink added to demuxer")

                # CRITICAL: Send cached config data to new sink
                # When recording starts mid-stream, the config packet was already processed
                # We need to send the cached config to the recorder so it can set extradata
                if hasattr(demuxer, '_config_data') and demuxer._config_data is not None:
                    # Create a synthetic config packet from cached config
                    from scrcpy_py_ddlx.core.stream import VideoPacket, PacketHeader
                    from scrcpy_py_ddlx.core.protocol import CodecId

                    config_packet = VideoPacket(
                        header=PacketHeader(
                            pts_flags=0,
                            pts=0,
                            size=len(demuxer._config_data),
                            is_config=True,
                            is_key_frame=False
                        ),
                        data=demuxer._config_data,
                        codec_id=getattr(demuxer, '_codec_id', CodecId.H265)
                    )
                    try:
                        self._recorder_queue.put_nowait(config_packet)
                        logger.info(f"Sent cached config to recorder: {len(demuxer._config_data)} bytes")
                    except queue.Full:
                        logger.warning("Recorder queue full, could not send cached config")
            else:
                logger.warning("VideoDemuxer does not support add_packet_sink")

        # 添加音频 sink（如果启用）
        if audio and hasattr(self._client, '_audio_demuxer') and self._client._audio_demuxer is not None:
            demuxer = self._client._audio_demuxer
            if hasattr(demuxer, 'add_packet_sink'):
                demuxer.add_packet_sink(self._recorder_queue)
                logger.info("Audio sink added to demuxer")
            else:
                logger.warning("AudioDemuxer does not support add_packet_sink")

    def _start_forwarder(self) -> None:
        """启动包转发线程（从录制队列转发到 Recorder）。"""
        def forwarder():
            while not self._stop_event.is_set():
                try:
                    packet = self._recorder_queue.get(timeout=0.1)
                    if self._recorder is not None:
                        self._recorder.push(packet)
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"Forwarder error: {e}")
                    break

        self._forwarder_thread = threading.Thread(
            target=forwarder,
            name="RecordingForwarder",
            daemon=True
        )
        self._forwarder_thread.start()

    def stop_recording(self) -> Optional[str]:
        """
        停止录制。

        Returns:
            录制文件名，如果失败返回 None
        """
        with self._lock:
            if self._recorder is None:
                return None

            try:
                # 停止转发线程
                self._stop_event.set()
                if self._forwarder_thread is not None:
                    self._forwarder_thread.join(timeout=2.0)
                    self._forwarder_thread = None

                # 从 Demuxer 移除 sink
                self._remove_sinks()

                # 停止 Recorder
                self._recorder.stop()
                filename = self._recorder._filename

                logger.info(f"Recording stopped: {filename}")
                logger.info(f"  Video packets: {self._recorder._video_packets_written}")
                logger.info(f"  Audio packets: {self._recorder._audio_packets_written}")

                return filename

            except Exception as e:
                logger.error(f"Error stopping recording: {e}")
                return None

            finally:
                self._cleanup()

    def _remove_sinks(self) -> None:
        """从 Demuxer 移除录制队列。"""
        if self._recorder_queue is None:
            return

        # 移除视频 sink
        if self._client._video_demuxer is not None:
            demuxer = self._client._video_demuxer
            if hasattr(demuxer, 'remove_packet_sink'):
                demuxer.remove_packet_sink(self._recorder_queue)
                logger.info("Video sink removed from demuxer")

        # 移除音频 sink
        if hasattr(self._client, '_audio_demuxer') and self._client._audio_demuxer is not None:
            demuxer = self._client._audio_demuxer
            if hasattr(demuxer, 'remove_packet_sink'):
                demuxer.remove_packet_sink(self._recorder_queue)
                logger.info("Audio sink removed from demuxer")

    def _cleanup(self) -> None:
        """清理资源。"""
        self._recorder = None
        self._recorder_queue = None
        self._forwarder_thread = None

    def is_recording(self) -> bool:
        """检查是否正在录制。"""
        with self._lock:
            return self._recorder is not None and self._recorder._running
