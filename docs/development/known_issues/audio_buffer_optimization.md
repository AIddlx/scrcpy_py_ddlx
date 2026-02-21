# 音频缓冲优化

## 问题描述

音频播放存在明显滞后（约 200ms+），影响音视频同步体验。

## 原因分析

### 1. 预缓冲过大
原配置：
```python
DEFAULT_PREBUFFER_MS = 200   # 200ms 预缓冲
DEFAULT_MAX_BUFFER_MS = 500  # 500ms 最大缓冲
```

这导致：
- 初始延迟就有 200ms
- 缓冲可能累积到 500ms

### 2. 关键帧解码阻塞
- 视频关键帧解码需要 100+ ms
- 解码期间音频解码被阻塞（GIL 竞争）
- 预缓冲不足时会导致 underrun

## 尝试过程

### 第一次尝试：预缓冲 50ms
```python
DEFAULT_PREBUFFER_MS = 50
DEFAULT_MAX_BUFFER_MS = 200
```

**结果**：出现大量 underrun
```
[CALLBACK] #12: Underrun (needed 7680 bytes, had 0)
[CALLBACK] #13: Underrun (needed 7680 bytes, had 0)
```

**原因**：50ms 只有约 3 个 OPUS 帧，关键帧解码期间缓冲被耗尽。

### 最终方案：预缓冲 100ms
```python
DEFAULT_PREBUFFER_MS = 100   # 100ms 预缓冲
DEFAULT_MAX_BUFFER_MS = 200  # 200ms 最大缓冲
```

**结果**：稳定运行，无 underrun
```
SoundDevicePlayer closed (pushed: 894, played: 860, underruns: 0)
```

## 最终配置

文件：`scrcpy_py_ddlx/core/audio/sounddevice_player.py`

```python
DEFAULT_TARGET_BUFFERING_MS = 50   # 目标缓冲延迟
DEFAULT_OUTPUT_BUFFER_MS = 20      # 匹配 OPUS 帧大小
DEFAULT_MAX_BUFFER_MS = 200        # 最大 200ms 限制延迟
DEFAULT_PREBUFFER_MS = 100         # 100ms 预缓冲（平衡延迟和稳定性）
```

## 经验总结

| 参数 | 原值 | 新值 | 说明 |
|------|------|------|------|
| PREBUFFER | 200ms | **100ms** | 降低初始延迟 |
| MAX_BUFFER | 500ms | **200ms** | 限制累积延迟 |

### 设计权衡
- **预缓冲太小（50ms）**：underrun，音频断续
- **预缓冲太大（200ms）**：延迟高，音视频不同步
- **预缓冲适中（100ms）**：既无 underrun，延迟也可接受

### OPUS 帧参考
- OPUS 格式：48kHz, 2ch, 20ms/帧
- 每帧：1920 samples = 7680 bytes (float32)
- 50fps 音频流
- 100ms ≈ 5 个 OPUS 帧

## 相关文件

- `scrcpy_py_ddlx/core/audio/sounddevice_player.py` - 音频播放器
- `scrcpy_py_ddlx/core/audio/decoder.py` - 音频解码器
- `scrcpy_py_ddlx/core/demuxer/udp_audio.py` - UDP 音频解复用器

## 后续改进方向

1. **音视频 PTS 同步**：基于时间戳实现精确同步
2. **动态丢帧**：当缓冲过大时丢弃旧帧
3. **自适应缓冲**：根据网络状况动态调整预缓冲
4. **GIL 优化**：将音频解码放到独立进程避免被视频解码阻塞

---

**修复日期**：2026-02-20
**测试环境**：Windows 11, NVIDIA GPU, vivo V2307A (Android 16)
