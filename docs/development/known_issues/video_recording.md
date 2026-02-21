# 视频录制功能

## 概述

MCP 服务器动态录制视频（带音频）功能。

## 状态

✅ 已实现（方案 A）

---

## 实现方案

### 方案 A: 录制编码后的包（已采用）

从 Demuxer 输出获取编码后的 H.265/OPUS 包，直接写入 MP4：

```
VideoDemuxer ──→ VideoPacket ──→ VideoDecoder → Preview
                     ↓
                Recorder (编码后直接写入)
```

**优点**：
- 无重新编码，性能最优
- 保持原始画质
- 支持视频和音频

**实现方式**：
1. `StreamingDemuxerBase` 添加 `add_packet_sink(queue)` 方法
2. 创建 `RecordingManager` 管理录制生命周期
3. 录制时创建队列并添加到 Demuxer 的 sink 列表
4. Demuxer 在放入主队列后同时分发到所有 sink
5. 转发线程从 sink 队列读取并传给 `Recorder`

---

## 相关文件

- `scrcpy_py_ddlx/core/demuxer/base.py` - `StreamingDemuxerBase` (add_packet_sink)
- `scrcpy_py_ddlx/core/packet_tee.py` - `RecordingManager`
- `scrcpy_py_ddlx/core/av_player.py` - `Recorder` 类
- `scrcpy_py_ddlx/mcp_server.py` - `record_video` 方法

---

## 使用方法

### MCP 工具

```json
{
  "tool": "record_video",
  "arguments": {
    "filename": "output.mp4",
    "duration": 10
  }
}
```

### Python API

```python
from scrcpy_py_ddlx.core.packet_tee import RecordingManager

manager = RecordingManager(client)
manager.start_recording("output.mp4", video=True, audio=True)
# ... 等待录制
manager.stop_recording()
```

---

## 历史问题（已解决）

### 旧实现的问题

1. **颜色错误**：红蓝反转 - 原因是 BGR/RGB 转换错误
2. **帧数不足**：请求 15 秒只录制 8 秒 - 原因是 `get_frame_nowait()` 返回重复帧
3. **画面静止**：重复帧问题 - 原因是 DelayBuffer 只存 1 帧
4. **音频阻塞**：TeeAudioSink 阻塞实时播放

### 根本原因

旧实现尝试从解码后的帧重新编码：
- `get_frame_nowait()` 返回 DelayBuffer 中的帧，不区分新旧
- 实时 H.264 编码消耗大量 CPU
- 帧获取逻辑与实际解码不同步

### 解决方案

采用方案 A：直接录制编码后的包，避免重新编码。

---

## 历史

- 2026-02-17: 初步实现，发现多个问题
- 2026-02-18: 标记为需要重新设计
- 2026-02-18: 采用方案 A 重新实现，已解决
