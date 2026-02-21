#!/usr/bin/env python3
"""
Android 设备硬件编码器能力查询脚本

支持不同 Android 版本:
- Android 8.0 及以下: media.codec (OMX)
- Android 9-11: media.player + media.codec 并存
- Android 10+: media.player (C2 架构)

用法:
    python query_android_encoders.py
    python query_android_encoders.py --json
    python query_android_encoders.py --save encoders.json
"""

import subprocess
import re
import json
import sys
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional
from enum import Enum


class CodecType(Enum):
    H264 = "h264"
    H265 = "h265"
    AV1 = "av1"
    VP8 = "vp8"
    VP9 = "vp9"


class CodecDirection(Enum):
    ENCODER = "encoder"
    DECODER = "decoder"


@dataclass
class CodecInfo:
    """编码器/解码器信息"""
    name: str           # 编码器名称, e.g. "c2.qti.hevc.encoder"
    alias: str          # 别名, e.g. "OMX.qcom.video.encoder.hevc"
    codec_type: str     # 编码类型: h264, h265, av1
    direction: str      # encoder 或 decoder
    is_hardware: bool   # 是否硬件加速
    vendor: bool        # 是否厂商实现
    software_only: bool # 是否纯软件
    profile_levels: List[str] = None  # 支持的配置级别

    def __post_init__(self):
        if self.profile_levels is None:
            self.profile_levels = []


@dataclass
class DeviceEncoderCapabilities:
    """设备编码能力汇总"""
    android_version: str
    device_model: str
    video_encoders: Dict[str, Dict]  # {h264: {hardware: [...], software: [...]}}
    video_decoders: Dict[str, Dict]
    audio_encoders: Dict[str, List[str]]

    def get_best_video_encoder(self) -> Optional[str]:
        """获取最佳视频编码器 (优先级: H.265硬件 > H.264硬件 > 软件编码)"""
        for codec in ['h265', 'h264']:
            if codec in self.video_encoders:
                hw = self.video_encoders[codec].get('hardware', [])
                if hw:
                    return codec
        # 回退到软件编码
        for codec in ['h265', 'h264']:
            if codec in self.video_encoders:
                sw = self.video_encoders[codec].get('software', [])
                if sw:
                    return codec
        return None

    def has_hardware_encoder(self, codec_type: str) -> bool:
        """检查是否有指定类型的硬件编码器"""
        codec_type = codec_type.lower().replace('.', '')
        if codec_type in self.video_encoders:
            return len(self.video_encoders[codec_type].get('hardware', [])) > 0
        return False


def run_adb_command(cmd: str, timeout: int = 15) -> str:
    """Execute ADB command and return output"""
    try:
        # Use list format for better compatibility
        result = subprocess.run(
            ["adb", "shell", cmd],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        print(f"[WARN] Command timed out: {cmd[:50]}...")
        return ""
    except FileNotFoundError:
        print("[ERROR] 'adb' not found in PATH")
        return ""
    except Exception as e:
        print(f"[ERROR] ADB command failed: {e}")
        return ""


def get_android_version() -> str:
    """获取 Android 版本"""
    output = run_adb_command("getprop ro.build.version.release")
    return output.strip() or "unknown"


def get_device_model() -> str:
    """获取设备型号"""
    brand = run_adb_command("getprop ro.product.brand").strip()
    model = run_adb_command("getprop ro.product.model").strip()
    return f"{brand} {model}".strip() or "unknown"


def parse_codec_type(mime_type: str) -> Optional[str]:
    """从 MIME 类型解析编码类型"""
    mime_map = {
        'video/avc': 'h264',
        'video/h264': 'h264',
        'video/hevc': 'h265',
        'video/h265': 'h265',
        'video/av01': 'av1',
        'video/av1': 'av1',
        'video/x-vnd.on2.vp8': 'vp8',
        'video/vp8': 'vp8',
        'video/x-vnd.on2.vp9': 'vp9',
        'video/vp9': 'vp9',
        'audio/3gpp': 'amrnb',
        'audio/amr-wb': 'amrwb',
        'audio/mp4a-latm': 'aac',
        'audio/aac': 'aac',
        'audio/opus': 'opus',
        'audio/flac': 'flac',
    }
    return mime_map.get(mime_type.lower())


def is_hardware_encoder(name: str) -> bool:
    """判断是否为硬件编码器"""
    # 硬件编码器通常包含厂商标识
    hardware_prefixes = [
        'OMX.qcom.',      # 高通
        'OMX.MTK.',       # 联发科
        'OMX.Exynos.',    # 三星
        'OMX.hisi.',      # 华为
        'OMX.sec.',       # 三星
        'OMX.Intel.',     # 英特尔
        'OMX.NVIDIA.',    # 英伟达
        'c2.qti.',        # 高通 C2
        'c2.mtk.',        # 联发科 C2
        'c2.exynos.',     # 三星 C2
        'c2.hisi.',       # 华为 C2
    ]

    # 软件编码器标识
    software_prefixes = [
        'OMX.google.',
        'c2.android.',
        'c2.vivo.',       # vivo 软件编码器
        'c2.oppo.',       # OPPO 软件编码器
    ]

    name_lower = name.lower()

    # 先检查是否明确是软件编码器
    for prefix in software_prefixes:
        if name_lower.startswith(prefix.lower()):
            return False

    # 检查是否是硬件编码器
    for prefix in hardware_prefixes:
        if name_lower.startswith(prefix.lower()):
            return True

    # 默认根据名称判断
    return not any(sw in name_lower for sw in ['google', 'android', 'sw', 'soft'])


def parse_media_player_output(output: str) -> List[CodecInfo]:
    """解析 dumpsys media.player 输出 (Android 10+)"""
    codecs = []

    # 匹配编码器/解码器块
    # 格式:
    # Media type 'video/hevc':
    #   Encoder "c2.qti.hevc.encoder" supports
    #     aliases: [ "OMX.qcom.video.encoder.hevc" ]
    #     attributes: 0xb: [
    #       encoder: 1,
    #       vendor: 1,
    #       software-only: 0,
    #       hw-accelerated: 1 ]

    current_media_type = None

    lines = output.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]

        # 匹配 Media type
        media_match = re.match(r"Media type '([^']+)'", line)
        if media_match:
            current_media_type = media_match.group(1)
            i += 1
            continue

        # 匹配 Encoder/Decoder
        codec_match = re.match(r'\s+(Encoder|Decoder) "([^"]+)" supports', line)
        if codec_match and current_media_type:
            direction = codec_match.group(1).lower()
            codec_name = codec_match.group(2)
            codec_type = parse_codec_type(current_media_type)

            if not codec_type:
                i += 1
                continue

            # 解析后续属性
            alias = ""
            is_hw = False
            is_vendor = False
            is_sw_only = True
            profile_levels = []

            # 读取接下来的行直到下一个编码器或媒体类型
            j = i + 1
            while j < len(lines):
                attr_line = lines[j]

                # 如果遇到新的编码器或媒体类型，停止
                if re.match(r'\s+(Encoder|Decoder) "', attr_line) or \
                   re.match(r"Media type '", attr_line):
                    break

                # 解析别名
                alias_match = re.search(r'"([^"]+)"', attr_line)
                if 'alias' in attr_line.lower() and alias_match and not alias:
                    alias = alias_match.group(1)

                # 解析属性
                if 'encoder:' in attr_line:
                    pass  # 已经知道是编码器
                if 'vendor:' in attr_line:
                    is_vendor = '1' in attr_line
                if 'software-only:' in attr_line:
                    is_sw_only = '1' in attr_line
                if 'hw-accelerated:' in attr_line:
                    is_hw = '1' in attr_line

                # 解析配置级别
                if 'profile/levels' in attr_line:
                    # 提取下一行的配置
                    pass

                j += 1

            # 如果没有从属性中获取到 hw 信息，从名称判断
            if not is_hw and is_hardware_encoder(codec_name):
                is_hw = True
                is_sw_only = False

            codecs.append(CodecInfo(
                name=codec_name,
                alias=alias,
                codec_type=codec_type,
                direction=direction,
                is_hardware=is_hw,
                vendor=is_vendor,
                software_only=is_sw_only,
                profile_levels=profile_levels
            ))

        i += 1

    return codecs


def parse_media_codec_output(output: str) -> List[CodecInfo]:
    """解析 dumpsys media.codec 输出 (Android 8-9)"""
    codecs = []

    # OMX 格式:
    # OMX.qcom.video.encoder.hevc
    #   type: video/hevc
    #   ...

    current_codec = None
    current_type = None
    is_encoder = False

    lines = output.split('\n')
    for line in lines:
        # 匹配 OMX 或 C2 编码器名称
        codec_match = re.match(r'\s*(OMX\.[\w\.]+|c2\.[\w\.]+)$', line.strip())
        if codec_match:
            if current_codec and current_type:
                codec_type = parse_codec_type(current_type)
                if codec_type:
                    is_hw = is_hardware_encoder(current_codec)
                    codecs.append(CodecInfo(
                        name=current_codec,
                        alias="",
                        codec_type=codec_type,
                        direction="encoder" if is_encoder else "decoder",
                        is_hardware=is_hw,
                        vendor=is_hw,
                        software_only=not is_hw
                    ))

            current_codec = codec_match.group(1)
            current_type = None
            is_encoder = 'encoder' in current_codec.lower()
            continue

        # 匹配类型
        type_match = re.match(r'\s*type:\s*(.+)', line)
        if type_match and current_codec:
            current_type = type_match.group(1).strip()

    # 处理最后一个
    if current_codec and current_type:
        codec_type = parse_codec_type(current_type)
        if codec_type:
            is_hw = is_hardware_encoder(current_codec)
            codecs.append(CodecInfo(
                name=current_codec,
                alias="",
                codec_type=codec_type,
                direction="encoder" if is_encoder else "decoder",
                is_hardware=is_hw,
                vendor=is_hw,
                software_only=not is_hw
            ))

    return codecs


def parse_media_codecs_xml() -> List[CodecInfo]:
    """解析 /vendor/etc/media_codecs.xml"""
    codecs = []
    output = run_adb_command("cat /vendor/etc/media_codecs.xml 2>/dev/null")

    if not output:
        return codecs

    # 匹配 <MediaCodec name="..." type="...">
    # 支持多种格式
    patterns = [
        r'<MediaCodec\s+name="([^"]+)"\s+type="([^"]+)"',
        r'<MediaCodec\s+type="([^"]+)"\s+name="([^"]+)"',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, output)
        for match in matches:
            name = match[0]
            mime_type = match[1]

            # 检查是否是视频编码器
            codec_type = parse_codec_type(mime_type)
            if not codec_type:
                continue

            is_encoder = 'encoder' in name.lower()
            is_hw = is_hardware_encoder(name)

            # 避免重复
            existing_names = [c.name for c in codecs]
            if name not in existing_names:
                codecs.append(CodecInfo(
                    name=name,
                    alias="",
                    codec_type=codec_type,
                    direction="encoder" if is_encoder else "decoder",
                    is_hardware=is_hw,
                    vendor=is_hw,
                    software_only=not is_hw
                ))

    return codecs


def query_device_capabilities() -> DeviceEncoderCapabilities:
    """查询设备编码能力"""
    android_version = get_android_version()
    device_model = get_device_model()

    print(f"[INFO] Device: {device_model}")
    print(f"[INFO] Android: {android_version}")

    all_codecs = []

    # 尝试 dumpsys media.player (Android 10+)
    print("[INFO] Trying dumpsys media.player...")
    output = run_adb_command("dumpsys media.player 2>/dev/null")
    if output and "Encoder" in output:
        print("[INFO] Found media.player service")
        codecs = parse_media_player_output(output)
        all_codecs.extend(codecs)
    else:
        print("[INFO] media.player not available or empty")

    # 尝试 dumpsys media.codec (Android 8-11)
    if len(all_codecs) < 5:  # 如果结果不够完整
        print("[INFO] Trying dumpsys media.codec...")
        output = run_adb_command("dumpsys media.codec 2>/dev/null")
        if output and "OMX" in output:
            print("[INFO] Found media.codec service")
            codecs = parse_media_codec_output(output)
            all_codecs.extend(codecs)
        else:
            print("[INFO] media.codec not available")

    # 尝试解析 media_codecs.xml (最后手段)
    if len(all_codecs) < 5:
        print("[INFO] Trying /vendor/etc/media_codecs.xml...")
        codecs = parse_media_codecs_xml()
        all_codecs.extend(codecs)

    # 整理结果
    video_encoders = {}
    video_decoders = {}
    audio_encoders = {}

    video_codecs = {'h264', 'h265', 'av1', 'vp8', 'vp9'}
    audio_codecs = {'opus', 'aac', 'flac', 'amrnb', 'amrwb'}

    for codec in all_codecs:
        if codec.codec_type in video_codecs:
            target = video_encoders if codec.direction == 'encoder' else video_decoders
            category = 'hardware' if codec.is_hardware else 'software'

            if codec.codec_type not in target:
                target[codec.codec_type] = {'hardware': [], 'software': []}

            target[codec.codec_type][category].append(codec.name)

        elif codec.codec_type in audio_codecs and codec.direction == 'encoder':
            if codec.codec_type not in audio_encoders:
                audio_encoders[codec.codec_type] = []
            audio_encoders[codec.codec_type].append(codec.name)

    return DeviceEncoderCapabilities(
        android_version=android_version,
        device_model=device_model,
        video_encoders=video_encoders,
        video_decoders=video_decoders,
        audio_encoders=audio_encoders
    )


def print_summary(capabilities: DeviceEncoderCapabilities):
    """打印设备能力摘要"""
    print("\n" + "=" * 60)
    print("Hardware Encoder Capabilities Summary")
    print("=" * 60)

    print("\nVideo Encoders:")
    print("-" * 40)
    for codec in ['h264', 'h265', 'av1', 'vp8', 'vp9']:
        hw = capabilities.video_encoders.get(codec, {}).get('hardware', [])
        sw = capabilities.video_encoders.get(codec, {}).get('software', [])

        if hw or sw:
            print(f"\n  {codec.upper()}:")
            if hw:
                print(f"    [HW] Hardware: {', '.join(hw[:2])}{'...' if len(hw) > 2 else ''}")
            if sw:
                print(f"    [SW] Software: {', '.join(sw[:2])}{'...' if len(sw) > 2 else ''}")
            if not hw and not sw:
                print(f"    [--] Not supported")

    print("\nBest Encoder Choice:")
    print("-" * 40)
    best = capabilities.get_best_video_encoder()
    if best:
        hw = capabilities.has_hardware_encoder(best)
        print(f"  Recommended: {best.upper()} {'(Hardware)' if hw else '(Software)'}")

    print("\nEncoder Support Status:")
    print("-" * 40)
    for codec in ['h264', 'h265', 'av1']:
        has_hw = capabilities.has_hardware_encoder(codec)
        status = "[HW] Hardware" if has_hw else "[SW] Software or None"
        print(f"  {codec.upper()}: {status}")

    print("\n" + "=" * 60)


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Query Android device hardware encoder capabilities')
    parser.add_argument('--json', action='store_true', help='Output in JSON format')
    parser.add_argument('--save', type=str, help='Save result to file')
    parser.add_argument('--quiet', action='store_true', help='Quiet mode, output result only')
    args = parser.parse_args()

    if not args.quiet:
        print("Android Hardware Encoder Query Tool")
        print("=" * 40)

    capabilities = query_device_capabilities()

    if args.json:
        # Output in JSON format
        result = {
            'android_version': capabilities.android_version,
            'device_model': capabilities.device_model,
            'video_encoders': capabilities.video_encoders,
            'video_decoders': capabilities.video_decoders,
            'audio_encoders': capabilities.audio_encoders,
            'best_encoder': capabilities.get_best_video_encoder(),
            'hardware_support': {
                'h264': capabilities.has_hardware_encoder('h264'),
                'h265': capabilities.has_hardware_encoder('h265'),
                'av1': capabilities.has_hardware_encoder('av1'),
            }
        }
        output = json.dumps(result, indent=2, ensure_ascii=False)
        print(output)

        if args.save:
            with open(args.save, 'w', encoding='utf-8') as f:
                f.write(output)
            print(f"\n[INFO] Saved to {args.save}")
    else:
        print_summary(capabilities)

        if args.save:
            with open(args.save, 'w', encoding='utf-8') as f:
                json.dump(asdict(capabilities), f, indent=2, ensure_ascii=False)
            print(f"[INFO] Saved to {args.save}")


if __name__ == "__main__":
    main()
