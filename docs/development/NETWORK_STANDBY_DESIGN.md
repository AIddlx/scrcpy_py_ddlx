# 网络模式编码器热启动设计

> 日期：2026-02-17
> 状态：**实现完成**

## 重要技术限制

> **非 root ADB shell 本质是"沙盒租户"**：
> - 可用工具：`nohup`、`setsid`（如有）、`am`
> - `start` 完全封死
> - 代码 daemon 是"自杀式"操作（脱离后无法控制）
> - **真正的长期后台服务**：要么 root，要么走应用层（如通过 `am` 启动 Foreground Service）
>
> 当前使用 `nohup` 是权宜之计，以后可探索：
> 1. 通过 `am startservice` 启动 Foreground Service
> 2. 使用 scrcpy-companion 作为服务管理器
> 3. WorkManager / JobScheduler 定期唤醒

---

## 问题分析

### 当前行为
```
客户端 connect(video=False, audio=False)
    ↓
服务端启动，开始发送视频/音频流  ← 浪费带宽和 CPU
    ↓
客户端不接收/不解码
```

### 期望行为
```
客户端 connect(video=False, audio=False)
    ↓
服务端启动，编码器待命（standby）
    ↓
客户端需要截图 → 发送 REQUEST_FRAME 消息
    ↓
服务端启动编码器，发送一帧，然后待命
```

## 设计方案

### 1. 服务端编码器待命模式

```java
// VideoEncoder 状态机
IDLE ──start()──→ ENCODING ──stop()──→ IDLE
  │                   │
  │                   │ pause()
  │                   ↓
  └──────────← STANDBY ──resume()──→ ENCODING

// STANDBY: 编码器已初始化，但不输出帧
// ENCODING: 正常编码输出
```

### 2. 控制消息扩展

**新增控制消息类型**：

| 类型 | 值 | 方向 | 说明 |
|------|---|------|------|
| `REQUEST_VIDEO_FRAME` | 20 | C→S | 请求一帧视频 |
| `START_VIDEO` | 21 | C→S | 启动视频流 |
| `STOP_VIDEO` | 22 | C→S | 停止视频流（编码器待命） |
| `START_AUDIO` | 23 | C→S | 启动音频流 |
| `STOP_AUDIO` | 24 | C→S | 停止音频流（编码器待命） |

**消息格式**：
```
REQUEST_VIDEO_FRAME:
  - type: 1 byte (20)
  - 无额外参数

START_VIDEO:
  - type: 1 byte (21)
  - 无额外参数（使用当前配置）

SCREENSHOT (已有，增强)：
  - type: 1 byte (18)
  - 服务端收到后立即编码一帧并发送
```

### 3. 服务端实现 ✅

**已实现文件**：
- `ControlMessage.java`: 添加 TYPE_REQUEST_VIDEO_FRAME (20) ~ TYPE_STOP_AUDIO (24)
- `Controller.java`: 添加 setSurfaceEncoder(), setAudioEncoder(), 以及对应的消息处理方法
- `SurfaceEncoder.java`: 添加 setStandby(), requestSingleFrame(), waitInStandby()
- `AudioEncoder.java`: 添加 setStandby()
- `Server.java`: 传递编码器引用给 Controller

### 4. 客户端实现 ✅

**已实现文件**：
- `protocol.py`: 添加 REQUEST_VIDEO_FRAME (20) ~ STOP_AUDIO (24)
- `control.py`: 添加新消息类型的序列化支持
- `client.py`: 添加 request_video_frame(), start_video_stream(), stop_video_stream(), start_audio_stream(), stop_audio_stream()

### 5. MCP 集成 ✅

**已更新**：
- `scrcpy_http_mcp_server.py`: 网络模式 video=False 时使用 request_video_frame()

## 使用场景

### 场景 1：只要截图，不要视频流
```python
config = ClientConfig(
    connection_mode="network",
    video=False,           # 不启动视频流
)
client = ScrcpyClient(config)
client.connect()

# 需要截图时
client.request_video_frame()  # 请求一帧
frame = client.screenshot()
```

### 场景 2：动态启停视频
```python
# 启动时只要控制
client.connect(video=False)

# 需要预览时启动视频
client.start_video_stream()

# 不需要预览时停止（编码器待命，节省带宽）
client.stop_video_stream()

# 截图仍然可用
client.screenshot()  # 内部调用 request_video_frame
```

### 场景 3：网络模式截图（video=False）
```python
# MCP 服务器场景
connect(connection_mode="network", video=False)

# 截图时
screenshot()  # 自动发送 REQUEST_VIDEO_FRAME
```

## 实现步骤

### 阶段 1：控制消息定义 ✅
- [x] 在 `protocol.py` 添加新消息类型
- [x] 在 Java 服务端添加对应常量

### 阶段 2：服务端实现 ✅
- [x] VideoEncoder 添加 standby 模式
- [x] AudioEncoder 添加 standby 模式
- [x] 处理 REQUEST_VIDEO_FRAME 消息
- [x] 处理 START_VIDEO / STOP_VIDEO 消息

### 阶段 3：客户端实现 ✅
- [x] ControlMessage 支持新类型
- [x] ScrcpyClient 添加 request_video_frame()
- [x] ScrcpyClient 添加 start_video_stream() / stop_video_stream()

### 阶段 4：MCP 集成 ✅
- [x] 更新 screenshot 在网络模式下的行为

## 兼容性

- 新消息类型对旧服务端无效（忽略）
- 旧客户端不发送新消息，不影响新服务端
- 需要同时更新客户端和服务端才能使用新功能

---

*此文档记录网络模式编码器热启动设计及实现。*
