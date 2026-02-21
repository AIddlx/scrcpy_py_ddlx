# 硬件解码器优先级

## 概述

视频解码器的选择对性能和画质有重要影响。本文档说明 scrcpy-py-ddlx 中硬件解码器的优先级策略。

## 优先级原则

**核心原则**: 厂商专用硬解 > 通用硬件接口 > 软件解码

厂商专用硬件解码器通常比通用接口更高效，因为它们能更好地利用 GPU 的特定功能。

## Windows 解码优先级

```
1. NVDEC (NVIDIA)    - NVIDIA GPU 专用，性能最优
2. QSV (Intel)        - Intel GPU 专用
3. D3D11VA (通用)     - Windows 通用硬件接口，兼容性最好
4. 软件解码           - 最后降级方案
```

## Linux 解码优先级

```
1. NVDEC (NVIDIA)    - NVIDIA GPU 专用
2. QSV (Intel)        - Intel GPU 专用
3. VAAPI (通用)       - Intel/AMD 通用接口
4. VDPAU (旧)         - NVIDIA 旧接口
5. 软件解码           - 最后降级方案
```

## 代码实现

文件: `scrcpy_py_ddlx/core/hw_decoder.py`

```python
# Windows - 厂商专用硬解优先
if cls._is_codec_available("h264_nvdec"):
    return cls(HWDeviceType.NVIDIA)
if cls._is_codec_available("h264_qsv"):
    return cls(HWDeviceType.INTEL_QSV)
if cls._is_codec_available("h264_d3d11va"):
    return cls(HWDeviceType.D3D11VA)
```

## 编解码器协商

文件: `scrcpy_py_ddlx/client/capability_cache.py`

### 协商策略

**Phase 1**: 双方都支持硬解
```
优先级: AV1 > H.265 > H.264
```

**Phase 2**: 仅设备端硬解（PC 软解）
```
优先级: H.265 > H.264
```

### AV1 特殊处理

AV1 需要特殊处理，因为：

| 端 | 支持情况 |
|---|---------|
| 手机端硬编 | 极少，仅骁龙 8 Gen2/3、天玑 9200+ 等旗舰芯片 |
| 电脑端硬解 | NVIDIA 30系+、AMD 6000系+、Intel Arc/11代核显+ |

**策略**: AV1 必须双方都支持硬解才推荐，否则降级到 H.265

## 常见问题

### Q: 为什么不用 D3D11VA 作为首选？

A: D3D11VA 是通用接口，虽然兼容性好，但效率通常不如厂商专用解码器。例如 NVIDIA 的 NVDEC 针对 H.264/H.265 有专门的硬件优化。

### Q: 如何检查当前使用的解码器？

A: 查看日志输出，启动时会显示：
```
Auto-detected NVIDIA NVDEC for hardware decoding
```

### Q: 硬解不可用时会怎样？

A: 自动降级到软件解码，日志会显示：
```
No hardware decoder available, will use software decoding
```

## 相关文档

- [CODEC_CAPABILITY_DETECTION.md](./CODEC_CAPABILITY_DETECTION.md) - 编解码器能力检测
- [CAPABILITY_NEGOTIATION.md](./CAPABILITY_NEGOTIATION.md) - 能力协商协议
