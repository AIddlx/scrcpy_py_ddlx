# Server 主类改写 (Server.java)

> 服务端入口，支持传统模式和 Stay-Alive 模式

---

## 文件位置

```
scrcpy/server/src/main/java/com/genymobile/scrcpy/Server.java
```

---

## 改写内容

### 新增运行模式

```java
// 传统模式: 单次会话
private static void scrcpy(Options options)

// Stay-Alive 模式: 持续运行
private static void runStayAliveMode(Options options)
```

### UDP 发现集成

```java
// 启动 UDP 发现监听
UdpDiscoveryReceiver discovery = new UdpDiscoveryReceiver(port, false);
Thread terminateThread = new Thread(() -> discovery.listenForTerminate());
terminateThread.start();

// 监控终止请求
Thread monitorThread = new Thread(() -> {
    while (!discovery.isTerminateRequested()) {
        Thread.sleep(500);
    }
    if (discovery.isTerminateRequested()) {
        connection.close();
    }
});
```

### 网络模式支持

```java
// 创建网络连接
private static DesktopConnection createConnection(Options options) {
    if (options.isNetworkMode()) {
        return DesktopConnection.network(
            options.getControlPort(),
            options.getVideoPort(),
            options.getAudioPort()
        );
    } else {
        return DesktopConnection.usb();
    }
}
```

---

## 进程控制模式 (v1.5)

### setsid - 进程会话控制

```
网络模式始终使用 setsid
```

**作用**：让服务端进程独立于 ADB 会话

| 特性 | 说明 |
|------|------|
| **启用时机** | 网络模式**始终启用**，USB 模式不启用 |
| **目的** | 进程脱离 ADB 会话，独立运行 |
| **效果** | USB 拔插不会导致服务终止 |
| **实现** | 客户端 shell 命令：`nohup setsid sh -c '{server_cmd}'` |

**代码实现** (`scrcpy_http_mcp_server.py` 第 2583-2585 行)：

```python
# Always use setsid for network mode to survive ADB disconnect
# Without setsid, the server process will be killed when ADB session ends
shell_cmd = f"nohup setsid sh -c '{server_cmd}' > /data/local/tmp/scrcpy_server.log 2>&1 &"
```

**为什么需要 setsid**：

```
原始 scrcpy (USB 模式):
┌─────────────┐
│   ADB Shell │ ← 进程依赖 ADB 连接
│  └─ Server  │   USB 断开 → 进程终止
└─────────────┘

scrcpy-py-ddlx (网络模式):
┌─────────────┐
│  Server     │ ← 独立会话 (setsid)
│  (PID: XXX) │   USB 断开 → 进程继续
└─────────────┘
```

**与 stay_alive 的关系**：
- `setsid` 和 `stay_alive` 是**独立的**两个概念
- 网络模式下 `setsid` **始终启用**
- `stay_alive` 控制是否支持多客户端连接

---

## Stay-Alive 模式

```java
private static void runStayAliveMode(Options options) {
    while (running) {
        try {
            DesktopConnection connection = waitForConnection();
            scrcpySession(options, connection, null);
        } catch (Exception e) {
            // 继续等待下一个连接
        }
    }
}
```

**作用**：控制服务端是否支持多客户端连接

| 特性 | 说明 |
|------|------|
| **启用方式** | `stay_alive=true` 参数 |
| **目的** | 支持多个客户端先后连接 (hot-connect) |
| **效果** | 客户端断开后服务端继续运行 |
| **默认行为** | 单客户端模式 (断开后退出) |

**两种模式对比**：

| 模式 | stay_alive=false | stay_alive=true |
|------|------------------|-----------------|
| 客户端断开 | 服务退出 | 服务继续运行 |
| 多连接 | 不支持 | 支持 |
| 用途 | 单次使用场景 | 长期服务场景 |

**典型流程**：

```
stay_alive=false (默认):
客户端连接 → 传输数据 → 客户端断开 → 服务退出

stay_alive=true:
客户端1连接 → 传输数据 → 客户端1断开 → 等待...
客户端2连接 → 传输数据 → 客户端2断开 → 等待...
(直到收到终止命令)
```

---

## Options.java 扩展

### 新增参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `control_port` | int | TCP 控制端口 (27184) |
| `video_port` | int | UDP 视频端口 (27185) |
| `audio_port` | int | UDP 音频端口 (27186) |
| `fec_enabled` | boolean | 启用 FEC |
| `video_fec_enabled` | boolean | 视频 FEC |
| `audio_fec_enabled` | boolean | 音频 FEC |
| `fec_group_size` | int | FEC K 值 |
| `fec_parity_count` | int | FEC M 值 |
| `fec_mode` | String | frame/fragment |
| `auth_key_file` | String | 认证密钥路径 |
| `stay_alive` | boolean | Stay-Alive 模式 |
| `max_connections` | int | 最大连接数 (-1=无限) |
| `discovery_port` | int | UDP 发现端口 (27186) |
| `low_latency` | boolean | 低延迟模式 |
| `encoder_priority` | int | 编码器优先级 |
| `encoder_buffer` | int | 编码器缓冲 |
| `skip_frames` | boolean | 跳帧模式 |
| `bitrate_mode` | String | cbr/vbr |
| `i_frame_interval` | float | 关键帧间隔 |

---

## 启动命令示例

```bash
CLASSPATH=/data/local/tmp/scrcpy-server.apk app_process / \
  com.genymobile.scrcpy.Server 3.3.4 \
  control_port=27184 video_port=27185 audio_port=27186 \
  video_codec=h265 video_bit_rate=4000000 max_fps=60 \
  fec_enabled=true fec_group_size=4 fec_parity_count=1 \
  auth_key_file=/data/local/tmp/scrcpy-auth.key \
  stay_alive=true
```

---

## 相关文档

- [UDP 发送详解](udp_sender.md)
- [UDP 发现](discovery.md)
- [认证处理器](auth_handler.md)
