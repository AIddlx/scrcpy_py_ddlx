# 录音透传模式待实现

## 概述

录音功能支持三种模式，其中透传模式尚未完全实现。

## 影响版本

所有版本

## 优先级

中

## 状态

⏳ 待实现（当前回退为转码模式）

---

## 问题描述

### 现象

当使用 `format=auto` 或不指定格式时，录音会回退为 OPUS 转码模式，而非真正的透传模式。

### 当前实现

```
┌─────────────────────────────────────────────────────────────┐
│                    当前录音流程                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  format=auto → 回退为 OPUS 转码                              │
│                    ↓                                        │
│  OPUS 数据包 → 解码为 PCM → 重新编码为 OPUS → .opus 文件      │
│                                                             │
│  问题：二次编码有质量损失，CPU 开销大                          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 理想实现

```
┌─────────────────────────────────────────────────────────────┐
│                    理想录音流程                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  format=auto → 透传模式                                      │
│                    ↓                                        │
│  OPUS 数据包 → 直接写入 OGG 容器 → .ogg 文件                  │
│                                                             │
│  优点：无损、零延迟、CPU 开销最小                             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 根本原因

透传模式需要修改音频 demuxer 层，在解码前拦截原始 OPUS 数据包。这涉及：

1. **修改 `AudioDemuxer`** - 添加原始数据包回调机制
2. **修改 `AudioDecoder`** - 支持同时录制和解码
3. **使用 `OpusPassthroughRecorder`** - 已创建但未集成

### 相关代码

**已准备的透传录制器**:
- `scrcpy_py_ddlx/core/audio/passthrough_recorder.py`

**需要修改的文件**:
- `scrcpy_py_ddlx/core/demuxer/audio.py` - 添加数据包拦截
- `scrcpy_py_ddlx/client/client.py` - 集成透传录制器
- `scrcpy_py_ddlx/mcp_server.py` - 启用透传模式

---

## 当前可用模式

| format 参数 | 模式 | 输出 | 状态 |
|------------|------|------|------|
| `auto` | 透传（回退为转码） | .opus | ⚠️ 回退 |
| `wav` | 解码为 PCM | .wav | ✅ 可用 |
| `opus` | 解码后重新编码 | .opus | ✅ 可用 |
| `mp3` | 解码后重新编码 | .mp3 | ✅ 可用 |

---

## 改进方案

### 方案：集成透传录制器

1. **修改 AudioDemuxer**：在 `_recv_packet()` 后添加回调
2. **修改 AudioDecoder**：支持 passthrough_recorder
3. **修改 start_audio_recording**：检测 passthrough=True 时使用透传录制器

**预估工作量**：2-4 小时

---

## 相关文件

- `scrcpy_py_ddlx/core/audio/passthrough_recorder.py` - 透传录制器（已创建）
- `scrcpy_py_ddlx/core/audio/recorder.py` - 普通录制器
- `scrcpy_py_ddlx/core/demuxer/audio.py` - 音频 demuxer
- `scrcpy_py_ddlx/mcp_server.py` - MCP 服务端

## 相关文档

- [ADB 隧道模式](../../ADB_TUNNEL_MODE.md)

## 历史

- 2026-02-18: 问题识别，创建透传录制器，暂时回退为转码模式
