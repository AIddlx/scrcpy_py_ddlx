"""
能力协商协议常量定义

客户端和服务端必须使用相同的常量值。
服务端对应的常量定义在: com.genymobile.scrcpy.device.CapabilityNegotiation

协议版本: 1.0
"""

import struct
from dataclasses import dataclass
from typing import List, Tuple


# ============ 协议版本 ============
PROTOCOL_VERSION = 1

# ============ 编码器 ID ============
class VideoCodecId:
    """视频编码器 ID（4字节 ASCII）"""
    H264 = 0x68323634  # "h264"
    H265 = 0x68323635  # "h265"
    AV1 = 0x00617631   # "av1"

    @staticmethod
    def to_string(codec_id: int) -> str:
        mapping = {
            VideoCodecId.H264: "h264",
            VideoCodecId.H265: "h265",
            VideoCodecId.AV1: "av1",
        }
        return mapping.get(codec_id, f"unknown(0x{codec_id:08x})")


class AudioCodecId:
    """音频编码器 ID"""
    OPUS = 0x6f707573  # "opus"
    AAC = 0x00000003
    FLAC = 0x00000004

    @staticmethod
    def to_string(codec_id: int) -> str:
        mapping = {
            AudioCodecId.OPUS: "opus",
            AudioCodecId.AAC: "aac",
            AudioCodecId.FLAC: "flac",
        }
        return mapping.get(codec_id, f"unknown(0x{codec_id:08x})")


# ============ 编码器标志位 ============
class EncoderFlags:
    """编码器能力标志"""
    HARDWARE = 0x01  # 硬件编码器
    SOFTWARE = 0x02  # 软件编码器


# ============ 客户端配置标志位 ============
class ConfigFlags:
    """客户端配置标志"""
    AUDIO_ENABLED = 0x01
    VIDEO_ENABLED = 0x02
    CBR_MODE = 0x04
    VIDEO_FEC = 0x08
    AUDIO_FEC = 0x10


# ============ 消息格式 ============
@dataclass
class EncoderInfo:
    """编码器信息"""
    codec_id: int
    flags: int
    priority: int  # 越小优先级越高

    def is_hardware(self) -> bool:
        return bool(self.flags & EncoderFlags.HARDWARE)

    def is_software(self) -> bool:
        return bool(self.flags & EncoderFlags.SOFTWARE)


@dataclass
class DeviceCapabilities:
    """设备能力信息"""
    screen_width: int
    screen_height: int
    video_encoders: List[EncoderInfo]
    audio_encoders: List[EncoderInfo]

    @staticmethod
    def parse(data: bytes) -> 'DeviceCapabilities':
        """
        解析设备能力信息

        格式:
        - screen_width: 4 bytes (uint32, big-endian)
        - screen_height: 4 bytes (uint32, big-endian)
        - video_encoder_count: 1 byte
        - video_encoders: N * 12 bytes (codec_id:4, flags:4, priority:4)
        - audio_encoder_count: 1 byte
        - audio_encoders: M * 12 bytes
        """
        offset = 0

        # 屏幕尺寸 (8 bytes)
        screen_width, screen_height = struct.unpack('>II', data[offset:offset+8])
        offset += 8

        # 视频编码器列表
        video_encoder_count = data[offset]
        offset += 1

        video_encoders = []
        for _ in range(video_encoder_count):
            codec_id, flags, priority = struct.unpack('>III', data[offset:offset+12])
            video_encoders.append(EncoderInfo(codec_id, flags, priority))
            offset += 12

        # 音频编码器列表
        audio_encoder_count = data[offset]
        offset += 1

        audio_encoders = []
        for _ in range(audio_encoder_count):
            codec_id, flags, priority = struct.unpack('>III', data[offset:offset+12])
            audio_encoders.append(EncoderInfo(codec_id, flags, priority))
            offset += 12

        return DeviceCapabilities(
            screen_width=screen_width,
            screen_height=screen_height,
            video_encoders=video_encoders,
            audio_encoders=audio_encoders
        )


@dataclass
class ClientConfiguration:
    """客户端配置"""
    video_codec_id: int
    audio_codec_id: int
    video_bitrate: int
    audio_bitrate: int
    max_fps: int
    config_flags: int
    i_frame_interval: float  # IEEE 754 float

    def serialize(self) -> bytes:
        """
        序列化客户端配置

        格式:
        - video_codec_id: 4 bytes (uint32, big-endian)
        - audio_codec_id: 4 bytes (uint32, big-endian)
        - video_bitrate: 4 bytes (uint32, big-endian)
        - audio_bitrate: 4 bytes (uint32, big-endian)
        - max_fps: 4 bytes (uint32, big-endian)
        - config_flags: 4 bytes (uint32, big-endian)
        - i_frame_interval: 4 bytes (IEEE 754 float, big-endian)
        """
        return struct.pack(
            '>IIIIIIIf',
            self.video_codec_id,
            self.audio_codec_id,
            self.video_bitrate,
            self.audio_bitrate,
            self.max_fps,
            self.config_flags,
            0,  # reserved
            self.i_frame_interval
        )


def select_best_video_codec(capabilities: DeviceCapabilities) -> int:
    """
    选择最佳视频编码器

    优先级: AV1 > H.265 > H.264
    优先选择硬件编码器
    """
    # 按优先级排序的编码器列表
    priority_order = [
        VideoCodecId.AV1,
        VideoCodecId.H265,
        VideoCodecId.H264,
    ]

    for preferred_codec in priority_order:
        # 优先找硬件编码器
        for encoder in capabilities.video_encoders:
            if encoder.codec_id == preferred_codec and encoder.is_hardware():
                return preferred_codec
        # 其次找软件编码器
        for encoder in capabilities.video_encoders:
            if encoder.codec_id == preferred_codec:
                return preferred_codec

    # 默认返回 H.264
    return VideoCodecId.H264


def select_best_audio_codec(capabilities: DeviceCapabilities) -> int:
    """
    选择最佳音频编码器

    优先级: OPUS > AAC > FLAC
    """
    priority_order = [
        AudioCodecId.OPUS,
        AudioCodecId.AAC,
        AudioCodecId.FLAC,
    ]

    for preferred_codec in priority_order:
        for encoder in capabilities.audio_encoders:
            if encoder.codec_id == preferred_codec:
                return preferred_codec

    return AudioCodecId.OPUS


__all__ = [
    'PROTOCOL_VERSION',
    'VideoCodecId',
    'AudioCodecId',
    'EncoderFlags',
    'ConfigFlags',
    'EncoderInfo',
    'DeviceCapabilities',
    'ClientConfiguration',
    'select_best_video_codec',
    'select_best_audio_codec',
]
