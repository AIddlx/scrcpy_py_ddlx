# scrcpy-py-ddlx 客户端-服务端通信协议规范

> **版本**: 1.3
> **最后更新**: 2026-02-20
> **状态**: 实现 - TCP控制通道心跳已添加

本文档定义了 scrcpy-py-ddlx 项目中客户端（Python）和服务端（Android/Java）之间的通信协议。**任何对协议的修改都必须同时更新本文档，并确保双方实现一致。**

---

## ⚠️ 修改检查清单

修改 UDP 协议时，**必须同步更新**以下文件：

| 文件 | 说明 | 检查点 |
|------|------|--------|
| `UdpMediaSender.java` | 服务端 UDP 发送 | HEADER_SIZE 常量、所有发送方法 |
| `udp_video.py` | 客户端 UDP 接收 | `_parse_udp_header()`、UdpPacketHeader |
| `client.py` | 第一个 UDP 包解析 | 硬编码偏移量 (24, 36) |
| `protocol.py` | 协议常量 | UDP_HEADER_SIZE |
| `delay_buffer.py` | 帧元数据 | FrameWithMetadata 字段 |
| `stream.py` | VideoPacket | send_time_ns 字段 |
| `video.py` | 解码器 | push() 调用参数 |
| `opengl_widget.py` | E2E 延迟计算 | send_time_ns 读取 |
| `PROTOCOL_SPEC.md` | 本文档 | UDP 头部格式 |

---

## 目录

1. [连接模式概述](#1-连接模式概述)
2. [数据包格式规范](#2-数据包格式规范)
3. [连接建立流程](#3-连接建立流程)
4. [视频流协议](#4-视频流协议)
5. [音频流协议](#5-音频流协议)
6. [控制通道协议](#6-控制通道协议)
7. [错误处理与恢复](#7-错误处理与恢复)
8. [版本兼容性](#8-版本兼容性)

---

## 1. 连接模式概述

### 1.1 支持的连接模式

| 模式 | 视频传输 | 音频传输 | 控制传输 | 描述 |
|------|---------|---------|---------|------|
| **ADB隧道** | ADB forward → TCP | ADB forward → TCP | ADB forward → TCP | 通过USB连接，使用ADB端口转发 |
| **网络TCP** | TCP | TCP | TCP | 通过WiFi，纯TCP连接 |
| **网络UDP** | UDP | UDP | TCP | 通过WiFi，视频/音频用UDP，控制用TCP |

### 1.2 模式标识

```python
# 客户端配置
class ConnectionMode:
    ADB_TUNNEL = "adb"      # ADB 隧道模式
    NETWORK_TCP = "tcp"     # 网络 TCP 模式
    NETWORK_UDP = "udp"     # 网络 UDP 模式（推荐）
```

---

## 2. 数据包格式规范

### 2.1 Scrcpy 标准数据包头部（12字节）

**适用于**: 所有连接模式的视频/音频帧

```
偏移量   字段          大小    类型      描述
------   ----          ----    ----      ----
0        pts_flags     8       uint64    PTS + 标志位（big-endian）
8        size          4       uint32    负载大小（big-endian）
```

**pts_flags 字段位定义**:
```
位 63:    CONFIG 标志    - 1表示配置包，0表示媒体包
位 62:    KEY_FRAME 标志 - 1表示关键帧（仅视频）
位 0-61:  PTS 值        - 演示时间戳（微秒）
```

**示例**:
```python
# 配置包
pts_flags = 0x8000000000000000  # bit 63 = 1
size = 12                        # 负载大小

# 关键帧
pts_flags = pts | 0x4000000000000000  # pts + bit 62
size = frame_size

# 普通帧
pts_flags = pts  # 仅 PTS
size = frame_size
```

### 2.2 UDP 模式扩展头部（24字节）

**适用于**: 仅网络UDP模式的视频/音频传输

```
偏移量   字段          大小    类型      描述
------   ----          ----    ----      ----
0        sequence      4       uint32    包序列号（big-endian）
4        timestamp     8       int64     时间戳 / PTS（big-endian）
12       flags         4       uint32    标志位（big-endian）
16       send_time_ns  8       int64     设备发送时间（纳秒，big-endian）[v1.2新增]
```

**flags 字段位定义**:
```
位 0:    KEY_FRAME 标志 - 1表示关键帧
位 1:    CONFIG 标志    - 1表示配置包
位 2:    FEC_DATA 标志  - 1表示FEC数据包
位 3:    FEC_PARITY 标志 - 1表示FEC校验包
位 31:   FRAGMENTED 标志 - 1表示分片包
```

**完整 UDP 数据包结构**:
```
[UDP 头部: 24字节] [Scrcpy 头部: 12字节] [负载: N字节]
```

### 2.3 UDP 分片格式

当帧大小超过 UDP 最大负载（约 65KB）时，需要分片：

```
[UDP 头部: 24字节] [分片索引: 4字节] [分片数据: N字节]

分片索引从 0 开始递增
flags 的 bit 31 设置为 1 表示分片包
```

### 2.4 端到端延迟追踪 (v1.2)

**send_time_ns 字段说明**:
- 服务端在发送 UDP 包时记录 `System.nanoTime()`
- 客户端接收后可通过 `time.time() * 1e9 - send_time_ns` 计算完整 E2E 延迟
- **注意**: 需要设备与 PC 时钟大致同步，否则延迟值会有偏差

**数据流**:
```
服务端                          客户端
  │                               │
  │ System.nanoTime()             │
  │ ─────────────────────────────>│ time.time()
  │   send_time_ns in UDP header  │
  │                               │
  │        E2E 延迟 = (client_time * 1e9) - send_time_ns
```

### 2.5 协议常量定义

**服务端 (Java)**:
```java
// UdpMediaSender.java
private static final int HEADER_SIZE = 24;  // UDP header size
```

**客户端 (Python)**:
```python
# protocol.py
UDP_HEADER_SIZE: Final[int] = 24  # UDP header size

# 解析示例
seq, ts, flags, send_ns = struct.unpack('>IqIq', data[:24])
```

**关键偏移量**:
| 偏移量 | 说明 |
|--------|------|
| 0-3 | sequence (4B) |
| 4-11 | timestamp (8B) |
| 12-15 | flags (4B) |
| 16-23 | send_time_ns (8B) |
| 24+ | Scrcpy header / payload |

---

## 3. 连接建立流程

### 3.1 ADB 隧道模式

```
┌─────────────┐                    ┌─────────────┐
│   客户端     │                    │   服务端     │
└──────┬──────┘                    └──────┬──────┘
       │                                  │
       │  1. adb forward 创建隧道          │
       │─────────────────────────────────>│
       │                                  │
       │  2. 连接到本地转发端口             │
       │─────────────────────────────────>│
       │                                  │
       │  3. 接收 dummy byte (1字节)       │
       │<─────────────────────────────────│
       │                                  │
       │  4. 接收设备名 (64字节)            │
       │<─────────────────────────────────│
       │                                  │
       │  5. 接收 codec_id (4字节, 原始)   │
       │<─────────────────────────────────│
       │                                  │
       │  6. 接收视频尺寸 (8字节, 原始)     │
       │<─────────────────────────────────│
       │                                  │
       │  7. 开始接收视频帧 (带 scrcpy 头部) │
       │<─────────────────────────────────│
       │                                  │
```

**重要**: ADB 模式下，codec_id 和视频尺寸是**原始字节**，没有 scrcpy 头部！

### 3.2 网络 UDP 模式（带能力协商）

**新版本协议（推荐）**：支持能力协商，客户端可根据服务器能力选择最佳编码器。

```
阶段1: 连接建立
┌─────────────┐                    ┌─────────────┐
│   客户端     │                    │   服务端     │
└──────┬──────┘                    └──────┬──────┘
       │                                  │
       │  1. 绑定 UDP 视频/音频端口        │
       │  (等待接收)                       │
       │                                  │
       │  2. 连接 TCP 控制端口             │
       │─────────────────────────────────>│
       │                                  │
       │  3. 接收 dummy byte (1字节)       │
       │<─────────────────────────────────│
       │                                  │
       │  4. 接收设备名 (64字节, TCP)       │
       │<─────────────────────────────────│
       │                                  │
阶段2: 能力协商
       │  5. 接收设备能力信息 (TCP)         │
       │<─────────────────────────────────│
       │     见 3.3 设备能力格式            │
       │                                  │
       │  6. 发送客户端配置 (TCP)          │
       │─────────────────────────────────>│
       │     见 3.4 客户端配置格式          │
       │                                  │
阶段3: 媒体传输
       │  7. 接收视频配置包 (UDP)           │
       │  [UDP头16] [scrcpy头12] [配置]    │
       │<─────────────────────────────────│
       │                                  │
       │  8. 开始接收视频帧 (UDP)           │
       │<─────────────────────────────────│
       │                                  │
```

### 3.3 设备能力信息格式（TCP）

服务器通过 TCP 控制通道发送设备能力，让客户端选择最佳配置。

**注意**: 设备名 (device_name) 已通过 sendDeviceMeta 在能力协商之前发送，不包含在此消息中。

```
偏移量   字段                    大小      类型      描述
------   ----                    ----      ----      ----
0        screen_width            4        uint32    屏幕宽度（big-endian）
4        screen_height           4        uint32    屏幕高度（big-endian）
8        video_encoder_count     1        uint8     视频编码器数量
9        video_encoders          N*12     bytes     视频编码器列表
         - codec_id              4        uint32    编码器ID
         - flags                 4        uint32    标志位 (bit0=硬件, bit1=软件)
         - priority              4        uint32    推荐优先级（越小越好）
9+N*12   audio_encoder_count     1        uint8     音频编码器数量
10+N*12  audio_encoders          M*12     bytes     音频编码器列表（格式同上）
```

**编码器 ID 对照表**:
```
视频编码器:
  0x68323634 = "h264"
  0x68323635 = "h265"
  0x00617631 = "av1"

音频编码器:
  0x6f707573 = "opus"
  0x00000003 = "aac"
  0x00000004 = "flac"
```

**编码器标志位**:
```
bit 0: 硬件编码器 (性能好，推荐)
bit 1: 软件编码器 (兼容性好)
```

### 3.4 客户端配置格式（TCP）

客户端选择配置后发送给服务器。

```
偏移量   字段                    大小      类型      描述
------   ----                    ----      ----      ----
0        video_codec_id          4        uint32    选择的视频编码器ID
4        audio_codec_id          4        uint32    选择的音频编码器ID
8        video_bitrate           4        uint32    视频码率 (bps)
12       audio_bitrate           4        uint32    音频码率 (bps)
16       max_fps                 4        uint32    最大帧率
20       config_flags            4        uint32    配置标志位
24       reserved                4        uint32    保留字段（填0）
28       i_frame_interval        4        float     I帧间隔（秒，IEEE 754 big-endian）

总大小: 32 字节
```

**配置标志位**:
```
bit 0:  启用音频
bit 1:  启用视频
bit 2:  CBR 模式 (0=VBR, 1=CBR)
bit 3:  启用视频 FEC
bit 4:  启用音频 FEC
```

### 3.5 旧版网络 UDP 模式（无能力协商）

```
┌─────────────┐                    ┌─────────────┐
│   客户端     │                    │   服务端     │
└──────┬──────┘                    └──────┬──────┘
       │                                  │
       │  1. 绑定 UDP 视频端口             │
       │  (等待接收)                       │
       │                                  │
       │  2. 连接 TCP 控制端口             │
       │─────────────────────────────────>│
       │                                  │
       │  3. 接收 dummy byte (TCP, 1字节)  │
       │<─────────────────────────────────│
       │                                  │
       │  4. 接收设备名 (TCP, 64字节)       │
       │<─────────────────────────────────│
       │                                  │
       │  5. 接收视频配置包 (UDP)           │
       │  [UDP头16] [scrcpy头12] [配置12]  │
       │<─────────────────────────────────│
       │                                  │
       │  6. 开始接收视频帧 (UDP)           │
       │<─────────────────────────────────│
       │                                  │
```

**关键差异**:
- **dummy byte 和设备名**: 通过 TCP 控制通道传输
- **视频配置包**: 通过 UDP 视频通道传输，**有 scrcpy 头部**
- **视频帧**: 通过 UDP 视频通道传输，有 scrcpy 头部

### 3.3 网络模式下的数据通道分离

| 数据类型 | ADB 隧道模式 | 网络 UDP 模式 |
|---------|-------------|--------------|
| dummy byte | video_socket (TCP) | control_socket (TCP) |
| 设备名 (64字节) | video_socket (TCP) | control_socket (TCP) |
| 视频配置包 | video_socket (TCP, 原始字节) | video_socket (UDP, 带 scrcpy 头部) |
| 视频帧 | video_socket (TCP, 带 scrcpy 头部) | video_socket (UDP, 带 scrcpy 头部) |

---

## 4. 视频流协议

### 4.1 视频配置包（初始化）

**ADB 隧道模式**:
```
codec_id:    4 字节 (uint32, big-endian)
width:       4 字节 (uint32, big-endian)
height:      4 字节 (uint32, big-endian)
总计:        12 字节 (无 scrcpy 头部)
```

**网络 UDP 模式**:
```
UDP 头部:    16 字节
  sequence:  4 字节
  timestamp: 8 字节
  flags:     4 字节 (bit 1 = CONFIG)

Scrcpy 头部: 12 字节
  pts_flags: 8 字节 (0x8000000000000000, CONFIG 标志)
  size:      4 字节 (12)

配置负载:    12 字节
  codec_id:  4 字节 (uint32, big-endian)
  width:     4 字节 (uint32, big-endian)
  height:    4 字节 (uint32, big-endian)

总计:        40 字节 (UDP 发送)
            24 字节 (客户端接收，UDP 头部被剥离)
```

### 4.2 H.264/H.265 配置包（SPS/PPS）

编码器启动后会发送 SPS/PPS 配置数据，这些数据也标记为 CONFIG 包：

```
Scrcpy 头部: 12 字节
  pts_flags: 8 字节 (0x8000000000000000, CONFIG 标志)
  size:      4 字节 (SPS+PPS 数据大小)

负载:        N 字节
  SPS + PPS 数据
```

**重要**: 客户端必须正确处理这个配置包，将其与后续的关键帧合并后再送入解码器。

### 4.3 视频帧格式

```
Scrcpy 头部: 12 字节
  pts_flags: 8 字节
    - bit 63: 0 (非配置包)
    - bit 62: 1 表示关键帧，0 表示普通帧
    - bit 0-61: PTS (微秒)
  size:      4 字节 (帧数据大小)

负载:        N 字节
  H.264/H.265/AV1 编码数据
```

### 4.4 配置包合并规则（H.264/H.265）

```python
def merge_config_packet(config_data: bytes, frame_data: bytes) -> bytes:
    """
    将配置包（SPS/PPS）与关键帧合并。

    H.264/H.265 解码器需要在每个关键帧前接收 SPS/PPS。

    Args:
        config_data: SPS + PPS 数据
        frame_data:  关键帧数据

    Returns:
        合并后的数据: config_data + frame_data
    """
    return config_data + frame_data
```

---

## 5. 音频流协议

### 5.1 音频配置包

**格式与视频配置包类似**，但负载内容不同：

```
ADB 隧道模式:
  codec_id: 4 字节 (原始)

网络 UDP 模式:
  UDP 头部: 16 字节
  Scrcpy 头部: 12 字节 (CONFIG 标志)
  负载: codec_id (4 字节)
```

### 5.2 音频帧格式

```
Scrcpy 头部: 12 字节
  pts_flags: 8 字节
  size:      4 字节

负载:        N 字节
  OPUS/FLAC/AAC 编码数据
```

---

## 6. 控制通道协议

### 6.1 控制消息格式

```
类型:        1 字节
长度:        4 字节 (uint32, big-endian)
数据:        N 字节
```

### 6.2 消息类型定义

#### 客户端 -> 服务端 (ControlMessage)

| 类型值 | 名称 | 描述 |
|-------|------|------|
| 0 | TYPE_INJECT_KEYCODE | 键盘事件 |
| 1 | TYPE_INJECT_TEXT | 文本输入 |
| 2 | TYPE_INJECT_TOUCH_EVENT | 触摸事件 |
| 3 | TYPE_INJECT_SCROLL_EVENT | 滚轮事件 |
| 4 | TYPE_BACK_OR_SCREEN_ON | 返回键/唤醒屏幕 |
| 5 | TYPE_EXPAND_NOTIFICATION_PANEL | 展开通知面板 |
| 6 | TYPE_EXPAND_SETTINGS_PANEL | 展开设置面板 |
| 7 | TYPE_COLLAPSE_PANELS | 收起面板 |
| 8 | TYPE_GET_CLIPBOARD | 获取剪贴板 |
| 9 | TYPE_SET_CLIPBOARD | 设置剪贴板 |
| 10 | TYPE_SET_DISPLAY_POWER | 屏幕电源控制 |
| 11 | TYPE_ROTATE_DEVICE | 旋转设备 |
| 12-14 | TYPE_UHID_* | UHID 设备操作 |
| 15 | TYPE_OPEN_HARD_KEYBOARD_SETTINGS | 打开硬键盘设置 |
| 16 | TYPE_START_APP | 启动应用 |
| 17 | TYPE_RESET_VIDEO | 重置视频/请求关键帧 |
| 18 | TYPE_SCREENSHOT | 截图请求 |
| 19 | TYPE_GET_APP_LIST | 获取应用列表 |
| 20-24 | TYPE_*_VIDEO/AUDIO | 媒体流控制 |
| **25** | **TYPE_PING** | **心跳请求 [v1.3新增]** |

#### 服务端 -> 客户端 (DeviceMessage)

| 类型值 | 名称 | 描述 |
|-------|------|------|
| 0 | TYPE_CLIPBOARD | 剪贴板内容 |
| 1 | TYPE_ACK_CLIPBOARD | 剪贴板确认 |
| 2 | TYPE_UHID_OUTPUT | UHID 输出 |
| 3 | TYPE_APP_LIST | 应用列表 |
| 4 | TYPE_SCREENSHOT | 截图数据 |
| **5** | **TYPE_PONG** | **心跳响应 [v1.3新增]** |

### 6.3 心跳机制 (v1.3)

TCP 控制通道心跳用于检测连接存活状态。

#### 消息格式

```
PING (Client -> Server):
  类型:        1 字节 = 25 (0x19)
  timestamp:   8 字节 (int64, big-endian, 微秒)
  总大小:      9 字节

PONG (Server -> Client):
  类型:        1 字节 = 5 (0x05)
  timestamp:   8 字节 (int64, big-endian, 回显 PING 时间戳)
  总大小:      9 字节
```

#### 工作流程

```
┌─────────────────┐                    ┌─────────────────┐
│    客户端        │                    │    服务端        │
└────────┬────────┘                    └────────┬────────┘
         │                                      │
         │  ──── PING (每2秒) ────────────────>│
         │      timestamp: 当前时间(微秒)       │
         │                                      │ 处理 PING
         │  <─── PONG (立即响应) ──────────────│
         │      timestamp: 回显                │ 发送 PONG
         │                                      │
         │  如果 5 秒无 PONG 响应：              │
         │  → 断开连接                          │
         │                                      │
```

#### 超时参数

| 参数 | 值 | 说明 |
|------|---|------|
| PING_INTERVAL | 2.0 秒 | PING 发送间隔 |
| TIMEOUT | 5.0 秒 | 无 PONG 响应超时时间 |

#### 实现位置

| 组件 | 文件 |
|------|------|
| 服务端 PING 处理 | `Controller.java` - `handlePing()` |
| 服务端 PONG 发送 | `DeviceMessage.java` - `createPong()` |
| 客户端心跳管理器 | `heartbeat.py` - `HeartbeatManager` |
| 客户端 PING 发送 | `control.py` - `set_ping()` |
| 客户端 PONG 接收 | `device_msg.py` - `_process_pong()` |

---

## 7. 错误处理与恢复

### 7.1 常见错误场景

| 错误 | 可能原因 | 恢复策略 |
|-----|---------|---------|
| Payload size 过大 (>16MB) | 协议格式不匹配，数据错位 | 检查服务端/客户端版本，重新连接 |
| IncompleteReadError | 连接中断 | 重试或重连 |
| UDP 包丢失 | 网络不稳定 | 等待下一个关键帧 |
| 解码错误 | 配置包丢失或损坏 | 等待下一个关键帧 |

### 7.2 调试指南

#### 启用详细日志

**客户端 (Python)**:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

**服务端 (Java)**:
服务端日志通过 `Ln.d()` 输出，确保设备上启用了调试模式。

#### 关键调试点

1. **UdpPacketReader.recv()**: 显示每次读取的 buffer 状态
2. **UdpPacketReader._receive_packet()**: 显示接收到的 UDP 包详情和 hex dump
3. **StreamingVideoDemuxer._recv_packet()**: 显示解析的头部数据
4. **UdpMediaSender.sendSinglePacket()**: 显示发送的 UDP 包详情

#### 常见问题诊断

**问题: Payload size 2897478176 exceeds maximum 16777216**

这个错误表明数据解析位置错误。可能的原因：
1. 客户端初始化代码没有完全消耗第一个配置包
2. 服务端发送的数据格式与客户端期望不一致
3. UDP 包在传输过程中被损坏

**诊断步骤**:
1. 检查客户端日志中 "UdpPacketReader.recv()" 的 buffer_before 和 buffer_after 值
2. 检查 "Payload hex" 输出，确认数据格式
3. 对比服务端日志中的 "UDP packet #N" 输出
4. 确认服务端和客户端的协议版本一致

### 7.3 协议版本检查

```python
# 客户端在连接时发送版本信息
PROTOCOL_VERSION = 1

# 如果版本不匹配，服务端应断开连接并发送错误码
```

---

## 8. ADB 隧道模式 vs 网络 UDP 模式

### 8.1 为什么 ADB 隧道模式更简单？

scrcpy 的核心协议设计是基于 **TCP 流式传输** 的特性：

```
[pts_flags: 8字节] [size: 4字节] [payload: size字节]
```

**仅 12 字节头部，就这么简单！**

| TCP 提供的保证 | scrcpy 不需要做的事 |
|---------------|-------------------|
| 字节顺序保证 | ❌ 不需要序列号 |
| 可靠传输 | ❌ 不需要 ACK/重传 |
| 无限流长度 | ❌ 不需要分片 |
| 流式读取 | ✅ size 字段本身就是边界标记 |

**ADB 隧道模式数据流**：
```
服务端: [头部12][帧A] [头部12][帧B] [头部12][帧C] ...
           ↓ TCP 透明传输（自动分段、重传、排序）
客户端: 收到连续字节流，循环读取：
        1. 读 12 字节 → 解析 size
        2. 读 size 字节 → 得到完整帧
        3. 重复...
```

### 8.2 UDP 模式的挑战

UDP 没有提供 TCP 的这些保证，所以需要在应用层实现：

| UDP 缺失的特性 | 我们添加的机制 |
|---------------|---------------|
| 无顺序保证 | UDP 头部添加序列号 (seq) |
| 无可靠性 | 依赖应用层（当前实现：丢包则丢帧） |
| 单包限制 (65507字节) | 分片机制 (frag_idx + 重组) |
| 无边界 | 依赖 scrcpy 的 size 字段 + 分片重组 |

**UDP 模式数据流**：
```
服务端:
  小帧 (<65KB): [UDP头16][scrcpy头12][帧数据]
  大帧 (>65KB): [UDP头16][frag_idx=0][scrcpy头12][部分数据]
                [UDP头16][frag_idx=1][部分数据]
                ...

客户端:
  1. 收到 UDP 包，解析头部
  2. 如果是分片包 (flags bit 31 = 1):
     - 按 frag_idx 缓存分片
     - 等待所有分片到齐后重组
  3. 得到完整的 scrcpy 包，交给 VideoDemuxer
```

### 8.3 网络环境差异

| 环境 | 丢包率 | 乱序程度 | UDP 模式表现 |
|-----|-------|---------|-------------|
| 局域网 | <0.1% | 几乎无 | 与 ADB 模式无异 |
| 同运营商外网 | 0.1-1% | 轻微 | 偶有卡顿 |
| 跨运营商 | 1-5% | 明显 | 可能频繁丢帧、花屏 |
| 弱网环境 | >5% | 严重 | 体验较差，建议用 TCP |

**建议**：
- 局域网使用：UDP 模式延迟更低
- 外网使用：考虑 TCP 模式或增加前向纠错 (FEC)

### 8.4 本次修复的问题

#### 问题 1: 配置包缺少 scrcpy 头部

**症状**: 第二个 UDP 包（H.264 SPS/PPS）解析失败
```
Payload size 2897478176 exceeds maximum 16777216
```

**原因**: `Streamer.writePacket()` 检查 `sendFrameMeta`，而 `writeVideoHeader()` 无条件添加头部

**修复**: `Streamer.java` - UDP 模式下 `writePacket()` 始终添加 scrcpy 头部

#### 问题 2: 分片包格式不被客户端理解

**症状**: 关键帧（大包）解析失败
```
Payload size 1165988573 exceeds maximum 16777216
```

**原因**: 分片包有额外的 4 字节 frag_idx，但客户端没有处理

**修复**: `UdpPacketReader.py` - 实现分片检测和重组逻辑

---

## 9. 版本兼容性

### 9.1 协议变更历史

| 版本 | 日期 | 变更内容 |
|-----|------|---------|
| 1.0 | 2026-02-13 | 初始版本，定义 ADB 和 UDP 网络模式 |
| 1.1 | 2026-02-13 | **BUG修复**: UDP 模式下 `writePacket()` 必须始终添加 scrcpy 头部 |
| 1.2 | 2026-02-20 | UDP header 扩展至 24 字节，添加 send_time_ns 用于 E2E 延迟追踪 |
| 1.3 | 2026-02-20 | 添加 TCP 控制通道心跳机制 (PING/PONG) |

#### 版本 1.1 详细说明

**问题**: 在 UDP 网络模式下，`Streamer.writePacket()` 检查 `sendFrameMeta` 标志，如果为 false 则不添加 scrcpy 头部。但 `writeVideoHeader()` 无条件添加头部，导致数据包格式不一致。

**症状**:
- 第一个 UDP 包（codec_id/size）有 scrcpy 头部 ✓
- 第二个 UDP 包（H.264 SPS/PPS）没有 scrcpy 头部 ✗
- 客户端解析错误: `Payload size 2897478176 exceeds maximum`

**修复**: `Streamer.java` 中 UDP 模式的 `writePacket()` 现在始终添加 scrcpy 头部，与 `writeVideoHeader()` 行为一致。

```java
// 修复前 (BUG)
if (sendFrameMeta) {
    // 添加 scrcpy 头部
} else {
    udpSender.sendPacket(buffer.duplicate(), pts, config, keyFrame);  // 没有头部!
}

// 修复后 (正确)
// UDP 模式下始终添加 scrcpy 头部
ByteBuffer packet = ByteBuffer.allocate(12 + dataSize);
packet.putLong(ptsAndFlags);
packet.putInt(dataSize);
packet.put(buffer);
packet.flip();
udpSender.sendPacket(packet, pts, config, keyFrame);
```

### 8.2 向后兼容性规则

1. **新增字段**: 只能在数据包末尾追加，旧客户端忽略
2. **新增消息类型**: 使用未使用的类型值
3. **修改现有格式**: 必须增加协议版本号

---

## 附录 A: 数据包格式快速参考

### A.1 Scrcpy 头部（12字节）

```
+--------+--------+--------+--------+--------+--------+--------+--------+
|                    pts_flags (8 bytes, big-endian)                    |
+--------+--------+--------+--------+--------+--------+--------+--------+
|  size (4 bytes, big-endian)   |
+--------+--------+--------+--------+

pts_flags:
  bit 63: CONFIG
  bit 62: KEY_FRAME
  bits 0-61: PTS
```

### A.2 UDP 头部（16字节）

```
+--------+--------+--------+--------+
|     sequence (4 bytes, BE)        |
+--------+--------+--------+--------+--------+--------+--------+--------+
|                 timestamp (8 bytes, big-endian)                       |
+--------+--------+--------+--------+--------+--------+--------+--------+
|     flags (4 bytes, BE)           |
+--------+--------+--------+--------+

flags:
  bit 0: KEY_FRAME
  bit 1: CONFIG
  bit 31: FRAGMENTED
```

---

## 附录 B: 服务端关键代码位置

| 功能 | 文件路径 |
|------|---------|
| UDP 发送器 | `scrcpy/server/src/main/java/com/genymobile/scrcpy/udp/UdpMediaSender.java` |
| 流处理器 | `scrcpy/server/src/main/java/com/genymobile/scrcpy/device/Streamer.java` |
| 视频编码 | `scrcpy/server/src/main/java/com/genymobile/scrcpy/device/ScreenEncoder.java` |

## 附录 C: 客户端关键代码位置

| 功能 | 文件路径 |
|------|---------|
| 协议常量 | `scrcpy_py_ddlx/core/protocol.py` |
| UDP 包读取器 | `scrcpy_py_ddlx/client/udp_packet_reader.py` |
| 视频解复用器 | `scrcpy_py_ddlx/core/demuxer/video.py` |
| 连接管理 | `scrcpy_py_ddlx/client/connection.py` |
| 客户端主逻辑 | `scrcpy_py_ddlx/client/client.py` |

---

**文档维护者**: Claude AI
**确认状态**: ⚠️ 待客户端和服务端开发者确认

> 在修改代码前，请确保：
> 1. 更新本文档的相关章节
> 2. 在服务端和客户端同时实现变更
> 3. 添加版本兼容性检查（如适用）
> 4. 更新测试用例
