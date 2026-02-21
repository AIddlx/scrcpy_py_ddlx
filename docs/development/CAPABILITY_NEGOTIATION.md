# 能力协商协议实现文档

> **版本**: 1.0
> **日期**: 2026-02-15
> **状态**: 已实现

## 概述

能力协商协议允许客户端在连接时获取设备支持的能力（编码器、屏幕尺寸等），然后根据这些能力选择最佳配置。这解决了传统方式中客户端必须预先知道设备能力的问题。

## 问题背景

### 传统方式的问题

```
客户端启动 → 指定编码器参数 → 服务端启动 → 流传输
```

**问题**:
1. 客户端不知道设备支持哪些编码器
2. 可能选择了设备不支持的编码器导致失败
3. 无法根据设备能力智能选择最佳编码器（如优先H.265）
4. 参数必须在启动时一次性指定，无法动态调整

### 能力协商的解决方案

```
服务端启动 → 客户端连接 → 获取设备能力 → 选择最佳配置 → 流传输
```

**优势**:
1. 客户端可智能选择设备支持的最佳编码器
2. 支持动态配置（码率、FPS、CBR/VBR等）
3. 为未来的热连接、重连功能奠定基础

## 协议流程

```
┌─────────────┐                    ┌─────────────┐
│   客户端     │                    │   服务端     │
└──────┬──────┘                    └──────┬──────┘
       │                                  │
       │  1. 连接 TCP 控制端口             │
       │─────────────────────────────────>│
       │                                  │
       │  2. 接收 dummy byte (1字节)       │
       │<─────────────────────────────────│
       │                                  │
       │  3. 接收设备名 (64字节)            │
       │<─────────────────────────────────│
       │                                  │
       │  4. 接收设备能力 (TCP)            │
       │<─────────────────────────────────│
       │     - 屏幕尺寸                    │
       │     - 视频编码器列表               │
       │     - 音频编码器列表               │
       │                                  │
       │  5. 发送客户端配置 (TCP, 32字节)   │
       │─────────────────────────────────>│
       │     - 选择的编码器                │
       │     - 码率、帧率                  │
       │     - CBR/VBR 模式                │
       │     - I帧间隔                     │
       │                                  │
       │  6. 接收视频配置包 (UDP)           │
       │<─────────────────────────────────│
       │                                  │
       │  7. 开始接收视频/音频流            │
       │<─────────────────────────────────│
       │                                  │
```

## 数据格式

### 设备能力信息 (服务端 → 客户端)

| 字段 | 大小 | 类型 | 描述 |
|-----|------|------|------|
| screen_width | 4 | uint32 | 屏幕宽度 |
| screen_height | 4 | uint32 | 屏幕高度 |
| video_encoder_count | 1 | uint8 | 视频编码器数量 |
| video_encoders | N*12 | bytes | 视频编码器列表 |
| audio_encoder_count | 1 | uint8 | 音频编码器数量 |
| audio_encoders | M*12 | bytes | 音频编码器列表 |

每个编码器 (12字节):
| 字段 | 大小 | 类型 | 描述 |
|-----|------|------|------|
| codec_id | 4 | uint32 | 编码器ID |
| flags | 4 | uint32 | 标志位 (bit0=硬件, bit1=软件) |
| priority | 4 | uint32 | 优先级 (越小越好) |

### 客户端配置 (客户端 → 服务端)

| 字段 | 大小 | 类型 | 描述 |
|-----|------|------|------|
| video_codec_id | 4 | uint32 | 视频编码器ID |
| audio_codec_id | 4 | uint32 | 音频编码器ID |
| video_bitrate | 4 | uint32 | 视频码率 (bps) |
| audio_bitrate | 4 | uint32 | 音频码率 (bps) |
| max_fps | 4 | uint32 | 最大帧率 |
| config_flags | 4 | uint32 | 配置标志位 |
| reserved | 4 | uint32 | 保留 (填0) |
| i_frame_interval | 4 | float | I帧间隔 (秒) |

**config_flags 位定义**:
| 位 | 含义 |
|---|------|
| 0 | 启用音频 |
| 1 | 启用视频 |
| 2 | CBR模式 (0=VBR, 1=CBR) |
| 3 | 启用视频FEC |
| 4 | 启用音频FEC |

### 编码器ID对照表

**视频编码器**:
| ID | 名称 |
|---|------|
| 0x68323634 | h264 |
| 0x68323635 | h265 (hevc) |
| 0x00617631 | av1 |

**音频编码器**:
| ID | 名称 |
|---|------|
| 0x6f707573 | opus |
| 0x00000003 | aac |
| 0x00000004 | flac |

## 代码实现

### 服务端文件

| 文件 | 功能 |
|-----|------|
| `CapabilityNegotiation.java` | 协议常量、编码器查询、配置解析 |
| `DesktopConnection.java` | 能力发送、配置接收 |
| `Server.java` | 能力协商流程控制 |
| `Options.java` | 应用客户端配置 |

### 客户端文件

| 文件 | 功能 |
|-----|------|
| `negotiation.py` | 协议常量、能力解析、配置序列化 |
| `client.py` | 能力协商流程、配置选择 |

## 编码器选择策略

### 视频编码器

优先级: **AV1 > H.265 > H.264**

优先选择硬件编码器。

```python
def select_best_video_codec(capabilities):
    priority_order = [AV1, H265, H264]
    for codec in priority_order:
        # 优先找硬件编码器
        for encoder in capabilities.video_encoders:
            if encoder.codec_id == codec and encoder.is_hardware():
                return codec
    return H264  # 默认
```

### 音频编码器

优先级: **OPUS > AAC > FLAC**

## 测试验证

### 客户端日志

```
Device name: RMX1931
Device capabilities: screen=1080x2400, video_encoders=6, audio_encoders=5
Available video codecs: ['h265', 'h265', 'h264', 'h265', 'h264', 'h264']
Available audio codecs: ['opus', 'aac', 'aac', 'flac', 'flac']
Sending client config: video=h265, audio=opus, video_bitrate=4000000, max_fps=60, cbr=False
Client configuration sent successfully
```

### 服务端日志

```
Sending device capabilities to client...
Device capabilities sent
Waiting for client configuration...
Received client configuration: video=h265, audio=opus, video_bitrate=4000000, cbr=false
Applied client video codec: h265
Applied client audio codec: opus
Applied client bitrates: video=4000000, audio=128000
Applied client max fps: 60.0
Applied client I-frame interval: 10.0
Using video encoder: 'OMX.qcom.video.encoder.hevc'
Using audio encoder: 'c2.android.opus.encoder'
```

## 未来增强

### 热连接支持 (TODO)

当前实现在客户端断开后服务端会退出。未来可支持:

```
while (server_running) {
    connection = accept_connection();
    capabilities = send_capabilities();
    config = receive_client_config();
    apply_config(config);
    start_streaming();
    wait_for_disconnect();
    stop_streaming();
    // 准备接受下一次连接
}
```

**优势**:
1. 服务端持久运行，无需每次重启
2. 客户端可随时连接/断开
3. 每次连接可使用不同参数
4. 支持多客户端轮流连接

### 运行时配置切换 (TODO)

在流传输过程中动态调整参数:
1. 动态调整码率
2. 动态切换CBR/VBR
3. 动态调整帧率

## 相关文档

- [PROTOCOL_SPEC.md](../PROTOCOL_SPEC.md) - 完整协议规范
- [CLAUDE.md](../../CLAUDE.md) - 项目指南

## 版本历史

| 版本 | 日期 | 变更 |
|-----|------|------|
| 1.0 | 2026-02-15 | 初始实现 |
