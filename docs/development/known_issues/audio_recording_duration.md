# 录音时长问题

## 概述

`record_audio` 实际录制时长可能少于设定的 duration。

## 影响版本

所有版本

## 优先级

低

## 状态

待改进

---

## 问题描述

### 现象

用户调用 `record_audio(duration=5)`，但实际录制的音频时长可能只有 4.98 秒。

### 根本原因

1. `AudioRecorder` 使用**经过的实际时间**（`time.time() - start_time`）判断是否达到 `max_duration`
2. 但最终时长是基于**实际收到的音频帧**计算的（`frames / sample_rate`）
3. 如果设备静默或延迟播放，音频数据流会有间隙

### 相关代码

**文件**: `scrcpy_py_ddlx/core/audio/recorder.py`

```python
def push(self, frame: bytes) -> bool:
    # ...
    # Check max duration - 基于经过时间判断
    if self._max_duration:
        elapsed = time.time() - self._start_time
        if elapsed >= self._max_duration:
            self.close()
            return False
```

```python
def close(self) -> None:
    # ...
    # 基于音频帧计算实际时长
    duration = self._frames_written / self._sample_rate
```

### 日志示例

```
Opening audio recorder: recordings/recording.wav
  Max duration: 5 sec
Max duration (5s) reached, closing recorder
Recording saved: recordings/recording.wav
  Duration: 4.98 sec   # 实际时长
  Frames: 239040
```

---

## 影响分析

### 用户影响

- 低：大多数情况下差异很小（< 0.1 秒）
- 仅在设备静默或延迟播放时才会明显

### 功能影响

- 不影响核心功能
- 返回的 duration 是请求值而非实际值

---

## 改进方案

### 方案 A: 返回实际录制时长（推荐）

修改 `mcp_server.py` 中的 `record_audio` 方法：

```python
# 从录音器获取实际时长
actual_duration = self._client._audio_recorder._frames_written / self._client._audio_recorder._sample_rate

return {
    "success": True,
    "filename": final_filename,
    "requested_duration": duration,
    "actual_duration": actual_duration,  # 新增
    "format": format,
}
```

### 方案 B: 添加警告提示

当实际时长与请求时长差异超过阈值（如 5%）时，添加警告：

```python
if abs(actual_duration - duration) / duration > 0.05:
    result["warning"] = "Actual duration differs significantly from requested. Device may have been silent."
```

### 方案 C: 基于音频帧判断结束

修改录音器，基于音频帧数量而非经过时间判断结束：

```python
# 计算需要的帧数
target_frames = int(self._max_duration * self._sample_rate)
if self._frames_written >= target_frames:
    self.close()
```

**风险**: 如果设备完全静默，录音将永远不会结束。

---

## 相关文件

- `scrcpy_py_ddlx/core/audio/recorder.py` - 录音器实现
- `scrcpy_py_ddlx/mcp_server.py` - MCP 录音方法
- `scrcpy_py_ddlx/client/client.py` - 客户端录音方法

## 相关文档

- `docs/user/troubleshooting.md` - 用户故障排除

## 历史

- 2026-02-17: 问题识别并记录
