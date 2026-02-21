# 端到端延迟分析

> **版本**: 1.3
> **最后更新**: 2026-02-20
> **相关测试**: scrcpy_network_test_20260220_*

本文档记录了端到端延迟的测量方法、分析结果和优化经验。

---

## 测量背景

用户通过拍照测量发现预览画面滞后于手机约 **148ms**，需要分析延迟来源。

---

## 延迟分解

### 客户端延迟（实测）

| 阶段 | 平均 | 最小 | 最大 | 说明 |
|------|------|------|------|------|
| 队列等待 | 0.2ms | 0.1ms | 18.3ms | 解码队列 |
| GPU 解码 | 3.4ms | 2.7ms | 8.2ms | NVDEC H.265 |
| **UDP→consume** | **13.8ms** | 3.9ms | 24.3ms | 接收到渲染 |

### 服务端帧间隔（实测）

| 指标 | 值 | 说明 |
|------|-----|------|
| 平均帧间隔 | 24.4ms | ≈ 41fps |
| 最小间隔 | 6ms | |
| **最大间隔** | **128ms** | ⚠️ 异常 |

### 完整延迟估算

| 阶段 | 延迟 | 来源 |
|------|------|------|
| Android 屏幕捕获 | ~10-20ms | 估计 |
| **Android 编码缓冲** | **~50-100ms** | 主要瓶颈 |
| 网络传输 | ~1-5ms | 局域网 |
| 客户端处理 | ~14ms | 实测 |
| 显示 VSync | ~0-16ms | 60Hz |
| **总计** | **~75-155ms** | ✅ 接近实测 148ms |

---

## 关键发现

### 1. 客户端不是瓶颈

客户端总延迟仅 ~14ms，包括：
- 解码：3.4ms
- 队列+渲染：~10ms

### 2. 服务端帧间隔异常

最大帧间隔达到 **128ms**，远超 60fps 的 16.67ms 期望值。

### 3. 延迟主要来自 Android 编码器

148ms 延迟的主要来源是 Android 端的编码缓冲。

---

## 服务端延迟根因分析

通过分析 `SurfaceEncoder.java`，发现以下导致延迟的关键因素：

### 1. REPEAT_FRAME_DELAY 设置 (100ms)

**代码位置**: `SurfaceEncoder.java:35`

```java
private static final int REPEAT_FRAME_DELAY_US = 100_000; // repeat after 100ms
```

**影响**: 当屏幕内容没有变化时，MediaCodec 会等待 100ms 后才发送重复帧。

**协议设置**: `createFormat()` 方法中设置
```java
format.setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, REPEAT_FRAME_DELAY_US);
```

### 2. MediaCodec 内部缓冲

**代码位置**: `SurfaceEncoder.java:240`

```java
int outputBufferId = codec.dequeueOutputBuffer(bufferInfo, -1);
```

**问题**:
- `-1` 表示无限等待编码输出
- MediaCodec 内部有帧缓冲队列用于优化压缩
- 编码器可能缓存 2-3 帧来提高 B 帧压缩效率

### 3. VBR 模式的码率控制

**代码位置**: `SurfaceEncoder.java:316`

```java
format.setInteger(MediaFormat.KEY_BITRATE_MODE,
    MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_VBR);
```

**影响**:
- VBR (Variable Bitrate) 模式下，编码器会延迟输出以优化码率分配
- 复杂场景时编码器会积累更多帧来决策最佳压缩策略
- 这是默认模式

### 4. 128ms 最大帧间隔的原因

**场景分析**:

| 场景 | 预期间隔 | 实际间隔 | 原因 |
|------|----------|----------|------|
| 正常运动 | 16-25ms | 24.4ms avg | ✅ 正常 |
| 静止画面 | N/A | 100ms | REPEAT_FRAME_DELAY |
| 复杂运动+VBR | 16ms | 可能 50-128ms | 编码器缓冲+码率控制 |

**结论**: 128ms 的最大帧间隔是以下因素的组合：
1. **VBR 码率控制延迟**: 复杂场景时编码器缓冲
2. **MediaCodec 内部队列**: 编码器缓存多帧优化压缩
3. **系统调度**: Android 后台进程可能被抢占

---

## 优化尝试记录 (v1.2 - v1.3)

### 已实现的优化参数

| 参数 | 默认值 | 说明 | 实际效果 |
|------|--------|------|----------|
| `low_latency` | `false` | 启用 MediaCodec 低延迟模式 (Android 11+) | ⚠️ 部分设备不兼容 |
| `encoder_priority` | `1` | 编码线程优先级: 0=normal, 1=urgent, 2=realtime | 效果有限 |
| `encoder_buffer` | `0` | 编码器缓冲: 0=auto, 1=禁用B帧 | 效果有限 |
| `skip_frames` | `true` | 跳过缓冲帧，只发送最新帧 | 帧间隔均匀时无效 |

### 优化尝试结论

**测试结果**: 上述优化参数对延迟改善**效果有限**（改善 <10ms）

**原因分析**:

1. **`low_latency` 兼容性问题**
   - 部分设备（如 realme RMX1931）不支持
   - 会导致 `MediaCodec.configure()` 抛出 `IllegalArgumentException`
   - 建议默认关闭

2. **`encoder_buffer` 效果有限**
   - `KEY_MAX_B_FRAMES` 只影响编码策略
   - 硬件编码器的内部流水线无法通过软件参数改变

3. **`skip_frames` 未触发**
   - 跳帧逻辑仅在编码器输出队列有多帧积压时生效
   - 实测显示编码器输出帧间隔较均匀（~16-25ms）
   - 没有多帧积压，跳帧逻辑不会触发

4. **线程优先级影响有限**
   - Android 线程调度本身有优先级限制
   - 即使设置为最高优先级，仍受系统调度影响

### 核心问题：硬件编码器的固有限制

```
应用层
    ↓
MediaCodec API  ← 应用只能控制到这里
    ↓
OMX IL / Codec2 框架  ← 系统级，不对外开放
    ↓
厂商驱动 (Qualcomm/MTK/三星)
    ↓
硬件编码器 (DSP/GPU)  ← 闭源固件，无法修改
```

**结论**: MediaCodec 已经是应用层能访问的最高级别接口。编码器的内部缓冲是**硬件/固件决定的**，无法通过软件修改。

---

## 最终结论

### 延迟组成

| 阶段 | 延迟 | 是否可控 |
|------|------|----------|
| Android 屏幕捕获 | ~10-20ms | ❌ 系统级 |
| **Android 编码缓冲** | **~50-100ms** | ❌ 硬件固件 |
| 网络传输 | ~1-5ms | ✅ 可优化 |
| 客户端解码+渲染 | ~14ms | ✅ 已优化 |
| 显示 VSync | ~0-16ms | ❌ 显示器 |

### 现实情况

- **100-150ms 是无线投屏的正常范围**
- 客户端已优化到 ~14ms，进一步优化空间有限
- 主要延迟来自 Android 硬件编码器，这是设备固有限制
- 专业低延迟方案使用专用硬件+FPGA，不是通用手机

### 如果需要更低延迟

1. **使用 USB ADB 模式** - 比 WiFi 低 10-30ms
2. **使用 H264 代替 H265** - 编码速度快 10-20ms
3. **降低分辨率/帧率** - 减少编码负担
4. **软件编码 (FFmpeg)** - 完全可控，但 CPU 占用极高
5. **MJPEG 模式** - 延迟极低 (~5-20ms)，但带宽增加 3-5 倍

---

## 相关文档

- [PROTOCOL_CHANGE_CHECKLIST.md](PROTOCOL_CHANGE_CHECKLIST.md) - 协议修改检查清单
- [VIDEO_AUDIO_PIPELINE.md](VIDEO_AUDIO_PIPELINE.md) - 音视频管道
- [FEC_FIX_EXPERIENCE.md](../FEC_FIX_EXPERIENCE.md) - FEC 修复经验
