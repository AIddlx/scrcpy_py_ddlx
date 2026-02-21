#!/usr/bin/env python3
"""
PC 端硬件编解码能力检测脚本

检测:
1. PyAV/FFmpeg 支持的解码器和编码器
2. 硬件加速解码 (NVIDIA NVDEC/Intel QSV/AMD AMF)
3. 硬件加速编码 (NVIDIA NVENC/Intel QSV/AMD AMF)
4. 推荐的编解码配置

用法:
    python query_pc_decoders.py
    python query_pc_decoders.py --json
    python query_pc_decoders.py --save result.json
"""

import sys
import json
import platform
import subprocess
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple


@dataclass
class DecoderInfo:
    """解码器信息"""
    name: str           # 解码器名称
    codec: str          # 编码类型: h264, h265, av1
    is_hardware: bool   # 是否硬件加速
    available: bool     # 是否可用


@dataclass
class PCCapabilities:
    """PC 编解码能力"""
    os: str
    python_version: str
    pyav_version: str
    ffmpeg_version: str

    # 解码器支持
    decoders: Dict[str, Dict]  # {h264: {hardware: [...], software: [...]}}

    # 编码器支持
    encoders: Dict[str, Dict]  # {h264: {hardware: [...], software: [...]}}

    # 硬件加速
    nvidia_cuda: bool
    nvidia_nvenc: bool      # NVIDIA 硬件编码
    nvidia_nvdec: bool      # NVIDIA 硬件解码
    intel_qsv: bool         # Intel Quick Sync Video (编解码)
    amd_amf: bool           # AMD AMF (编解码)
    vaapi: bool             # Linux VAAPI
    d3d11va: bool           # Windows D3D11VA

    # 推荐配置
    recommended_decode_codec: str
    recommended_decode_decoder: str
    recommended_encode_codec: str
    recommended_encode_encoder: str

    def can_decode_hardware(self, codec: str) -> bool:
        """检查是否有指定编解码器的硬件解码"""
        codec = codec.lower().replace('.', '')
        if codec in self.decoders:
            return len(self.decoders[codec].get('hardware', [])) > 0
        return False

    def can_decode_software(self, codec: str) -> bool:
        """检查是否有软件解码"""
        codec = codec.lower().replace('.', '')
        if codec in self.decoders:
            return len(self.decoders[codec].get('software', [])) > 0
        return False

    def can_encode_hardware(self, codec: str) -> bool:
        """检查是否有指定编解码器的硬件编码"""
        codec = codec.lower().replace('.', '')
        if codec in self.encoders:
            return len(self.encoders[codec].get('hardware', [])) > 0
        return False

    def can_encode_software(self, codec: str) -> bool:
        """检查是否有软件编码"""
        codec = codec.lower().replace('.', '')
        if codec in self.encoders:
            return len(self.encoders[codec].get('software', [])) > 0
        return False


def check_pyav_available() -> Tuple[bool, str]:
    """检查 PyAV 是否可用"""
    try:
        import av
        return True, av.__version__
    except ImportError:
        return False, ""


def check_ffmpeg_available() -> Tuple[bool, str]:
    """检查 FFmpeg 是否可用"""
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            # 提取版本号
            first_line = result.stdout.split('\n')[0]
            version = first_line.split()[2] if len(first_line.split()) > 2 else "unknown"
            return True, version
    except:
        pass
    return False, ""


def check_nvidia_gpu() -> Tuple[bool, bool, bool]:
    """检查 NVIDIA GPU (CUDA, NVENC 硬件编码, NVDEC 硬件解码)"""
    cuda_available = False
    nvenc_available = False
    nvdec_available = False

    # 检查 nvidia-smi
    try:
        result = subprocess.run(
            ['nvidia-smi'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            cuda_available = True
    except:
        pass

    # 检查 PyAV/FFmpeg NVENC 和 NVDEC 支持
    try:
        import av
        # 检查 NVENC 编码器 (h264_nvenc, hevc_nvenc)
        try:
            av.Codec('h264_nvenc', 'w')
            nvenc_available = True
        except:
            pass

        # 检查 NVDEC 解码器 (h264_cuvid, hevc_cuvid)
        try:
            av.Codec('h264_cuvid', 'r')
            nvdec_available = True
        except:
            pass

        # 如果 PyAV 检测不到但 nvidia-smi 可用，仍然标记为可能支持
        if cuda_available:
            if not nvenc_available:
                nvenc_available = True  # 可能支持，需要 FFmpeg 编译支持
            if not nvdec_available:
                nvdec_available = True  # 可能支持，需要 FFmpeg 编译支持

    except ImportError:
        pass

    return cuda_available, nvenc_available, nvdec_available


def check_intel_qsv() -> bool:
    """检查 Intel Quick Sync Video"""
    try:
        import av
        # 检查是否有 QSV 解码器
        try:
            codec = av.Codec('h264_qsv', 'r')
            return True
        except:
            pass

        # Windows: 检查 Intel GPU
        if platform.system() == 'Windows':
            try:
                result = subprocess.run(
                    ['wmic', 'path', 'win32_VideoController', 'get', 'name'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if 'Intel' in result.stdout:
                    return True
            except:
                pass
    except:
        pass

    return False


def check_amd_amf() -> bool:
    """检查 AMD AMF"""
    if platform.system() == 'Windows':
        try:
            result = subprocess.run(
                ['wmic', 'path', 'win32_VideoController', 'get', 'name'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if 'AMD' in result.stdout or 'Radeon' in result.stdout:
                return True
        except:
            pass
    return False


def check_d3d11va() -> bool:
    """检查 Windows D3D11VA 硬件加速"""
    if platform.system() != 'Windows':
        return False

    try:
        import av
        # 尝试创建 D3D11 硬件设备
        # PyAV 在 Windows 上通常使用 d3d11va 或 dxva2
        return True
    except:
        pass

    return False


def get_pyav_decoders() -> Dict[str, Dict]:
    """获取 PyAV 支持的解码器"""
    decoders = {
        'h264': {'hardware': [], 'software': []},
        'h265': {'hardware': [], 'software': []},
        'av1': {'hardware': [], 'software': []},
        'vp8': {'hardware': [], 'software': []},
        'vp9': {'hardware': [], 'software': []},
    }

    try:
        import av

        # 解码器名称映射
        codec_map = {
            'h264': ['h264', 'h264_qsv', 'h264_cuvid', 'h264_d3d11va', 'h264_dxva2'],
            'h265': ['hevc', 'hevc_qsv', 'hevc_cuvid', 'hevc_d3d11va', 'hevc_dxva2'],
            'av1': ['av1', 'libdav1d', 'av1_qsv', 'av1_cuvid'],
            'vp8': ['vp8'],
            'vp9': ['vp9', 'vp9_qsv', 'vp9_cuvid'],
        }

        # 硬件解码器标识
        hw_keywords = ['cuvid', 'qsv', 'd3d11va', 'dxva2', 'nvdec', 'amf', 'vaapi', 'vdpau']

        # 获取所有可用解码器
        available_codecs = []
        for codec in av.codecs_available:
            try:
                c = av.Codec(codec, 'r')
                if c.type == 'video':
                    available_codecs.append(codec)
            except:
                pass

        # 分类解码器
        for codec_type, names in codec_map.items():
            for name in names:
                if name in available_codecs:
                    is_hw = any(kw in name.lower() for kw in hw_keywords)
                    category = 'hardware' if is_hw else 'software'
                    if name not in decoders[codec_type][category]:
                        decoders[codec_type][category].append(name)

        # 额外检查: 标准解码器
        for codec_type in ['h264', 'h265', 'av1']:
            std_names = {
                'h264': 'h264',
                'h265': 'hevc',
                'av1': 'libdav1d'
            }
            std_name = std_names.get(codec_type)
            if std_name:
                try:
                    c = av.Codec(std_name, 'r')
                    if std_name not in decoders[codec_type]['software']:
                        decoders[codec_type]['software'].append(std_name)
                except:
                    pass

    except ImportError:
        pass

    return decoders


def get_pyav_encoders() -> Dict[str, Dict]:
    """获取 PyAV 支持的编码器"""
    encoders = {
        'h264': {'hardware': [], 'software': []},
        'h265': {'hardware': [], 'software': []},
        'av1': {'hardware': [], 'software': []},
        'vp8': {'hardware': [], 'software': []},
        'vp9': {'hardware': [], 'software': []},
    }

    try:
        import av

        # 编码器名称映射
        codec_map = {
            'h264': ['libx264', 'h264_nvenc', 'h264_qsv', 'h264_amf', 'h264_videotoolbox'],
            'h265': ['libx265', 'hevc_nvenc', 'hevc_qsv', 'hevc_amf', 'hevc_videotoolbox'],
            'av1': ['libaom-av1', 'libsvtav1', 'av1_nvenc', 'av1_qsv', 'av1_amf'],
            'vp8': ['libvpx'],
            'vp9': ['libvpx-vp9', 'vp9_qsv'],
        }

        # 硬件编码器标识
        hw_keywords = ['nvenc', 'qsv', 'amf', 'videotoolbox', 'vaapi']

        # 获取所有可用编码器
        available_codecs = []
        for codec in av.codecs_available:
            try:
                c = av.Codec(codec, 'w')
                if c.type == 'video':
                    available_codecs.append(codec)
            except:
                pass

        # 分类编码器
        for codec_type, names in codec_map.items():
            for name in names:
                if name in available_codecs:
                    is_hw = any(kw in name.lower() for kw in hw_keywords)
                    category = 'hardware' if is_hw else 'software'
                    if name not in encoders[codec_type][category]:
                        encoders[codec_type][category].append(name)

        # 额外检查: 标准软件编码器
        for codec_type in ['h264', 'h265']:
            std_names = {
                'h264': 'libx264',
                'h265': 'libx265'
            }
            std_name = std_names.get(codec_type)
            if std_name:
                try:
                    c = av.Codec(std_name, 'w')
                    if std_name not in encoders[codec_type]['software']:
                        encoders[codec_type]['software'].append(std_name)
                except:
                    pass

    except ImportError:
        pass

    return encoders


def get_ffmpeg_decoders() -> Dict[str, Dict]:
    """通过 FFmpeg 命令获取解码器"""
    decoders = {
        'h264': {'hardware': [], 'software': []},
        'h265': {'hardware': [], 'software': []},
        'av1': {'hardware': [], 'software': []},
        'vp8': {'hardware': [], 'software': []},
        'vp9': {'hardware': [], 'software': []},
    }

    try:
        result = subprocess.run(
            ['ffmpeg', '-decoders'],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            return decoders

        # 解析输出
        hw_keywords = ['cuvid', 'qsv', 'd3d11va', 'dxva2', 'nvdec', 'amf', 'vaapi', 'vdpau']

        codec_map = {
            'h264': ['h264', 'h264_cuvid', 'h264_qsv', 'h264_d3d11va'],
            'h265': ['hevc', 'hevc_cuvid', 'hevc_qsv', 'hevc_d3d11va'],
            'av1': ['av1', 'libdav1d', 'av1_cuvid', 'av1_qsv'],
            'vp8': ['vp8'],
            'vp9': ['vp9', 'vp9_cuvid', 'vp9_qsv'],
        }

        for line in result.stdout.split('\n'):
            for codec_type, names in codec_map.items():
                for name in names:
                    if name in line:
                        is_hw = any(kw in line.lower() for kw in hw_keywords)
                        category = 'hardware' if is_hw else 'software'
                        if name not in decoders[codec_type][category]:
                            decoders[codec_type][category].append(name)

    except:
        pass

    return decoders


def get_ffmpeg_encoders() -> Dict[str, Dict]:
    """通过 FFmpeg 命令获取编码器"""
    encoders = {
        'h264': {'hardware': [], 'software': []},
        'h265': {'hardware': [], 'software': []},
        'av1': {'hardware': [], 'software': []},
        'vp8': {'hardware': [], 'software': []},
        'vp9': {'hardware': [], 'software': []},
    }

    try:
        result = subprocess.run(
            ['ffmpeg', '-encoders'],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            return encoders

        # 解析输出
        hw_keywords = ['nvenc', 'qsv', 'amf', 'videotoolbox', 'vaapi']

        codec_map = {
            'h264': ['libx264', 'h264_nvenc', 'h264_qsv', 'h264_amf'],
            'h265': ['libx265', 'hevc_nvenc', 'hevc_qsv', 'hevc_amf'],
            'av1': ['libaom-av1', 'libsvtav1', 'av1_nvenc', 'av1_qsv', 'av1_amf'],
            'vp8': ['libvpx'],
            'vp9': ['libvpx-vp9', 'vp9_qsv'],
        }

        for line in result.stdout.split('\n'):
            for codec_type, names in codec_map.items():
                for name in names:
                    if name in line:
                        is_hw = any(kw in line.lower() for kw in hw_keywords)
                        category = 'hardware' if is_hw else 'software'
                        if name not in encoders[codec_type][category]:
                            encoders[codec_type][category].append(name)

    except:
        pass

    return encoders


def merge_decoders(pyav: Dict, ffmpeg: Dict) -> Dict[str, Dict]:
    """合并 PyAV 和 FFmpeg 解码器"""
    result = {}

    for codec in ['h264', 'h265', 'av1', 'vp8', 'vp9']:
        result[codec] = {
            'hardware': list(set(pyav.get(codec, {}).get('hardware', []) +
                                ffmpeg.get(codec, {}).get('hardware', []))),
            'software': list(set(pyav.get(codec, {}).get('software', []) +
                                ffmpeg.get(codec, {}).get('software', [])))
        }

    return result


def merge_encoders(pyav: Dict, ffmpeg: Dict) -> Dict[str, Dict]:
    """合并 PyAV 和 FFmpeg 编码器"""
    result = {}

    for codec in ['h264', 'h265', 'av1', 'vp8', 'vp9']:
        result[codec] = {
            'hardware': list(set(pyav.get(codec, {}).get('hardware', []) +
                                ffmpeg.get(codec, {}).get('hardware', []))),
            'software': list(set(pyav.get(codec, {}).get('software', []) +
                                ffmpeg.get(codec, {}).get('software', [])))
        }

    return result


def get_recommended_decode_config(capabilities: PCCapabilities) -> Tuple[str, str]:
    """获取推荐解码配置"""
    # 优先级: H.265硬件 > H.264硬件 > H.265软件 > H.264软件
    priority = [
        ('h265', 'hardware'),
        ('h264', 'hardware'),
        ('h265', 'software'),
        ('h264', 'software'),
    ]

    for codec, category in priority:
        decoders = capabilities.decoders.get(codec, {}).get(category, [])
        if decoders:
            return codec, decoders[0]

    return 'h264', 'software'


def get_recommended_encode_config(capabilities: PCCapabilities) -> Tuple[str, str]:
    """获取推荐编码配置 (用于录屏录像)"""
    # 优先级: H.264硬件 > H.265硬件 > H.264软件 > H.265软件
    # 注意: 录屏优先 H.264 因为兼容性更好，H.265 编码更耗资源
    priority = [
        ('h264', 'hardware'),  # H.264 硬件编码兼容性最好
        ('h265', 'hardware'),  # H.265 硬件编码压缩率更高
        ('h264', 'software'),  # H.264 软件编码通用
        ('h265', 'software'),  # H.265 软件编码较慢
    ]

    for codec, category in priority:
        encoders = capabilities.encoders.get(codec, {}).get(category, [])
        if encoders:
            return codec, encoders[0]

    return 'h264', 'software'


def detect_capabilities() -> PCCapabilities:
    """检测 PC 编解码能力"""
    # 基本信息
    os_info = f"{platform.system()} {platform.release()}"
    python_ver = platform.python_version()

    # PyAV
    pyav_ok, pyav_ver = check_pyav_available()
    pyav_ver = pyav_ver if pyav_ok else "not installed"

    # FFmpeg
    ffmpeg_ok, ffmpeg_ver = check_ffmpeg_available()
    ffmpeg_ver = ffmpeg_ver if ffmpeg_ok else "not installed"

    # 硬件加速
    nvidia_cuda, nvidia_nvenc, nvidia_nvdec = check_nvidia_gpu()
    intel_qsv = check_intel_qsv()
    amd_amf = check_amd_amf()
    d3d11va = check_d3d11va()
    vaapi = platform.system() == 'Linux'  # Linux 通常支持 VAAPI

    # 解码器
    if pyav_ok:
        pyav_decoders = get_pyav_decoders()
        pyav_encoders = get_pyav_encoders()
    else:
        pyav_decoders = {}
        pyav_encoders = {}

    if ffmpeg_ok:
        ffmpeg_decoders = get_ffmpeg_decoders()
        ffmpeg_encoders = get_ffmpeg_encoders()
    else:
        ffmpeg_decoders = {}
        ffmpeg_encoders = {}

    decoders = merge_decoders(pyav_decoders, ffmpeg_decoders)
    encoders = merge_encoders(pyav_encoders, ffmpeg_encoders)

    # 构建结果
    caps = PCCapabilities(
        os=os_info,
        python_version=python_ver,
        pyav_version=pyav_ver,
        ffmpeg_version=ffmpeg_ver,
        decoders=decoders,
        encoders=encoders,
        nvidia_cuda=nvidia_cuda,
        nvidia_nvenc=nvidia_nvenc,
        nvidia_nvdec=nvidia_nvdec,
        intel_qsv=intel_qsv,
        amd_amf=amd_amf,
        vaapi=vaapi,
        d3d11va=d3d11va,
        recommended_decode_codec='',
        recommended_decode_decoder='',
        recommended_encode_codec='',
        recommended_encode_encoder=''
    )

    # 获取推荐配置
    caps.recommended_decode_codec, caps.recommended_decode_decoder = get_recommended_decode_config(caps)
    caps.recommended_encode_codec, caps.recommended_encode_encoder = get_recommended_encode_config(caps)

    return caps


def print_summary(caps: PCCapabilities):
    """打印能力摘要"""
    print("\n" + "=" * 60)
    print("PC Hardware Codec Capabilities")
    print("=" * 60)

    print(f"\nSystem: {caps.os}")
    print(f"Python: {caps.python_version}")
    print(f"PyAV: {caps.pyav_version}")
    print(f"FFmpeg: {caps.ffmpeg_version}")

    print("\n" + "-" * 40)
    print("Hardware Acceleration:")
    print("-" * 40)
    print(f"  NVIDIA CUDA: {'Yes' if caps.nvidia_cuda else 'No'}")
    print(f"  NVIDIA NVENC (Encode): {'Yes' if caps.nvidia_nvenc else 'No'}")
    print(f"  NVIDIA NVDEC (Decode): {'Yes' if caps.nvidia_nvdec else 'No'}")
    print(f"  Intel QSV: {'Yes' if caps.intel_qsv else 'No'}")
    print(f"  AMD AMF: {'Yes' if caps.amd_amf else 'No'}")
    if platform.system() == 'Windows':
        print(f"  D3D11VA: {'Yes' if caps.d3d11va else 'No'}")
    if platform.system() == 'Linux':
        print(f"  VAAPI: {'Yes' if caps.vaapi else 'No'}")

    # 解码器
    print("\n" + "-" * 40)
    print("Video Decoders:")
    print("-" * 40)

    for codec in ['h264', 'h265', 'av1', 'vp8', 'vp9']:
        hw = caps.decoders.get(codec, {}).get('hardware', [])
        sw = caps.decoders.get(codec, {}).get('software', [])

        print(f"\n  {codec.upper()}:")
        if hw:
            print(f"    [HW] Hardware: {', '.join(hw)}")
        else:
            print(f"    [HW] Hardware: Not available")
        if sw:
            print(f"    [SW] Software: {', '.join(sw)}")

    # 编码器
    print("\n" + "-" * 40)
    print("Video Encoders (for recording):")
    print("-" * 40)

    for codec in ['h264', 'h265', 'av1', 'vp8', 'vp9']:
        hw = caps.encoders.get(codec, {}).get('hardware', [])
        sw = caps.encoders.get(codec, {}).get('software', [])

        print(f"\n  {codec.upper()}:")
        if hw:
            print(f"    [HW] Hardware: {', '.join(hw)}")
        else:
            print(f"    [HW] Hardware: Not available")
        if sw:
            print(f"    [SW] Software: {', '.join(sw)}")

    # 推荐配置
    print("\n" + "-" * 40)
    print("Recommended Configuration:")
    print("-" * 40)
    print(f"  Decode (Playback):")
    print(f"    Codec: {caps.recommended_decode_codec.upper()}")
    print(f"    Decoder: {caps.recommended_decode_decoder}")
    print(f"  Encode (Recording):")
    print(f"    Codec: {caps.recommended_encode_codec.upper()}")
    print(f"    Encoder: {caps.recommended_encode_encoder}")

    # 硬件支持状态
    print("\n" + "-" * 40)
    print("Hardware Support Summary:")
    print("-" * 40)
    print(f"  {'Codec':<8} {'Decode':<15} {'Encode':<15}")
    print(f"  {'-'*8} {'-'*15} {'-'*15}")
    for codec in ['h264', 'h265', 'av1']:
        decode_status = "HW" if caps.can_decode_hardware(codec) else "SW"
        encode_status = "HW" if caps.can_encode_hardware(codec) else "SW"
        print(f"  {codec.upper():<8} {decode_status:<15} {encode_status:<15}")

    print("\n" + "=" * 60)


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Query PC hardware codec capabilities (decode and encode)')
    parser.add_argument('--json', action='store_true', help='Output in JSON format')
    parser.add_argument('--save', type=str, help='Save result to file')
    parser.add_argument('--quiet', action='store_true', help='Quiet mode')
    args = parser.parse_args()

    if not args.quiet:
        print("PC Hardware Codec Query Tool (Decode + Encode)")
        print("=" * 40)

    caps = detect_capabilities()

    if args.json:
        result = {
            'os': caps.os,
            'python_version': caps.python_version,
            'pyav_version': caps.pyav_version,
            'ffmpeg_version': caps.ffmpeg_version,
            'decoders': caps.decoders,
            'encoders': caps.encoders,
            'hardware_acceleration': {
                'nvidia_cuda': caps.nvidia_cuda,
                'nvidia_nvenc': caps.nvidia_nvenc,
                'nvidia_nvdec': caps.nvidia_nvdec,
                'intel_qsv': caps.intel_qsv,
                'amd_amf': caps.amd_amf,
                'd3d11va': caps.d3d11va,
                'vaapi': caps.vaapi,
            },
            'recommended_decode': {
                'codec': caps.recommended_decode_codec,
                'decoder': caps.recommended_decode_decoder,
            },
            'recommended_encode': {
                'codec': caps.recommended_encode_codec,
                'encoder': caps.recommended_encode_encoder,
            },
            'hardware_support': {
                'h264': {'decode': caps.can_decode_hardware('h264'), 'encode': caps.can_encode_hardware('h264')},
                'h265': {'decode': caps.can_decode_hardware('h265'), 'encode': caps.can_encode_hardware('h265')},
                'av1': {'decode': caps.can_decode_hardware('av1'), 'encode': caps.can_encode_hardware('av1')},
            }
        }
        output = json.dumps(result, indent=2)
        print(output)

        if args.save:
            with open(args.save, 'w', encoding='utf-8') as f:
                f.write(output)
            print(f"\n[INFO] Saved to {args.save}")
    else:
        print_summary(caps)

        if args.save:
            with open(args.save, 'w', encoding='utf-8') as f:
                json.dump(asdict(caps), f, indent=2)
            print(f"[INFO] Saved to {args.save}")


if __name__ == "__main__":
    main()
