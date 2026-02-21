# 热连接实现方案

本文档描述热连接（持久服务端 + 客户端唤醒）的实现方案。

**前置阅读**：[服务端代码结构分析](SERVER_CODE_ANALYSIS.md)

---

## 1. 目标

```
服务器启动 → UDP监听等待唤醒
                ↓
         收到 WAKE_UP
                ↓
         接受TCP连接 → 能力协商 → 推流
                ↓
         客户端断开
                ↓
         回到UDP监听 ←── 循环
```

**核心特性**：
- 服务端持久运行，无需重复推送
- 客户端随时可唤醒并连接
- 每次连接可动态配置参数（码率、帧率、编码器等）
- 断开后自动回到等待状态

---

## 2. 服务端改造

### 2.1 Options.java - 添加新参数

```java
// 新增字段
private boolean stayAlive = false;      // 持久运行模式
private int maxConnections = -1;         // 最大连接次数 (-1 = 无限)

// 新增解析
case "stay_alive":
    options.stayAlive = Boolean.parseBoolean(value);
    break;
case "max_connections":
    options.maxConnections = Integer.parseInt(value);
    break;
```

### 2.2 Server.java - 主循环改造

```java
public static void main(String... args) {
    Options options = Options.parse(args);

    if (options.isStayAlive() && options.isNetworkMode()) {
        // 热连接模式：持久运行
        runStayAliveMode(options);
    } else {
        // 传统模式：单次连接
        runSingleMode(options);
    }
}

private static void runStayAliveMode(Options options) {
    UdpDiscoveryReceiver discovery = new UdpDiscoveryReceiver(options.getDiscoveryPort());
    int connectionCount = 0;

    try {
        while (options.getMaxConnections() < 0 || connectionCount < options.getMaxConnections()) {
            Ln.i("Waiting for wake request on port " + options.getDiscoveryPort());

            // 1. 阻塞等待唤醒
            discovery.startListening();  // 收到 WAKE_UP 后返回

            if (!discovery.isWakeRequested()) {
                continue;  // 被中断，重新等待
            }

            Ln.i("Wake request received, starting connection...");
            connectionCount++;

            // 2. 接受 TCP 连接
            try {
                DesktopConnection connection = DesktopConnection.openNetwork(
                    options.getControlPort(),
                    options.getVideoPort(),
                    options.getAudioPort(),
                    options.getVideo(),
                    options.getAudio(),
                    options.getControl(),
                    false  // no dummy byte in network mode
                );

                // 3. 执行单次会话
                runSession(options, connection);

            } catch (IOException e) {
                Ln.w("Connection error: " + e.getMessage());
            } finally {
                // 4. 清理，准备下一次连接
                Ln.i("Session ended, waiting for next wake...");
                discovery.reset();  // 重置状态
            }
        }
    } finally {
        discovery.stop();
    }
}

private static void runSession(Options options, DesktopConnection connection) throws IOException {
    // 现有的 scrcpy() 逻辑，但：
    // - 不创建新的 CleanUp
    // - 使用传入的 connection
    // - 结束时不 exit
}
```

### 2.3 UdpDiscoveryReceiver.java - 添加 reset 方法

```java
public void reset() {
    wakeRequested = false;
    clientAddress = null;
}

public void stopListening() {
    running = false;
}
```

### 2.4 DesktopConnection.java - 连接复用

```java
// 确保连接可以正确关闭并重新打开
public void shutdownAndClose() {
    shutdown();
    close();
}
```

---

## 3. 客户端改造

### 3.1 新增 udp_wake.py

```python
"""UDP wake sender for hot-connection."""

import socket
import time
from typing import Optional, Tuple

DISCOVERY_PORT = 27183
DISCOVER_REQUEST = b"SCRCPY_DISCOVER"
WAKE_REQUEST = b"WAKE_UP"
WAKE_RESPONSE = b"WAKE_ACK"
DISCOVER_RESPONSE_PREFIX = b"SCRCPY_HERE "
DISCOVERY_TIMEOUT = 2.0
WAKE_TIMEOUT = 5.0


def discover_devices(timeout: float = DISCOVERY_TIMEOUT) -> list:
    """Discover all scrcpy servers on the network."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)

    devices = []
    try:
        # Broadcast discovery
        sock.sendto(DISCOVER_REQUEST, ('<broadcast>', DISCOVERY_PORT))

        while True:
            try:
                data, addr = sock.recvfrom(1024)
                if data.startswith(DISCOVER_RESPONSE_PREFIX):
                    # Parse: "SCRCPY_HERE <device_name> <ip>"
                    parts = data[len(DISCOVER_RESPONSE_PREFIX):].decode().split()
                    if len(parts) >= 2:
                        devices.append({
                            'name': parts[0],
                            'ip': parts[1],
                            'address': addr[0]
                        })
            except socket.timeout:
                break
    finally:
        sock.close()

    return devices


def wake_server(ip: str, port: int = DISCOVERY_PORT,
                timeout: float = WAKE_TIMEOUT) -> Tuple[bool, Optional[str]]:
    """
    Wake a sleeping scrcpy server.

    Returns:
        (success, error_message)
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    try:
        # Send wake request
        sock.sendto(WAKE_REQUEST, (ip, port))
        Ln.i(f"Sent WAKE_UP to {ip}:{port}")

        # Wait for acknowledgment
        data, addr = sock.recvfrom(1024)
        if data == WAKE_RESPONSE:
            Ln.i(f"Received WAKE_ACK from {addr}")
            return True, None
        else:
            return False, f"Unexpected response: {data}"

    except socket.timeout:
        return False, "Wake timeout - server may not be in stay-alive mode"
    except Exception as e:
        return False, str(e)
    finally:
        sock.close()


def wake_and_connect(ip: str, control_port: int = 27184,
                     discovery_port: int = DISCOVERY_PORT) -> Tuple[bool, Optional[str]]:
    """
    Wake server and prepare for TCP connection.

    Returns:
        (success, error_message)
    """
    # 1. Wake the server
    success, error = wake_server(ip, discovery_port)
    if not success:
        return False, error

    # 2. Wait a moment for server to be ready
    time.sleep(0.5)

    return True, None
```

### 3.2 client.py - 添加热连接方法

```python
class ScrcpyClient:
    # ... existing code ...

    def connect_hot(self, device_ip: str = None,
                    discovery_port: int = 27183,
                    control_port: int = 27184,
                    video_port: int = 27185,
                    audio_port: int = 27186) -> bool:
        """
        Connect to a hot-connection server (wake + connect).

        Args:
            device_ip: Device IP address
            discovery_port: UDP discovery port
            control_port: TCP control port
            video_port: UDP video port
            audio_port: UDP audio port

        Returns:
            True if connected successfully
        """
        from scrcpy_py_ddlx.client.udp_wake import wake_and_connect

        # 1. Wake the server
        success, error = wake_and_connect(device_ip, discovery_port, control_port)
        if not success:
            logger.error(f"Failed to wake server: {error}")
            return False

        # 2. Update config
        self.config.host = device_ip
        self.config.control_port = control_port
        self.config.video_port = video_port
        self.config.audio_port = audio_port
        self.config.connection_mode = "network"

        # 3. Connect (skip server start)
        return self._connect_network_mode()
```

---

## 4. 使用流程

### 4.1 首次部署（推送持久服务器）

```bash
# 在手机上启动持久服务器
adb shell "CLASSPATH=/data/local/tmp/scrcpy-server \
    app_process / com.genymobile.scrcpy.Server \
    stay_alive=true \
    network_mode=true \
    control_port=27184 \
    video_port=27185 \
    audio_port=27186 \
    discovery_port=27183 \
    log_level=info"
```

### 4.2 客户端唤醒连接

```python
from scrcpy_py_ddlx import ClientConfig, ScrcpyClient

config = ClientConfig(
    codec="auto",  # 自动选择最优编码器
    bitrate=8000000,
    max_fps=60,
    audio=True
)

client = ScrcpyClient(config)

# 热连接（唤醒 + 连接）
client.connect_hot(
    device_ip="192.168.5.4",
    discovery_port=27183,
    control_port=27184
)

# ... 使用 ...

# 断开后，服务器回到等待状态
client.disconnect()

# 可以再次连接（无需重新推送）
client.connect_hot(device_ip="192.168.5.4")
```

### 4.3 命令行工具

```bash
# 发现网络上的服务器
python -m scrcpy_py_ddlx.tools.discover

# 唤醒指定服务器
python -m scrcpy_py_ddlx.tools.wake --ip 192.168.5.4

# 热连接
python tests_gui/test_network_direct.py \
    --no-push --wake \
    --ip 192.168.5.4
```

---

## 5. 实现步骤

### Phase 1: 服务端基础
1. `Options.java`: 添加 `stayAlive`, `maxConnections` 参数
2. `Server.java`: 添加 `runStayAliveMode()` 方法
3. `UdpDiscoveryReceiver.java`: 添加 `reset()`, `stopListening()` 方法

### Phase 2: 客户端唤醒
1. 创建 `udp_wake.py`: UDP 唤醒发送器
2. `client.py`: 添加 `connect_hot()` 方法
3. 测试基本唤醒和连接

### Phase 3: 集成测试
1. 更新 `test_network_direct.py` 支持 `--stay-alive` 模式
2. 测试断开后重新连接
3. 测试能力协商和动态配置

### Phase 4: 优化
1. 连接超时处理
2. 错误恢复机制
3. 日志和调试信息

---

## 6. 注意事项

### 6.1 服务端生命周期

```
启动 → UDP监听 → 唤醒 → TCP连接 → 能力协商 → 推流 → 断开 → UDP监听
         ↑                                                        |
         |_________________________循环___________________________|
```

### 6.2 参数动态配置

每次连接时通过能力协商动态配置：
- `codec`: h264/h265/av1
- `bitrate`: 码率
- `max_fps`: 帧率
- `bitrate_mode`: CBR/VBR
- `fec_enabled`: FEC 开关

### 6.3 兼容性

- `--stay-alive` 仅在 `--network-mode` 下有效
- 传统 ADB 隧道模式不受影响
- 客户端可以选择唤醒或直接连接（如果服务器已在推流）

---

## 7. 相关文档

- [能力协商协议](CAPABILITY_NEGOTIATION.md)
- [编解码能力检测](CODEC_CAPABILITY_DETECTION.md)
- [网络模式管线](NETWORK_PIPELINE.md)
- [热连接实现规范（旧）](HOT_CONNECTION_IMPLEMENTATION.md)

---

## 8. 后续优化（暂不实现）

### 8.1 带参数唤醒加速

**想法**：利用 `capability_cache.json` 缓存加速能力协商

```
当前流程：
  WAKE_UP → WAKE_ACK → TCP连接 → 发送能力 → 接收能力 → 选择配置 → 发送配置
                    └───────────────────────────────────────────────────┘
                                      能力协商（每次都做）

优化流程：
  WAKE_UP(device_serial) → WAKE_ACK(device_serial, model, ip)
       ↓
  客户端查缓存找到该设备的硬件编码器
       ↓
  TCP连接 → 直接发送配置（跳过能力查询）
       └───────────────────────────────┘
                  简化协商
```

**实现要点**：
1. WAKE_UP 可携带设备序列号（可选）
2. WAKE_ACK 返回设备信息（序列号、型号、IP）
3. 客户端根据序列号查 `capability_cache.json`
4. 如果缓存存在，直接使用缓存的编码器配置
5. 如果缓存不存在，回退到完整能力协商

**代码位置**：
- `UdpDiscoveryReceiver.java`: 扩展 WAKE 协议
- `udp_wake.py`: 支持带参数唤醒
- `client.py`: `_send_client_configuration()` 检查缓存

**注意**：这些优化暂时不实现，先完成基本热连接功能。

---

*文档创建: 2026-02-16*
*状态: 设计方案*
