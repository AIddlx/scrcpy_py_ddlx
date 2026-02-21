# FEC (前向纠错) 修复经验总结

**日期**: 2026-02-13
**问题**: UDP网络模式下快速滑动时出现画面马赛克
**解决状态**: ✅ 已解决

---

## 问题背景

### 症状
- 快速滑动屏幕时出现画面马赛克/零散碎片
- 滑动越快，马赛克越严重
- 静止后画面恢复正常

### 根本原因分析

经过深入调试，发现多个层面的问题：

1. **FEC分组与帧不对齐**
   - 原设计：按固定包数(K=4)分组
   - 问题：一个FEC组可能包含来自不同帧的包
   - 后果：跨帧XOR运算产生无效数据

2. **total_data字段为0**
   - 服务端发送data packet时不知道帧的总包数
   - 客户端收到`total_data=0`后无法判断组是否完整
   - 导致FEC组立即过期

3. **ByteBuffer.duplicate()问题**
   - Java的`duplicate()`共享底层数组
   - 原始buffer被读取后影响存储的数据
   - 导致parity计算时数据为空

---

## 修复方案

### 1. 帧基础FEC分组 (服务端)

**文件**: `scrcpy/server/src/main/java/com/genymobile/scrcpy/udp/SimpleXorFecEncoder.java`

**修改前**:
```java
// 按固定包数分组
private int groupSize = 4;
```

**修改后**:
```java
// 使用帧序列号作为group_id
public void setGroupId(int groupId) {
    this.currentGroupId = groupId;
}

// 动态获取实际包数
public List<ByteBuffer> generateParityPackets() {
    int groupSize = currentGroup.size(); // 实际包数
    // ...
}
```

**关键点**:
- 每帧有独立的FEC组
- 新帧开始时终结前一帧的FEC组
- parity packet包含正确的total_data

### 2. 服务端帧边界检测

**文件**: `scrcpy/server/src/main/java/com/genymobile/scrcpy/udp/UdpMediaSender.java`

```java
public void sendPacketWithFec(ByteBuffer data, long timestamp, ...) {
    // 检测帧边界（通过timestamp变化）
    boolean isNewFrame = (lastTimestamp != timestamp);
    if (isNewFrame) {
        // 终结前一帧的FEC组
        if (fecEncoder.hasIncompleteGroup()) {
            int dataPacketCount = fecEncoder.getCurrentGroupSize(); // 先获取！
            List<ByteBuffer> parityPackets = fecEncoder.generateParityPackets();
            // 发送parity...
        }
        // 开始新帧
        frameSeq++;
        fecEncoder.setGroupId(frameSeq);
        lastTimestamp = timestamp;
    }
    // 处理当前包...
}
```

### 3. 客户端从Parity更新total_data

**文件**: `scrcpy_py_ddlx/core/demuxer/fec.py`

```python
def add_parity_packet(self, group_id, parity_idx, total_data, ...):
    group = self._groups[group_id]

    # 关键修复：从parity packet更新正确的total_data
    if total_data > 0 and group.total_data_packets == 0:
        group.total_data_packets = total_data
        logger.debug(f"FEC group {group_id} total_data updated from parity: {total_data}")
```

### 4. 处理total_data=0的情况

**文件**: `scrcpy_py_ddlx/core/demuxer/fec.py`

```python
@property
def is_complete(self) -> bool:
    if self.total_data_packets == 0:
        return False  # 不知道预期数量，不能判断完成
    return len(self.data_packets) == self.total_data_packets

@property
def missing_count(self) -> int:
    if self.total_data_packets == 0:
        return 0  # 不知道预期数量，无法计算缺失
    return self.total_data_packets - len(self.data_packets)

@property
def can_recover(self) -> bool:
    if self.total_data_packets == 0:
        return False  # 不知道预期数量，无法恢复
    return len(self.parity_packets) >= self.missing_count and self.missing_count > 0
```

### 5. 服务端数据复制修复

**文件**: `scrcpy/server/src/main/java/com/genymobile/scrcpy/udp/SimpleXorFecEncoder.java`

**问题代码**:
```java
ByteBuffer savedForGroup = dataPacket.duplicate(); // 共享底层数组！
```

**修复代码**:
```java
public ByteBuffer addPacket(ByteBuffer dataPacket) {
    // 必须在wrapDataPacket消耗buffer之前复制数据！
    int dataSize = dataPacket.remaining();
    byte[] dataCopy = new byte[dataSize];
    dataPacket.get(dataCopy);
    ByteBuffer savedForGroup = ByteBuffer.wrap(dataCopy);

    // 重置position供后续使用
    dataPacket.position(dataPacket.position() - dataSize);

    // 现在可以安全地wrap
    ByteBuffer wrapped = wrapDataPacket(dataPacket, currentPacketIdx);
    currentGroup.add(savedForGroup);
    // ...
}
```

### 6. 客户端分片快速检测 (辅助优化)

**文件**: `scrcpy_py_ddlx/core/demuxer/udp_video.py`

```python
# 分片间隙检测
if actual_frag_count < expected_frag_count:
    missing = [i for i in range(expected_frag_count) if i not in buf.fragments]

    # 关键帧分片0丢失 - 立即请求关键帧
    if 0 in missing and (buf.flags & UDP_FLAG_KEY_FRAME):
        logger.warning(f"[FRAG-GAP] Key frame fragment 0 lost, sending PLI")
        self._send_pli()
        del self._fragment_buffers[ts]
        return None
```

---

## 协议规范更新

### FEC数据包头 (7字节)
```
Offset  Size  Field
0       2     group_id (帧序列号)
2       1     packet_idx (组内索引)
3       1     total_data (K，data packet中为0，parity packet中为实际值)
4       1     total_parity (M)
5       2     original_size (用于恢复时截断)
```

### FEC奇偶包头 (5字节)
```
Offset  Size  Field
0       2     group_id (帧序列号)
2       1     parity_idx
3       1     total_data (实际K值)
4       1     total_parity (M)
```

### 关键设计决策

1. **帧序列号作为group_id**
   - 每帧一个FEC组
   - 避免跨帧混淆

2. **total_data延迟确定**
   - data packet发送时为0（未知）
   - parity packet发送时为实际值
   - 客户端从parity更新

3. **帧边界检测**
   - 通过timestamp变化检测新帧
   - 新帧开始时终结前一帧

---

## 调试技巧

### 1. 日志分析

服务端关键日志：
```
FEC frame X complete, sent Y parity packets for Z data packets
```

客户端关键日志：
```
FEC group X total_data updated from parity: Y
FEC recovery succeeded for group X
```

### 2. 常见问题排查

| 症状 | 可能原因 | 检查点 |
|------|----------|--------|
| FEC组过期 | total_data=0 | parity packet是否包含正确的total_data |
| 马赛克 | 跨帧FEC | group_id是否使用帧序列号 |
| parity全零 | 数据未存储 | addPacket是否正确复制数据 |

### 3. 性能监控

```python
# 客户端统计
stats = fec_decoder.get_stats()
# groups_completed: 成功完成的组数
# groups_recovered: 通过FEC恢复的组数
# packets_recovered: 恢复的包数
# groups_failed: 失败的组数
```

---

## 遗留问题

1. **频繁操控后渲染画面增大**
   - 症状：频繁滑动后客户端渲染画面巨幅增大
   - 状态：待后续处理
   - 可能原因：帧队列积压/解码器缓冲问题

---

## 修改文件清单

| 文件 | 修改类型 |
|------|----------|
| `SimpleXorFecEncoder.java` | 重写：帧基础分组 |
| `UdpMediaSender.java` | 新增：帧边界检测、分片FEC |
| `fec.py` | 修复：total_data更新、属性检查 |
| `udp_video.py` | 新增：分片快速检测 |
| `connection.py` | 增大：UDP接收缓冲区(16MB) |

---

## 2026-02-20 修复: EMSGSIZE 错误 (Parity 分片)

### 问题描述

**症状**:
- 服务端报错: `java.io.IOException: sendto failed: EMSGSIZE (Message too long)`
- FEC parity packets 无法发送（大小超过 120KB）
- 客户端从未收到 parity packets
- 帧率从 ~45fps 下降到 ~12fps

**根本原因**:
- Frame-level FEC 将 4 帧数据进行 XOR 运算生成 parity
- 当 4 帧中有大帧时（如快速运动场景），parity 大小可达 120KB+
- UDP 最大有效载荷为 ~65KB (65507 bytes)
- Parity packet 超过 MTU 导致发送失败

### 解决方案: Parity Packet 分片

#### 服务端修改

**文件**: `scrcpy/server/src/main/java/com/genymobile/scrcpy/udp/UdpMediaSender.java`

**新增方法**:
```java
/**
 * Send a FEC parity packet.
 * Handles fragmentation if parity data exceeds UDP MTU.
 */
public void sendFecParityPacket(ByteBuffer fecParity, long timestamp) throws IOException {
    int dataSize = fecParity.remaining();
    int maxParityPayload = MAX_PACKET_SIZE - HEADER_SIZE - SimpleXorFecEncoder.getFecParityHeaderSize();

    if (dataSize <= maxParityPayload) {
        // Fits in single packet
        sendSingleFecParityPacket(fecParity, timestamp);
    } else {
        // Need to fragment
        sendFragmentedFecParityPacket(fecParity, timestamp);
    }
}

/**
 * Send a fragmented FEC parity packet.
 * Parity packets can be large (100KB+) and must be fragmented to fit UDP MTU.
 */
private void sendFragmentedFecParityPacket(ByteBuffer fecParity, long timestamp) throws IOException {
    long flags = FLAG_FEC_PARITY | (1L << 31); // FEC_PARITY + FRAGMENTED
    int maxFragmentData = MAX_PACKET_SIZE - HEADER_SIZE
                        - SimpleXorFecEncoder.getFecParityHeaderSize() - 4;
    int totalSize = fecParity.remaining();
    int fragmentIndex = 0;

    Ln.d("FEC parity fragmenting: total=" + totalSize + " bytes");

    while (fecParity.hasRemaining()) {
        int chunkSize = Math.min(fecParity.remaining(), maxFragmentData);

        ByteBuffer packet = ByteBuffer.allocate(HEADER_SIZE + chunkSize + 4);
        packet.putInt(sequence++);
        packet.putLong(timestamp);
        packet.putInt((int) flags);
        packet.putInt(fragmentIndex);

        byte[] temp = new byte[chunkSize];
        fecParity.get(temp);
        packet.put(temp);

        packet.flip();
        // ... send packet
        fragmentIndex++;
    }

    Ln.d("FEC parity fragments sent: " + fragmentIndex + " fragments");
}
```

#### 客户端修改

**文件**: `scrcpy_py_ddlx/core/demuxer/udp_video.py`

**新增处理逻辑**:
```python
# Dispatch 分片 parity packets
if udp_header.is_fec_parity and udp_header.is_fragmented:
    self._handle_fec_parity_fragment(udp_header, payload)
elif udp_header.is_fec_parity:
    self._handle_fec_parity(udp_header, payload)

def _handle_fec_parity_fragment(self, udp_header, payload):
    """
    Handle fragmented FEC parity packet.

    Format: [frag_idx: 4B] [FEC Header: 5B] [Parity Data: NB]
    """
    frag_idx = struct.unpack('>I', payload[:4])[0]
    frag_data = payload[4:]

    # Use timestamp as key for reassembly
    ts = udp_header.timestamp

    if ts not in self._parity_fragment_buffers:
        self._parity_fragment_buffers[ts] = {}

    self._parity_fragment_buffers[ts][frag_idx] = frag_data

    # Check if reassembly is complete
    buffer = self._parity_fragment_buffers[ts]
    max_idx = max(buffer.keys())

    # Check if we have all consecutive fragments
    complete = all(i in buffer for i in range(max_idx + 1))

    if complete:
        reassembled = b''.join(buffer[i] for i in range(max_idx + 1))
        del self._parity_fragment_buffers[ts]
        self._handle_fec_parity(udp_header, reassembled)
```

### 测试结果

**修复前**:
| 指标 | 值 |
|------|-----|
| EMSGSIZE 错误 | 频繁发生 |
| 帧率 | ~12 fps |
| Parity 发送 | 失败 |

**修复后**:
| 指标 | 值 |
|------|-----|
| EMSGSIZE 错误 | 无 |
| 帧率 | ~50 fps |
| Parity 发送 | 成功分片 |

**服务端日志示例**:
```
FEC generateParity: groupId=300, frames=4, maxSize=71293
FEC parity fragmenting: total=71298 bytes into 2 fragments
FEC parity fragments sent: 2 fragments
FEC group complete: sent 1 parity for 4 frames
```

**客户端日志示例**:
```
[FEC-PARITY-FRAG] ts=105961198675, seq=8, frag_idx=0, frag_size=65482
[FEC-PARITY-FRAG] ts=105961198675, seq=9, frag_idx=1, frag_size=50451
[FEC-PARITY] Reassembled 2 fragments, total size=115933 bytes
```

### 协议更新

**分片 Parity Packet 格式**:
```
UDP Header (16 bytes):
  seq: 4B
  timestamp: 8B
  flags: 4B (FLAG_FEC_PARITY | FLAG_FRAGMENTED)

Fragment Header (4 bytes):
  frag_idx: 4B

FEC Parity Header (5 bytes):
  group_id: 2B
  parity_idx: 1B
  total_data: 1B
  total_parity: 1B

Parity Data: NB
```

### 关键设计点

1. **分片标志**: 使用 `FLAG_FEC_PARITY | FLAG_FRAGMENTED` 组合
2. **分片索引**: 4字节 fragment index，与数据分片一致
3. **重组策略**: 按 timestamp 分组，检测连续片段后重组
4. **内存管理**: 限制缓冲区数量，防止内存泄漏

---

## 2026-02-20 修复: UDP Header 扩展与 E2E 延迟追踪

### 问题背景

**需求**: 分析完整的端到端渲染延迟，找出 148ms 延迟的来源。

**挑战**: 日志中的 "UDP→consume" 只测量了客户端内部延迟（~16ms），不包含：
- Android 编码延迟
- 网络传输延迟
- 渲染延迟

### 解决方案: 扩展 UDP Header

将 UDP header 从 16 字节扩展到 24 字节，添加 `send_time_ns` 字段：

```
旧格式 (16字节): [seq:4][timestamp:8][flags:4]
新格式 (24字节): [seq:4][timestamp:8][flags:4][send_time_ns:8]
```

### 教训: 协议修改的连锁影响

**问题**: 修改后连接失败
```
ERROR - Expected config packet but got non-config packet
```

**原因**: `client.py` 中硬编码了旧的偏移量：
```python
# 旧代码
header_bytes = packet[16:28]  # ❌ 应该是 24:36
payload_bytes = packet[28:28+payload_size]  # ❌ 应该是 36:36+payload_size
```

**解决**: 创建协议修改检查清单文档，列出所有需要同步修改的文件。

### 修改文件清单

| 文件 | 修改内容 |
|------|----------|
| `UdpMediaSender.java` | HEADER_SIZE: 16→24，所有发送方法添加 send_time_ns |
| `protocol.py` | UDP_HEADER_SIZE: 16→24 |
| `udp_video.py` | UdpPacketHeader 添加 send_time_ns，_parse_udp_header() 解析 24 字节 |
| `client.py` | 第一个 UDP 包偏移量: 16→24, 28→36 |
| `delay_buffer.py` | FrameWithMetadata 添加 send_time_ns |
| `stream.py` | VideoPacket 添加 send_time_ns |
| `video.py` | push() 传递 send_time_ns |
| `opengl_widget.py` | 计算完整 E2E 延迟 |

### 新增日志格式

```
[E2E] Frame #1: packet_id=6, Device→consume=XXX.Xms, UDP→consume=XX.Xms
[E2E-FULL] Frame #60: packet_id=71, Device→render=XXX.Xms (full pipeline)
```

### 流程改进

为避免类似问题，创建了：
- `docs/development/PROTOCOL_CHANGE_CHECKLIST.md` - 协议修改检查清单
- 更新 `CLAUDE.md` 作为文档入口索引

---

## 参考资料

- [PROTOCOL_SPEC.md](./PROTOCOL_SPEC.md) - 完整协议规范
- [FEC_PLI_PROTOCOL_SPEC.md](./FEC_PLI_PROTOCOL_SPEC.md) - FEC和PLI协议细节
- [PROTOCOL_CHANGE_CHECKLIST.md](./development/PROTOCOL_CHANGE_CHECKLIST.md) - 协议修改检查清单
