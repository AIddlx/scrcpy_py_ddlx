# 视频/音频管线

本文档整理 scrcpy-py-ddlx 的视频和音频数据流动路径。

---

## 视频管线

```
┌─────────────────────────────────────────────────────────────────┐
│                         设备端 (Android)                         │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐  │
│  │ ScreenCapture│ → │ VideoEncoder│ → │ scrcpy-server socket │  │
│  │   (H.265)   │    │  (H.265)    │    │   (video port)       │  │
│  └─────────────┘    └─────────────┘    └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ↓ TCP
┌─────────────────────────────────────────────────────────────────┐
│                         PC端 (Python)                            │
│                                                                  │
│  ┌─────────────────┐                                            │
│  │  VideoSocket    │ ← 接收编码后的 H.265 packets               │
│  └────────┬────────┘                                            │
│           ↓                                                      │
│  ┌─────────────────┐                                            │
│  │ VideoDemuxer    │ ← StreamingVideoDemuxer 解析 packet header │
│  │ (demuxer/video) │   输出: VideoPacket(header + data)         │
│  └────────┬────────┘                                            │
│           ↓ (VideoPacket queue)                                 │
│  ┌─────────────────┐                                            │
│  │ VideoDecoder    │ ← PyAV 解码 H.265 → YUV420P                │
│  │ (decoder/video) │   _frame_to_bgr() → RGB numpy array        │
│  └────────┬────────┘                                            │
│           ↓                                                      │
│  ┌─────────────────┐                                            │
│  │ DelayBuffer     │ ← 只存储最新 1 帧 (单帧 buffer)            │
│  │(delay_buffer.py)│   get_nowait() 返回当前帧，不区分新旧      │
│  └────────┬────────┘                                            │
│           ↓                                                      │
│  ┌─────────────────┐                                            │
│  │ Preview Window  │ ← Qt 显示 (Screen/VideoWindow)             │
│  │   或 Screenshot  │                                            │
│  └─────────────────┘                                            │
│                                                                  │
│  frame_sink 链路:                                               │
│  decoder._frame_sink.push(frame) → Screen.push(frame)          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 关键类

| 类 | 文件 | 功能 |
|---|------|------|
| `VideoDemuxer` | `core/demuxer/video.py` | 解析视频包，输出 VideoPacket |
| `VideoDecoder` | `core/decoder/video.py` | 解码视频，输出 RGB numpy array |
| `DelayBuffer` | `core/decoder/delay_buffer.py` | 单帧缓冲，防止撕裂 |
| `Screen` | `core/av_player.py` | Qt 视频显示 |

### 重要方法

```python
# VideoDecoder
get_frame_nowait()  # 获取当前帧（非阻塞，返回 DelayBuffer 中的帧）
get_frame_count()   # 获取解码的总帧数

# DelayBuffer
push(frame)         # 推入新帧（覆盖旧帧）
get_nowait()        # 返回当前帧（同一帧可能被多次返回）
```

---

## 音频管线

```
┌─────────────────────────────────────────────────────────────────┐
│                         设备端 (Android)                         │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐  │
│  │ AudioCapture │ → │ AudioEncoder│ → │ scrcpy-server socket │  │
│  │   (OPUS)     │    │   (OPUS)    │    │   (audio port)       │  │
│  └─────────────┘    └─────────────┘    └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ↓ TCP
┌─────────────────────────────────────────────────────────────────┐
│                         PC端 (Python)                            │
│                                                                  │
│  ┌─────────────────┐                                            │
│  │  AudioSocket    │ ← 接收编码后的 OPUS packets                │
│  └────────┬────────┘                                            │
│           ↓                                                      │
│  ┌─────────────────┐                                            │
│  │ AudioDemuxer    │ ← 解析 packet header                       │
│  │ (audio/demuxer) │   输出: AudioPacket                        │
│  └────────┬────────┘                                            │
│           ↓                                                      │
│  ┌─────────────────┐                                            │
│  │ AudioDecoder    │ ← PyAV 解码 OPUS → float32 PCM             │
│  │ (audio/decoder) │   输出: bytes (float32, interleaved)       │
│  └────────┬────────┘                                            │
│           ↓                                                      │
│  ┌─────────────────┐                                            │
│  │ AudioPlayer     │ ← SoundDevicePlayer 实时播放               │
│  │ 或 AudioRecorder│ ← 写入 WAV 文件                            │
│  └─────────────────┘                                            │
│                                                                  │
│  _frame_sink 链路:                                              │
│  decoder._frame_sink.push(bytes) → player.push(bytes)          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 关键类

| 类 | 文件 | 功能 |
|---|------|------|
| `AudioDemuxer` | `core/audio/demuxer.py` | 解析音频包 |
| `AudioDecoder` | `core/audio/decoder.py` | 解码 OPUS → PCM |
| `SoundDevicePlayer` | `core/audio/sounddevice_player.py` | 实时播放 |
| `AudioRecorder` | `core/audio/recorder.py` | WAV 录制 |

---

## 现有录制方案

### 1. 连接时录制（推荐）

通过 `ClientConfig` 配置，使用现有的 `Recorder` 类：

```python
config = ClientConfig(
    record_filename="output.mp4",
    record_format="mp4",
    audio=True
)
client = ScrcpyClient(config)
client.connect()
# 整个会话会被录制
```

**优点**：
- 已实现，稳定
- 直接录制编码后的包，无重新编码

**缺点**：
- 只能在连接时启用
- 无法动态开始/停止

### 2. 音频录制（已实现）

```python
# 动态录制音频
client.start_audio_recording("audio.wav", max_duration=5.0)
# ...
client.stop_audio_recording()
```

**原理**：在 `_frame_sink` 链路中插入 `AudioRecorder`

**注意事项**：
- 录制时长可能少于设定时间（如果设备静默）

---

## 录制设计思路

### 方案 A: 录制编码后的包（✅ 已实现）

```
VideoDemuxer ──→ VideoPacket ──→ Recorder (编码后直接写入)
                     ↓
                 VideoDecoder ──→ Preview
```

**优点**：无重新编码，性能最优

**实现方式**：
1. `StreamingDemuxerBase` 添加 `add_packet_sink(queue)` 方法
2. `RecordingManager` 管理录制生命周期
3. 录制时将队列添加到 Demuxer 的 sink 列表

### 方案 B: 录制解码后的帧（❌ 已放弃）

```
VideoDecoder ──→ DelayBuffer ──→ Recorder (重新编码)
                     ↓
                 Preview
```

**缺点**：
- 需要重新编码，CPU 开销大
- DelayBuffer 只存 1 帧，帧获取逻辑复杂
- 难以区分新旧帧

### 方案 C: 连接时录制（原有功能）

通过 `ClientConfig` 配置：

```python
config = ClientConfig(record_filename="output.mp4")
client = ScrcpyClient(config)
```

**优点**：已实现，稳定
**缺点**：只能在连接时启用，无法动态开始/停止

---

## 相关文档

- `CAPABILITY_NEGOTIATION.md` - 编解码器协商
- `HARDWARE_DECODER_PRIORITY.md` - 解码器优先级
- `PREVIEW_WINDOW_HANDLING.md` - 预览窗口处理
