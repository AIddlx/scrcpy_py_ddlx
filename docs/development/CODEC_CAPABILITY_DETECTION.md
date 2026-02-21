# 编解码能力检测经验总结

本文档总结手机端和PC端编解码能力检测的实现方法和经验。

---

## 0. 快速使用

### 自动检测和缓存

首次使用时，系统会自动检测设备能力并缓存：

```python
from scrcpy_py_ddlx.client.capability_cache import CapabilityCache, get_optimal_codec

# 获取单例实例
cache = CapabilityCache.get_instance()

# 获取最优配置（自动检测并缓存）
config = cache.get_optimal_config()
print(f"推荐编解码器: {config.codec}")  # h264, h265, 或 av1

# 简单获取最优编解码器
codec = get_optimal_codec()  # 返回 "h264", "h265", 或 "av1"
```

### 在 ClientConfig 中使用

```python
from scrcpy_py_ddlx import ClientConfig, ScrcpyClient

# 使用 "auto" 自动选择最优编解码器
config = ClientConfig(
    codec="auto",  # 自动检测（默认值）
    audio=True
)

client = ScrcpyClient(config)
client.connect()
```

### 缓存策略

**按设备序列号独立缓存**：每个设备有独立的缓存，更换设备时自动检测新设备。

**永久有效**：硬件能力不会变化，缓存永久有效。
- 手机硬件编码器是固定的
- PC 硬件编解码能力也是固定的（除非更换硬件）

如需强制刷新：
```bash
python tests_gui/test_capability_cache.py --refresh  # 刷新当前设备
python tests_gui/test_capability_cache.py --clear    # 清除所有缓存
```

**缓存文件位置**：
```
Windows: C:\Users\<用户名>\.cache\scrcpy-py-ddlx\capability_cache.json
Linux:   ~/.cache/scrcpy-py-ddlx/capability_cache.json
macOS:   ~/.cache/scrcpy-py-ddlx/capability_cache.json
```

**缓存文件结构**：

{
  "pc_capability": { ... },           // PC 能力
  "device_c96d1705": { ... },         // 设备1: realme RMX1931
  "device_abc123": { ... },           // 设备2: 另一台手机
}
```

---

## 1. 背景与动机

### 为什么需要能力检测？

1. **设备碎片化**: Android 设备有上万种型号，编码器支持差异巨大
2. **PC 差异**: 不同 PC 的 GPU 支持不同的硬件编解码（NVIDIA/Intel/AMD）
3. **AV1 兼容性**: AV1 编解码器并非所有设备都支持硬解
4. **省电考虑**: 手机软件编码太耗电，必须优先使用硬件编码器
5. **录像功能**: PC 端需要编码能力为录屏功能做准备

### 应用场景

| 场景 | 说明 |
|------|------|
| **能力协商** | 服务端发送编码能力，客户端选择最佳配置 |
| **手动配置** | 用户根据查询结果手动指定编解码器 |
| **热连接** | 服务端持久运行，客户端唤醒时动态配置 |
| **录屏录像** | PC 端需要编码器将画面录制为视频文件 |

---

## 2. Android 设备编码器检测

### 2.1 Android MediaCodec 架构演变

| Android 版本 | 架构 | 服务名称 |
|-------------|------|---------|
| 8.0 及以下 | OMX | `media.codec` |
| 9-11 | OMX + C2 并存 | `media.codec` + `media.player` |
| 10+ | C2 (Codec2) | `media.player` |

### 2.2 检测方法（按优先级）

#### 方法 1: dumpsys media.player (Android 10+)

```bash
adb shell dumpsys media.player
```

输出格式：
```
Media type 'video/hevc':
  Encoder "c2.qti.hevc.encoder" supports
    aliases: [ "OMX.qcom.video.encoder.hevc" ]
    attributes: 0xb: [
      encoder: 1,
      vendor: 1,
      software-only: 0,
      hw-accelerated: 1 ]
```

**优点**: 包含完整的硬件加速信息
**缺点**: 仅 Android 10+ 可用

#### 方法 2: dumpsys media.codec (Android 8-11)

```bash
adb shell dumpsys media.codec
```

输出格式：
```
OMX.qcom.video.encoder.hevc
  type: video/hevc
```

**优点**: 兼容旧版本
**缺点**: 格式较老，可能不包含硬件加速标识

#### 方法 3: /vendor/etc/media_codecs.xml (备用)

```bash
adb shell cat /vendor/etc/media_codecs.xml
```

**优点**: 所有版本可用
**缺点**: 需要解析 XML，可能不完整

### 2.3 硬件编码器识别规则

```python
# 硬件编码器前缀
hardware_prefixes = [
    'OMX.qcom.',      # 高通 Snapdragon
    'OMX.MTK.',       # 联发科 MediaTek
    'OMX.Exynos.',    # 三星 Exynos
    'OMX.hisi.',      # 华为 HiSilicon
    'OMX.sec.',       # 三星 (旧)
    'OMX.Intel.',     # 英特尔
    'OMX.NVIDIA.',    # 英伟达 Tegra
    'c2.qti.',        # 高通 C2
    'c2.mtk.',        # 联发科 C2
    'c2.exynos.',     # 三星 C2
    'c2.hisi.',       # 华为 C2
]

# 软件编码器前缀
software_prefixes = [
    'OMX.google.',    # Google 软件编码
    'c2.android.',    # Android 软件编码
    'c2.vivo.',       # vivo 软件
    'c2.oppo.',       # OPPO 软件
]
```

### 2.4 编码器选择优先级

```
H.265 硬件 > H.264 硬件 > H.265 软件 > H.264 软件
```

---

## 3. PC 端编解码器检测

### 3.1 检测方法

#### PyAV 检测

```python
import av

# 获取所有可用编解码器
for codec in av.codecs_available:
    try:
        # 解码器
        dec = av.Codec(codec, 'r')
        # 编码器
        enc = av.Codec(codec, 'w')
    except:
        pass
```

#### FFmpeg 命令检测

```bash
# 列出所有解码器
ffmpeg -decoders

# 列出所有编码器
ffmpeg -encoders
```

### 3.2 硬件加速类型

| 平台 | 硬件加速 | 解码器后缀 | 编码器后缀 |
|------|---------|-----------|-----------|
| NVIDIA | NVDEC/NVENC | `_cuvid` | `_nvenc` |
| Intel | Quick Sync Video | `_qsv` | `_qsv` |
| AMD | AMF | (D3D11VA) | `_amf` |
| Windows | D3D11VA | `_d3d11va` | - |
| Linux | VAAPI | `_vaapi` | `_vaapi` |
| macOS | VideoToolbox | `_videotoolbox` | `_videotoolbox` |

### 3.3 GPU 检测

#### NVIDIA

```bash
# 检查 nvidia-smi
nvidia-smi

# 检查 NVENC 编码器
ffmpeg -encoders | findstr nvenc
# h264_nvenc, hevc_nvenc, av1_nvenc

# 检查 NVDEC 解码器
ffmpeg -decoders | findstr cuvid
# h264_cuvid, hevc_cuvid, av1_cuvid
```

#### Intel

```python
# 检查 QSV 编码器
av.Codec('h264_qsv', 'w')  # 编码
av.Codec('h264_qsv', 'r')  # 解码
```

#### AMD

```python
# 检查 AMF 编码器
av.Codec('h264_amf', 'w')
```

### 3.4 编解码器选择优先级

**解码 (播放)**:
```
H.265 硬件 > H.264 硬件 > H.265 软件 > H.264 软件
```

**编码 (录像)**:
```
H.264 硬件 > H.265 硬件 > H.264 软件 > H.265 软件
```

> 录像优先 H.264 因为兼容性更好，H.265 编码更耗资源

---

## 4. 查询脚本使用

### 4.1 Android 设备查询

```bash
# 基本用法
python scripts/query_android_encoders.py

# JSON 输出
python scripts/query_android_encoders.py --json

# 保存结果
python scripts/query_android_encoders.py --save device_encoders.json

# 静默模式
python scripts/query_android_encoders.py --json --quiet
```

输出示例：
```
Device: vivo iQOO 12
Android: 14

Video Encoders:
  H264:
    [HW] Hardware: c2.qti.h264.encoder
    [SW] Software: c2.android.h264.encoder
  H265:
    [HW] Hardware: c2.qti.hevc.encoder
    [SW] Software: c2.android.hevc.encoder

Best Encoder Choice:
  Recommended: H265 (Hardware)
```

### 4.2 PC 端查询

```bash
# 基本用法
python scripts/query_pc_decoders.py

# JSON 输出
python scripts/query_pc_decoders.py --json

# 保存结果
python scripts/query_pc_decoders.py --save pc_codecs.json
```

输出示例：
```
System: Windows 11
PyAV: 16.0.1
FFmpeg: 7.1.1

Hardware Acceleration:
  NVIDIA CUDA: Yes
  NVIDIA NVENC (Encode): Yes
  NVIDIA NVDEC (Decode): Yes
  Intel QSV: No
  AMD AMF: No

Video Decoders:
  H264: [HW] h264_cuvid, [SW] libx264
  H265: [HW] hevc_cuvid, [SW] libx265
  AV1:  [HW] av1_cuvid,  [SW] libdav1d

Video Encoders:
  H264: [HW] h264_nvenc, [SW] libx264
  H265: [HW] hevc_nvenc, [SW] libx265
  AV1:  [HW] av1_nvenc,  [SW] libaom-av1

Recommended Configuration:
  Decode (Playback):  H265 / hevc_cuvid
  Encode (Recording): H264 / h264_nvenc
```

---

## 5. 能力协商中的应用

### 5.1 协商流程

```
┌─────────────┐                    ┌─────────────┐
│   Android   │                    │     PC      │
│   Server    │                    │   Client    │
└──────┬──────┘                    └──────┬──────┘
       │                                  │
       │  1. 发送编码能力                  │
       │  (支持的编码器列表)               │
       │ ────────────────────────────────>│
       │                                  │
       │                                  │ 2. 查询 PC 解码能力
       │                                  │    (query_pc_decoders.py)
       │                                  │
       │                                  │ 3. 选择最佳配置
       │                                  │    (考虑双方能力)
       │                                  │
       │  4. 发送客户端配置                │
       │ <────────────────────────────────│
       │  (选定的编码器、分辨率、码率)      │
       │                                  │
       │  5. 应用配置，开始推流            │
       │ ────────────────────────────────>│
       │                                  │
```

### 5.2 配置匹配逻辑

```python
def select_best_config(server_caps, client_caps):
    """选择最佳编解码配置"""

    # 手机支持的编码器
    android_h265_hw = server_caps.video_encoders.get('h265', {}).get('hardware', [])
    android_h264_hw = server_caps.video_encoders.get('h264', {}).get('hardware', [])

    # PC 支持的解码器
    pc_h265_hw = client_caps.decoders.get('h265', {}).get('hardware', [])
    pc_h264_hw = client_caps.decoders.get('h264', {}).get('hardware', [])

    # 匹配优先级
    if android_h265_hw and pc_h265_hw:
        return 'h265', android_h265_hw[0]
    elif android_h264_hw and pc_h264_hw:
        return 'h264', android_h264_hw[0]
    elif android_h265_hw:
        return 'h265', android_h265_hw[0]  # PC 软解
    else:
        return 'h264', android_h264_hw[0] if android_h264_hw else 'software'
```

### 5.3 自动检测流程

```
┌─────────────────────────────────────────────────────────────┐
│                    首次使用流程                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. ClientConfig(codec="auto")                              │
│            ↓                                                │
│  2. CapabilityCache.get_instance()                          │
│            ↓                                                │
│  3. 检查缓存文件是否存在                                     │
│            ↓                                                │
│     ┌──────────────────┬──────────────────┐                │
│     │ 缓存存在         │ 缓存不存在        │                │
│     │ (30天内有效)     │                  │                │
│     └────────┬─────────┴────────┬─────────┘                │
│              ↓                  ↓                          │
│        加载缓存           查询设备能力                       │
│              │            - dumpsys media.player            │
│              │            - dumpsys media.codec             │
│              │                  ↓                          │
│              │            查询 PC 能力                       │
│              │            - nvidia-smi                      │
│              │            - wmic VideoController            │
│              │                  ↓                          │
│              │            保存到缓存                         │
│              │                  ↓                          │
│              └──────────────────┘                          │
│                        ↓                                    │
│  4. 选择最优配置                                             │
│     优先级: H.265硬件 > H.264硬件 > 软件编码                  │
│                        ↓                                    │
│  5. 返回 OptimalConfig                                      │
│     - codec: "h265"                                         │
│     - use_hardware: True                                    │
│     - encoder_name: "OMX.qcom.video.encoder.hevc"           │
│     - pc_decoder: "hevc_cuvid"                              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 6. 经验总结

### 6.1 Android 端

| 经验 | 说明 |
|------|------|
| **版本兼容** | 需要支持多种 dumpsys 格式，Android 8-14 差异大 |
| **厂商差异** | 高通/联发科/三星/华为的编码器命名规则不同 |
| **C2 vs OMX** | Android 10+ 优先使用 C2，但 OMX 仍可工作 |
| **软件编码** | 软件编码耗电严重，尽量避免使用 |
| **AV1 支持** | 新设备开始支持 AV1 硬件编码，但覆盖率低 |

### 6.2 PC 端

| 经验 | 说明 |
|------|------|
| **PyAV vs FFmpeg** | PyAV 更方便，但 FFmpeg 命令更全面 |
| **NVENC 优势** | NVIDIA 硬件编码质量好，兼容性最佳 |
| **QSV 兼容** | Intel 核显也支持 QSV，不需要独显 |
| **AV1 硬解** | RTX 40 系列 / Arc 显卡支持 AV1 硬解 |
| **软件编码** | libx264 质量好但慢，实时录制推荐硬编 |

### 6.3 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 黑屏/花屏 | 编解码器不匹配 | 检查双方能力，降级到 H.264 |
| 高延迟 | 软件编解码 | 优先使用硬件加速 |
| 耗电快 | 手机软编码 | 强制使用硬件编码器 |
| 录制失败 | PC 无编码器 | 安装支持的 FFmpeg 版本 |

### 6.4 已知限制

| 限制 | 说明 | 后续方案 |
|------|------|---------|
| **ADB 检测失败** | 极少数设备可能无法通过 `dumpsys` 获取编解码器信息 | 未来可通过服务端 Java 代码直接调用 MediaCodecList API 获取 |
| **权限问题** | 某些厂商 ROM 限制 dumpsys 访问 | 服务端获取更可靠 |
| **AV1 检测** | 部分设备声称支持 AV1 但实际效果差 | 需要实际测试验证 |

> **注意**：如果遇到 ADB 检测失败的情况，可以：
> 1. 手动指定编解码器：`ClientConfig(codec="h264")`
> 2. 未来版本将支持服务端检测（更可靠）

---

## 7. 相关文档

| 文档 | 说明 |
|------|------|
| [能力协商协议](CAPABILITY_NEGOTIATION.md) | 协议规范和数据格式 |
| [网络模式管线](NETWORK_PIPELINE.md) | TCP 控制 + UDP 媒体流 |
| [热连接实现](HOT_CONNECTION_IMPLEMENTATION.md) | 持久服务端 + 动态配置 |

---

## 8. 脚本位置

```
scripts/
├── query_android_encoders.py  # Android 设备编码器查询
└── query_pc_decoders.py       # PC 编解码能力查询

scrcpy_py_ddlx/client/
└── capability_cache.py        # 能力缓存模块（自动检测）
```

---

## 9. API 参考

### CapabilityCache 类

```python
from scrcpy_py_ddlx.client.capability_cache import CapabilityCache

cache = CapabilityCache.get_instance()

# 获取设备能力（自动缓存，按序列号区分）
device = cache.get_device_capability(device_serial=None)  # None = 自动检测
# device.device_model: "vivo iQOO 12"
# device.android_version: "14"
# device.video_encoders: {'h264': {'hardware': [...], 'software': [...]}, ...}
# device.has_hardware_encoder('h265'): True/False

# 获取 PC 能力（自动缓存）
pc = cache.get_pc_capability()
# pc.nvidia_cuda: True/False
# pc.nvidia_nvenc: True/False
# pc.decoders: {'h264': {'hardware': [...], 'software': [...]}, ...}
# pc.has_hardware_decoder('h265'): True/False

# 获取最优配置
config = cache.get_optimal_config(device_serial=None)  # None = 自动检测当前设备
# config.codec: "h264" / "h265" / "av1"
# config.use_hardware: True/False
# config.encoder_name: "OMX.qcom.video.encoder.hevc"
# config.pc_decoder: "hevc_cuvid"
# config.pc_encoder: "hevc_nvenc"
# config.confidence: "high" / "medium" / "low"

# 强制刷新缓存
cache.get_device_capability(force_refresh=True)
cache.get_pc_capability(force_refresh=True)

# 清除缓存
cache.clear_cache()                    # 清除所有
cache.clear_cache(device_serial="xxx") # 清除指定设备

# 查看缓存信息
info = cache.get_cache_info()
# info['cached_devices']: [{serial, model, android_version, age_days}, ...]

# 列出所有缓存的设备
devices = cache.list_cached_devices()
```

### 便捷函数

```python
from scrcpy_py_ddlx.client.capability_cache import (
    get_optimal_codec,
    get_connected_device_serial
)

# 快速获取最优编解码器
codec = get_optimal_codec()  # 返回 "h264", "h265", 或 "av1"

# 获取当前连接的设备序列号
serial = get_connected_device_serial()  # 返回 "c96d1705" 或 None
```

### ClientConfig 集成

```python
from scrcpy_py_ddlx import ClientConfig

config = ClientConfig()

# 使用自动检测（默认值）
config.codec = "auto"

# 解析实际编解码器
actual_codec = config.resolve_codec(device_serial)
# 如果 codec="auto"，返回检测到的最优编解码器
# 否则返回 config.codec

# 检查是否自动模式
config.is_auto_codec()  # True/False
```

---

## 10. 测试工具

### 命令行测试

```bash
# 测试当前设备（自动检测）
python tests_gui/test_capability_cache.py

# 列出所有缓存的设备
python tests_gui/test_capability_cache.py --list

# 强制刷新当前设备缓存
python tests_gui/test_capability_cache.py --refresh

# 清除所有缓存
python tests_gui/test_capability_cache.py --clear
```

### 输出示例

```
============================================================
Capability Cache Test
============================================================

Cache file: C:\Users\xxx\.cache\scrcpy-py-ddlx\capability_cache.json
Cache exists: True

Cached devices (2):
  - realme RMX1931 (Android 11)
    Serial: c96d1705, Age: 2.5 days
  - vivo iQOO 12 (Android 14)
    Serial: abc123, Age: 5.1 days

Current device: c96d1705

Optimal Configuration:
  Codec: H265
  Use Hardware: True
  Encoder: OMX.qcom.video.encoder.hevc
  PC Decoder: hevc_cuvid
  Confidence: high
```

---

## 11. 后续改进方向

### 11.1 加速能力协商（利用缓存）

**当前流程**：每次连接都进行完整能力协商

**优化方向**：利用缓存跳过重复查询

```python
def _send_client_configuration_fast(self, device_serial):
    """快速配置（利用缓存）"""
    from scrcpy_py_ddlx.client.capability_cache import CapabilityCache

    cache = CapabilityCache.get_instance()

    # 检查是否有该设备的缓存
    device = cache.get_device_capability(device_serial)
    if device.has_hardware_encoder('h265'):
        # 直接使用缓存结果，跳过能力查询
        config = ClientConfiguration(
            video_codec_id=VideoCodecId.H265,
            # ...
        )
        self._send_config(config)
        return True

    # 缓存不存在，回退到完整协商
    return self._send_client_configuration_normal()
```

**优化效果**：
- 减少能力协商时间
- 热连接时更快启动
- 减少网络往返

**注意**：此优化暂不实现，先完成基本功能。

### 11.2 服务端检测（更可靠）

当前通过 ADB `dumpsys` 检测编解码器，但存在以下问题：
- 某些厂商 ROM 限制 dumpsys 访问
- 解析不同版本的 dumpsys 输出较复杂
- 权限问题可能导致检测失败

**改进方案**：通过服务端 Java 代码直接调用 Android API 获取

```java
// 服务端检测代码示例（未来实现）
import android.media.MediaCodecList;
import android.media.MediaCodecInfo;

public class ServerCodecDetector {
    public static List<CodecInfo> getVideoEncoders() {
        MediaCodecList list = new MediaCodecList(MediaCodecList.ALL_CODECS);
        MediaCodecInfo[] infos = list.getCodecInfos();

        List<CodecInfo> result = new ArrayList<>();
        for (MediaCodecInfo info : infos) {
            if (!info.isEncoder()) continue;
            if (!isVideoCodec(info)) continue;

            result.add(new CodecInfo(
                info.getName(),
                info.isHardwareAccelerated(),
                info.getSupportedTypes()
            ));
        }
        return result;
    }
}
```

**优势**：
- 直接调用系统 API，100% 可靠
- 无需解析文本输出
- 获取更详细的能力信息（Profile、Level 等）

**实现时机**：当发现 ADB 检测在某些设备上失败时实现

---

*文档创建: 2026-02-15*
*最后更新: 2026-02-15*
*适用版本: scrcpy-py-ddlx v2.x*
