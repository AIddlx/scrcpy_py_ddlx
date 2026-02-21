# UDP 视频流 0 字节 Bug 修复报告

**日期**: 2026-02-13
**状态**: 已修复
**影响版本**: 网络模式 (TCP 控制 + UDP 媒体)

---

## 1. 现象描述

### 1.1 测试环境
- 服务端: Android 11 (realme RMX1931)
- 客户端: Windows 11, Python
- 连接模式: 网络直连 (TCP 27184 控制 + UDP 27185 视频)

### 1.2 问题表现

| 项目 | 状态 |
|------|------|
| TCP 控制连接 | ✅ 正常 |
| UDP 发送器创建 | ✅ 日志显示创建成功 |
| 服务端日志 | ✅ 显示 "UDP sent: XX bytes" |
| 客户端接收 | ❌ 收到 0 字节数据包 |

**客户端日志**:
```
Received 0 bytes from ('192.168.5.4', 39596)
Received 0 bytes from ('192.168.5.4', 39596)
... (持续收到 0 字节包)
```

**服务端日志**:
```
UDP sendPacket: size=46272, ts=84669908853, config=false, keyFrame=false
UDP sendSinglePacket: packetSize=0, bufferLen=46288  ← 问题所在！
UDP sent successfully
```

---

## 2. 根本原因

### 2.1 Bug 位置

文件: `scrcpy/server/src/main/java/com/genymobile/scrcpy/udp/UdpMediaSender.java`

### 2.2 代码问题

```java
// 原始代码 (有 Bug)
private void sendSinglePacket(ByteBuffer data, long timestamp, long flags) throws IOException {
    int dataSize = data.remaining();
    ByteBuffer packet = ByteBuffer.allocate(HEADER_SIZE + dataSize);
    packet.putInt(sequence++);
    packet.putLong(timestamp);
    packet.putInt((int) flags);
    packet.put(data);

    int packetSize = packet.remaining();  // ← Bug: remaining() 返回 0!
    byte[] packetData = packet.array();

    DatagramPacket dp = new DatagramPacket(packetData, packetSize, clientAddress, clientPort);
    socket.send(dp);  // 发送 0 字节
}
```

### 2.3 技术分析

**ByteBuffer 工作原理**:

| 操作 | position | limit | remaining() |
|------|----------|-------|-------------|
| `allocate(100)` | 0 | 100 | 100 |
| `putInt(x)` | 4 | 100 | 96 |
| `putLong(x)` | 12 | 100 | 88 |
| `put(data)` | 88 | 100 | **12** (非 0) |
| `flip()` | 0 | 88 | **88** |

**问题**:
- 写入数据后，`position` 移动到末尾
- `remaining()` = `limit - position` = 很小的值或 0
- 未调用 `flip()` 前，无法正确读取已写入的数据

**为什么看起来发送成功**:
- `DatagramPacket` 使用 `packet.array()` 和 `packetSize`
- `packet.array()` 返回整个底层数组 (正确)
- 但 `packetSize = packet.remaining()` = 0 (错误)
- 结果: 发送了一个 0 字节的 UDP 包

---

## 3. 修复方案

### 3.1 修复代码

```java
// 修复后的代码
private void sendSinglePacket(ByteBuffer data, long timestamp, long flags) throws IOException {
    int dataSize = data.remaining();
    ByteBuffer packet = ByteBuffer.allocate(HEADER_SIZE + dataSize);
    packet.putInt(sequence++);
    packet.putLong(timestamp);
    packet.putInt((int) flags);
    packet.put(data);

    // 关键修复: flip() 将 position 重置为 0，limit 设置为当前位置
    packet.flip();

    int packetSize = packet.remaining();  // 现在返回正确的值
    byte[] packetData = packet.array();

    DatagramPacket dp = new DatagramPacket(packetData, packetSize, clientAddress, clientPort);
    socket.send(dp);
}
```

### 3.2 同样修复 `sendFragmented()`

```java
private void sendFragmented(ByteBuffer data, long timestamp, long flags) throws IOException {
    // ...
    while (data.hasRemaining()) {
        ByteBuffer chunk = ByteBuffer.allocate(HEADER_SIZE + 4 + chunkSize);
        chunk.putInt(sequence++);
        chunk.putLong(timestamp);
        chunk.putInt((int) fragFlags);
        chunk.putInt(fragmentIndex++);
        chunk.put(temp);

        // 关键修复
        chunk.flip();

        DatagramPacket dp = new DatagramPacket(chunk.array(), chunk.remaining(), clientAddress, clientPort);
        socket.send(dp);
    }
}
```

---

## 4. 修复验证

### 4.1 测试结果

**修复后客户端日志**:
```
Received 48 bytes from ('192.168.5.4', 41889) (seq: 1)
Received 65507 bytes from ('192.168.5.4', 41889) (seq: 2)
Received 5877 bytes from ('192.168.5.4', 41889) (seq: 3)
Received 2528 bytes from ('192.168.5.4', 41889) (seq: 4)
Received 1792 bytes from ('192.168.5.4', 41889) (seq: 5)
Got enough packets, stopping.

Summary: 5 valid packets, 0 empty packets ignored
```

### 4.2 验证项目

| 项目 | 修复前 | 修复后 |
|------|--------|--------|
| 客户端收到有效数据 | ❌ 0 字节 | ✅ 正常 |
| 大帧分片传输 | ❌ 失败 | ✅ 正常 (65507 bytes) |
| 空包过滤 | 30+ 个空包 | 0 个空包 |
| 视频流连续性 | ❌ 中断 | ✅ 连续 |

---

## 5. 经验教训

### 5.1 ByteBuffer 最佳实践

```java
// 写入模式 → 读取模式 必须调用 flip()
ByteBuffer buffer = ByteBuffer.allocate(1024);
buffer.putInt(123);
buffer.putLong(456L);
buffer.put(data);

buffer.flip();  // 必须！准备读取

while (buffer.hasRemaining()) {
    channel.write(buffer);
}
```

### 5.2 调试技巧

1. **添加详细日志**: 打印 `remaining()`, `position`, `limit` 等状态
2. **验证发送数据**: 在 `send()` 前检查数据包大小
3. **对比分析**: 比较预期的 `packetSize` 与实际发送的值

### 5.3 代码审查要点

- 使用 `ByteBuffer` 后是否调用了 `flip()` 或 `rewind()`
- `remaining()` 的返回值是否符合预期
- 网络发送前是否验证了数据长度

---

## 6. 相关文件

| 文件 | 修改类型 |
|------|----------|
| `udp/UdpMediaSender.java` | Bug 修复 |
| `device/Streamer.java` | UDP 模式支持 |

---

## 7. Git Commit

```
fix: UDP video stream sending 0-byte packets

Root cause: ByteBuffer.remaining() returns 0 after writing data
without calling flip() first.

Fix: Add packet.flip() and chunk.flip() before reading remaining().

- sendSinglePacket(): add packet.flip()
- sendFragmented(): add chunk.flip()

Verified: Client now receives valid UDP packets with correct sizes.
```
