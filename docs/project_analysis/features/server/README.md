# 服务端 (Android/Java)

> 运行在 Android 设备上的服务端组件，基于 scrcpy 大量改写

---

## 改写概览

本项目对 scrcpy 服务端进行了大量改写和扩展：

### 新增模块

| 模块 | 文件 | 功能 |
|------|------|------|
| [UDP 媒体发送](udp_sender.md) | `udp/UdpMediaSender.java` | UDP 视频音频发送，FEC 支持 |
| [FEC 编码器](fec_encoder.md) | `udp/SimpleXorFecEncoder.java` | XOR 前向纠错编码 |
| [UDP 发现](discovery.md) | `udp/UdpDiscoveryReceiver.java` | 设备发现 + 远程终止 |
| [文件服务器](file_server.md) | `file/FileServer.java` | 网络文件传输服务 |
| [文件通道处理](file_server.md) | `file/FileChannelHandler.java` | TCP 文件通道 |
| [认证处理器](auth_handler.md) | `AuthHandler.java` | HMAC-SHA256 认证 |

### 改写模块

| 模块 | 文件 | 改写内容 |
|------|------|---------|
| [Server 主类](server_main.md) | `Server.java` | stay-alive 模式、UDP 发现集成 |
| [Options](server_main.md) | `Options.java` | 大量新参数 (FEC/认证/网络端口等) |
| SurfaceEncoder | `video/SurfaceEncoder.java` | UDP 发送集成 |

---

## 目录结构

```
scrcpy/server/src/main/java/com/genymobile/scrcpy/
├── Server.java              # 主入口 (改写)
├── Options.java             # 配置解析 (扩展)
├── AuthHandler.java         # 认证处理 (新增)
│
├── udp/                     # UDP 模块 (新增)
│   ├── UdpMediaSender.java       # UDP 媒体发送
│   ├── SimpleXorFecEncoder.java  # FEC 编码器
│   └── UdpDiscoveryReceiver.java # UDP 发现/终止
│
├── file/                    # 文件传输 (新增)
│   ├── FileServer.java          # 文件服务器
│   ├── FileChannelHandler.java  # 文件通道处理
│   └── FileCommands.java        # 命令常量
│
├── video/                   # 视频模块 (改写)
│   ├── SurfaceEncoder.java      # 编码器 (UDP 集成)
│   └── ...
│
├── control/                 # 控制模块
│   └── ...
│
├── audio/                   # 音频模块
│   └── ...
│
└── device/                  # 设备模块 (改写)
    ├── Streamer.java            # 流发送器
    └── CapabilityNegotiation.java
```

---

## 新增功能详解

### 1. UDP 媒体发送 (UdpMediaSender)

```
原始 scrcpy: 仅支持 ADB Tunnel (TCP)
本项目:     支持 UDP 直传 + FEC 纠错
```

关键特性：
- 24 字节 UDP Header (seq + pts + flags + send_time_ns)
- 大帧分片传输
- FEC 校验包生成
- E2E 延迟追踪

### 2. FEC 编码器 (SimpleXorFecEncoder)

```
K 帧数据 → 1 帧校验
丢失 1 帧 → 可恢复
```

参数：
- K: 每组数据帧数 (默认 4)
- M: 每组校验帧数 (默认 1)

### 3. UDP 发现/终止 (UdpDiscoveryReceiver)

功能：
- 响应设备发现请求
- 接收远程终止命令
- 支持服务器状态查询

### 4. 文件服务器 (FileServer)

```
第4条 TCP 通道
├── CMD_LIST_DIR (0x01)
├── CMD_PUSH_FILE (0x02)
├── CMD_PULL_FILE (0x03)
└── CMD_DELETE_FILE (0x04)
```

### 5. 认证处理器 (AuthHandler)

```
Challenge-Response 协议
├── TYPE_CHALLENGE (0xF0) - 32 字节随机数
├── TYPE_RESPONSE (0xF1) - HMAC-SHA256
└── TYPE_AUTH_RESULT (0xF2) - 认证结果
```

---

## Server.java 改写

### 新增模式

```java
// 传统模式: 单次会话
private static void scrcpy(Options options)

// Stay-Alive 模式: 持续运行，接受多次连接
private static void runStayAliveMode(Options options)
```

### 进程控制 (setsid vs stay_alive)

**v1.5 关键变更**：明确区分两个独立的概念

| 特性 | setsid | stay_alive |
|------|--------|------------|
| **作用** | 进程会话控制 | 多客户端连接控制 |
| **启用时机** | 网络模式**始终启用** | 需要时启用 (`stay_alive=true`) |
| **目的** | 让服务端独立于 ADB 会话 | 支持多客户端先后连接 |
| **效果** | USB 拔插不会终止服务 | 客户端断开后服务继续运行 |
| **依赖关系** | 与 stay_alive 无关 | 与 setsid 无关 |

```
网络模式默认行为:
┌─────────────────────────────────────┐
│  Server (setsid=true)               │ ← 独立于 ADB 会话
│  ┌───────────────────────────────┐  │
│  │ stay_alive=false              │  │ ← 单客户端 (默认)
│  │ 客户端断开 → 服务退出         │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘

多客户端服务模式:
┌─────────────────────────────────────┐
│  Server (setsid=true)               │ ← 独立于 ADB 会话
│  ┌───────────────────────────────┐  │
│  │ stay_alive=true               │  │ ← 多客户端模式
│  │ 客户端断开 → 等待下一个连接   │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

### UDP 发现集成

```java
// 启动 UDP 发现监听器
UdpDiscoveryReceiver discovery = new UdpDiscoveryReceiver(port, false);
discovery.listenForTerminate();

// 监控终止请求
if (discovery.isTerminateRequested()) {
    connection.close();
}
```

---

## Options.java 扩展

### 新增参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `control_port` | int | TCP 控制端口 |
| `video_port` | int | UDP 视频端口 |
| `audio_port` | int | UDP 音频端口 |
| `fec_enabled` | boolean | 启用 FEC |
| `fec_group_size` | int | FEC 组大小 K |
| `fec_parity_count` | int | FEC 校验数 M |
| `fec_mode` | String | FEC 模式 |
| `auth_key_file` | String | 认证密钥文件 |
| `stay_alive` | boolean | 多客户端模式 |
| `max_connections` | int | 最大连接数 |
| `discovery_port` | int | UDP 发现端口 |

### setsid 说明

网络模式下，客户端通过 shell 命令中的 `setsid` 创建独立会话：

**代码实现** (`scrcpy_http_mcp_server.py` 第 2583-2585 行)：

```python
# Always use setsid for network mode to survive ADB disconnect
# Without setsid, the server process will be killed when ADB session ends
shell_cmd = f"nohup setsid sh -c '{server_cmd}' > /data/local/tmp/scrcpy_server.log 2>&1 &"
```

**效果**：
- 进程获得新的会话 ID (SID)
- 不再依赖 ADB 连接
- USB 拔插不影响服务运行

---

## 相关文档

- [UDP 发送详解](udp_sender.md)
- [FEC 编码器](fec_encoder.md)
- [UDP 发现](discovery.md)
- [文件服务器](file_server.md)
- [认证处理器](auth_handler.md)
- [Server 主类](server_main.md)
