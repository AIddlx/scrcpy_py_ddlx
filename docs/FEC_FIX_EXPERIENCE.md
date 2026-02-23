# FEC (前向纠错) 修复经验总结

**日期**: 2026-02-23 (最后更新)
**问题**: UDP网络模式下快速滑动时出现画面马赛克
**解决状态**: ✅ 已解决 (参数优化详见最后章节)

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

## 2026-02-23 修复: FEC 数据包未注册导致恢复失败

### 问题描述

**症状**:
- 启用 FEC 后，即使很低的丢包率（0.01%）也会导致马赛克
- FEC 恢复从未成功过
- 日志显示 `FEC parity` 包正常接收，但 `groups_recovered` 始终为 0

**根本原因**:
在 `_handle_fec_data()` 方法中，FEC 数据包被直接输出到队列，但**从未注册到 FEC 解码器**：

```python
# 原代码 (有 bug)
def _handle_fec_data(self, udp_header, payload):
    # ...解析 FEC header...
    video_packet = self._parse_scrcpy_packet(scrcpy_data)
    if video_packet:
        self._queue_packet(video_packet)  # 直接输出到队列
        # ❌ 缺少: fec_decoder.add_data_packet() 调用
```

这导致：
1. FEC 解码器不知道哪些数据包被接收了
2. `group.data_packets` 始终为空
3. 当有丢包时，恢复是不可能的

### 解决方案

**文件**: `scrcpy_py_ddlx/core/demuxer/udp_video.py`

**修改**:
```python
def _handle_fec_data(self, udp_header, payload):
    # ...解析 FEC header...
    video_packet = self._parse_scrcpy_packet(scrcpy_data)
    if video_packet:
        self._queue_packet(video_packet)  # 立即输出，保证低延迟

        # CRITICAL FIX: 注册数据包到 FEC 解码器
        if self._fec_decoder is not None and total_frames > 0:
            self._fec_decoder.add_data_packet(
                group_id=group_id,
                packet_idx=frame_idx,
                total_data=total_frames,
                total_parity=total_parity,
                data=scrcpy_data,
                original_size=original_size if original_size > 0 else len(scrcpy_data),
            )
            logger.debug(f"[FEC-REGISTER] Registered data packet: group={group_id}, idx={frame_idx}/{total_frames}")
```

### 设计说明

1. **立即输出策略**: 数据包先输出到队列，保证视频正常播放
2. **并行注册**: 同时注册到 FEC 解码器，为可能的恢复做准备
3. **恢复时机**: 当 parity 包到达且检测到丢包时，FEC 解码器可以正确恢复

### FEC 恢复流程

```
1. 数据包到达 (frame_idx=0,1,2,3):
   - 立即输出到队列 → 视频正常播放
   - 注册到 fec_decoder.data_packets[0,1,2,3]

2. 假设 frame_idx=2 丢失:
   - fec_decoder.data_packets = {0, 1, 3}  # 缺少 2
   - frame_idx=2 的包从未输出

3. Parity 包到达:
   - add_parity_packet() 检测到 can_recover = True
   - XOR 恢复: recovered = P0 XOR D0 XOR D1 XOR D3
   - 返回恢复的数据包列表
   - _process_fec_group() 将恢复的包输出到队列
```

### 测试建议

```bash
# 测试 FEC 恢复能力
python tests_gui/test_network_direct.py --fec --video-fec --drop-rate 0.01 --queue-size 3

# 查看日志确认 FEC 工作
# 期望看到:
# [FEC-REGISTER] Registered data packet: group=X, idx=Y/K
# FEC group X recovered: N missing packets
# FEC: recovered packet Y in group X
```

### 附带修复: is_complete 连续性检查

**问题**: `is_complete` 只检查数据包数量，不检查索引连续性
- 收到 frame_idx=1,2,3,4,5,6,7,8（缺少0）时，`is_complete` 错误返回 True
- `_get_ordered_data()` 访问 `data_packets[0]` 时抛出 `KeyError`

**修复**: 在 `FecGroupBuffer.is_complete` 中添加连续性检查
```python
@property
def is_complete(self) -> bool:
    """Check if all data packets are received (0-based: 0 to K-1)."""
    if self.total_data_packets == 0:
        return False
    if len(self.data_packets) != self.total_data_packets:
        return False
    return all(i in self.data_packets for i in range(self.total_data_packets))
```

### 2026-02-23 修复: 服务端/客户端统一 0-based 索引

**问题**: 服务端 `frameComplete()` 在发送新帧**之前**调用，导致索引偏移
- 第一帧不调用 `frameComplete()`，使用 idx=0
- 后续帧发送前先调用 `frameComplete()`，导致 idx=1,2,...,8
- 结果：K=8 时发送了 9 个包（idx=0-8）

**修复**:
1. **服务端**: 将 `frameComplete()` 移到发送帧数据**之后**
   - 帧 0：addPacket(idx=0) → frameComplete() → idx=1
   - 帧 1：addPacket(idx=1) → frameComplete() → idx=2
   - ...
   - 帧 7：addPacket(idx=7) → frameComplete() → idx=8 → shouldFinalizeGroup()=true

2. **客户端**: 使用统一的 0-based 索引检查（0 到 K-1）

**修改文件**:
- `UdpMediaSender.java`: 调整 frameComplete() 调用顺序
- `fec.py`: 简化为 0-based 索引检查

### 附带修复: 分片帧 FEC 注册

**问题**: 分片帧（FEC fragment）绕过了 FEC 注册
- `_handle_fec_fragment` 直接调用 `_handle_fragment` 进行重组
- 重组完成后没有注册到 FEC 解码器
- 导致 FEC 组永远无法完成（缺少 frame_idx=0 的大帧）

**现象**:
```
FEC fragment: group=0, idx=0/8, ... (bypassing FEC)
FEC add_data_packet: group=0, idx=1, stored=1/8
...
FEC group 0 expired: received 8/8 data, 1/4 parity
```

**修复**:
1. 在 `_handle_fec_fragment` 中保存 FEC 元数据（按 timestamp 索引）
2. 在 `_handle_fragment` 重组完成后检查是否有 FEC 元数据
3. 如果有，调用 `fec_decoder.add_data_packet()` 注册

```python
# _handle_fec_fragment 中:
self._fec_fragment_metadata[udp_header.timestamp] = {
    'group_id': group_id,
    'packet_idx': packet_idx,
    'total_data': total_data,
    'total_parity': total_parity,
    'original_size': original_size,
}

# _handle_fragment 重组完成后:
fec_meta = getattr(self, '_fec_fragment_metadata', {}).get(udp_header.timestamp)
if fec_meta and self._fec_decoder is not None:
    self._fec_decoder.add_data_packet(
        group_id=fec_meta['group_id'],
        packet_idx=fec_meta['packet_idx'],
        ...
    )
```

---

## 2026-02-23 优化: 解码错误检测 + 自动 PLI

### 问题背景

FEC 恢复后仍然可能出现画面残影/黄屏，因为：
1. XOR 恢复的帧可能有微小错误
2. 错误进入解码器参考帧后传播到后续帧
3. 之前没有机制在检测到错误时主动恢复

### 解决方案

在解码器中添加错误检测，当检测到以下情况时自动触发 PLI：
1. **帧跳过（Frame Skip）**：连续 5 帧以上被跳过
2. **NV12 转换失败**：GPU 帧转换失败
3. **帧处理异常**：解码过程中出现异常

### 代码实现

**文件**: `scrcpy_py_ddlx/core/decoder/video.py`

```python
# 添加错误回调
self._on_decode_error_callback: Optional[callable] = None
self._decode_error_count = 0
self._last_pli_time = 0.0

def set_on_decode_error_callback(self, callback: Optional[callable]) -> None:
    """Set callback for decode errors (used to trigger PLI)."""
    self._on_decode_error_callback = callback

def _trigger_decode_error(self, error_type: str, details: str = "") -> None:
    """Trigger PLI when decoding errors are detected."""
    self._decode_error_count += 1

    # Cooldown check (500ms)
    import time
    if time.time() - self._last_pli_time < 0.5:
        return

    if self._on_decode_error_callback:
        self._last_pli_time = time.time()
        logger.warning(f"[DECODE-ERROR] {error_type}: {details}, triggering PLI")
        self._on_decode_error_callback(error_type, details)
```

**文件**: `scrcpy_py_ddlx/client/components.py`

```python
# 连接解码器错误回调到 PLI 发送
if self._control_socket:
    def on_decode_error(error_type: str, details: str):
        import struct
        from scrcpy_py_ddlx.core.protocol import ControlMessageType
        msg = struct.pack('>B', ControlMessageType.RESET_VIDEO)
        self._control_socket.sendall(msg)
        logger.info(f"[DECODER-PLI] Sent PLI due to {error_type}: {details}")

    decoder.set_on_decode_error_callback(on_decode_error)
```

### 触发条件

| 错误类型 | 触发条件 | 说明 |
|----------|----------|------|
| `frame_skip` | 连续 5 帧跳过 | 渲染跟不上，可能参考帧损坏 |
| `nv12_fail` | NV12 转换失败 | GPU 帧数据损坏 |
| `process_error` | 帧处理异常 | 解码异常 |
| `content_corruption` | 帧内容异常 | UV 平面数据异常分布 |

---

## 2026-02-23 优化: 帧内容检测

### 问题背景

H.264 参考帧损坏后，解码器仍然"正常"输出，但画面出现：
- 下半区域变色（蓝色/黄色）
- 残影/重影
- 色彩异常

解码器层面无法检测这种问题，因为：
- 比特流语法是正确的
- 解码过程没有报错
- 只有输出内容是错误的

### 解决方案

在 NV12 帧输出后检查帧内容，检测常见的损坏模式：

**检测算法**：

```python
def _check_frame_content(self, frame_data, frame_w, frame_h):
    """Check frame content for visual corruption."""

    # 1. 极端值检测：UV 平面有过多的 0 或 255
    u_extreme_ratio = mean((u < 20) | (u > 235))
    if u_extreme_ratio > 0.3:  # 30% 阈值
        return False  # 损坏

    # 2. 突然色移：UV 均值变化过大
    if abs(u_mean - last_u_mean) > 50:
        return False  # 损坏

    # 3. 纯色检测：方差过低
    if u_var < 10 and v_var < 10:
        return False  # 损坏

    return True
```

**检测模式**：

| 模式 | 检测方法 | 典型表现 |
|------|----------|----------|
| 极端值过多 | UV 平面 >30% 在边界值 | 蓝/黄屏 |
| 突然色移 | UV 均值变化 >50 | 色彩突变 |
| 纯色区域 | UV 方差 <10 | 单色块 |

### 性能优化

- 每隔 N 帧检测一次（默认 10 帧）
- 采样检测（每 4 个像素取 1 个）
- 仅检查 UV 平面（Y 平面通常正常）

### 效果

- 当检测到解码错误时，自动请求新的关键帧
- 比等待服务端周期性发送关键帧更快恢复
- 减少残影/黄屏的持续时间

---

## 2026-02-23 经验: XOR FEC + H.264 的固有限制

### 问题背景

**症状**:
- 3% 丢包率下，画面出现残影/重影
- 快速滑动时静态区域文字难以看清
- 严重时画面变成浅黄色（YUV 解码器参考帧损坏）

**测试命令**:
```bash
python tests_gui/test_network_direct.py --drop-rate 0.03 --fec frame --fec-k 10 --fec-m 4 --codec h264
```

### 根本原因分析

这是 **XOR FEC + H.264 P 帧依赖** 的固有限制，不是 bug：

1. **H.264 帧依赖**:
   - I 帧（关键帧）：独立解码
   - P 帧：依赖前一帧作为参考
   - 参考帧损坏 → 所有后续 P 帧都受影响

2. **XOR 恢复的风险**:
   - XOR 恢复是精确的（数学上正确）
   - 但如果恢复的 P 帧数据有**任何微小问题**：
     - 截断大小错误
     - 数据对齐问题
     - 网络传输中的数据损坏
   - 错误会进入解码器的参考帧缓冲区

3. **错误传播**:
   ```
   关键帧 (正确) → P1 (恢复，微小错误) → P2 (错误传播) → P3 (错误传播) → ...
   ```
   - 直到下一个关键帧才会恢复
   - 黄屏是 Y 通道正确但 UV 通道损坏的典型表现

4. **高丢包率风险**:
   - K=10 组大小 + 3% 丢包 ≈ 每组 0.3 个丢包
   - 多个组有丢包时，累积错误风险高
   - 大组大小增加延迟，也给错误传播更多时间

### 解决方案：优化 FEC 参数

**关键发现**：更小的 FEC 参数可以更快恢复

**推荐配置**:
```bash
# 低丢包率 (≤1%)
python tests_gui/test_network_direct.py --drop-rate 0.01 --fec frame --fec-k 4 --fec-m 2 --codec h264

# 中等丢包率 (1-2%)
python tests_gui/test_network_direct.py --drop-rate 0.02 --fec frame --fec-k 2 --fec-m 1 --codec h264

# 高丢包率 (>2%) - 需要更多保护
python tests_gui/test_network_direct.py --drop-rate 0.03 --fec frame --fec-k 2 --fec-m 2 --codec h264
```

### 参数选择指南

| 参数 | 作用 | 小值优势 | 大值优势 |
|------|------|----------|----------|
| `--fec-k` | 每组帧数 | 更快恢复，更低延迟 | 更高效的带宽利用 |
| `--fec-m` | 校验包数 | 更低开销 | 更强的恢复能力 |
| `--queue-size` | 帧队列大小 | 更低延迟 | 更好的抖动容忍 |

**权衡**:
- 小 K 值（2-4）：快速恢复，但需要相对更多的 parity 开销
- 大 K 值（8-16）：高效，但恢复延迟高，错误传播风险大
- M=1：最小开销，只能恢复单包丢失
- M≥2：更强保护，但带宽开销增加

### 为什么小参数更好？

1. **更快的组完成**:
   - K=2 只需要 2 个帧就发送 parity
   - K=10 需要 10 个帧才发送 parity

2. **更低的累积错误风险**:
   - 小组意味着更频繁的"新开始"
   - 即使有错误，影响范围更小

3. **更低的延迟**:
   - 不需要等待大量帧累积
   - parity 更快到达，恢复更及时

### 其他优化建议

1. **使用 H.265**:
   ```bash
   python tests_gui/test_network_direct.py --codec h265 --fec frame --fec-k 2 --fec-m 1
   ```
   - H.265 有更好的错误恢复能力
   - 更高效的压缩，相同质量下更小的帧

2. **增加关键帧频率**（服务端设置）:
   - 更频繁的 I 帧可以限制错误传播范围
   - 代价是带宽增加

3. **降低码率**:
   ```bash
   python tests_gui/test_network_direct.py --bitrate 4000000 --fec frame --fec-k 2 --fec-m 1
   ```
   - 更小的帧 = 更少的 UDP 分片 = 更低的丢包风险

### 限制总结

| 丢包率 | 建议配置 | 预期效果 |
|--------|----------|----------|
| 0-1% | K=4, M=1 | 正常 |
| 1-2% | K=2, M=1 | 轻微残影 |
| 2-3% | K=2, M=2 | 可接受 |
| >3% | 不推荐 | FEC 无法有效恢复 |

**注意**: XOR FEC 不是万能的。在高丢包率环境下，应该：
1. 改善网络条件
2. 使用 TCP 模式
3. 降低视频质量/码率

---

## 2026-02-23 经验: 帧内容检测效果评估

### 测试结果

**帧内容检测 + 自动 PLI** 方案在实际测试中：
- ✅ **有效果**：能检测到部分损坏帧并触发 PLI
- ❌ **不理想**：需要复杂的参数调优，且效果受场景影响大

### 问题分析

1. **检测敏感度难以平衡**
   - 敏感度高：正常场景变化也被误判为损坏（如快速切换深色/浅色界面）
   - 敏感度低：实际损坏未被检测到
   - 不同应用/游戏的内容特征差异大

2. **UV 检测的局限性**
   - 只检测 UV 平面，但损坏可能表现在 Y 平面（亮度）
   - 某些损坏模式（如局部马赛克）UV 值分布正常
   - 动态场景的正常 UV 变化与损坏难以区分

3. **PLI 触发延迟**
   - 检测到损坏 → 发送 PLI → 服务端响应 → 新关键帧到达
   - 这个周期内用户已经看到了损坏的画面

4. **参考帧污染不可逆**
   - 一旦参考帧被污染，所有后续 P 帧都受影响
   - PLI 只能请求新的关键帧，无法修复已显示的错误帧

### 敏感度参数

可通过命令行调节（但不建议普通用户调整）：

```bash
--content-interval 5      # 检测间隔（帧数）
--content-extreme 0.15    # 极端值阈值（0.15 = 15%）
--content-shift 30        # 颜色突变阈值
--content-variance 50     # 最低方差阈值
--no-content-check        # 禁用内容检测
```

### 结论

帧内容检测是一个**辅助优化**，不能替代良好的网络环境。

---

## 2026-02-23 修复: 屏幕旋转时画面暂停无响应 (FEC + 大队列)

### 问题描述

**症状**:
- 使用 FEC + 大队列 (`--queue-size 9`) 时，屏幕旋转导致画面暂停且无响应
- 命令: `--drop-rate 0.01 --fec frame --fec-k 2 --fec-m 2 --queue-size 9`

**根本原因**:
1. **配置包被丢弃**: 大队列满时，配置包（CONFIG packet）与普通帧一样排队，可能因 50ms 超时被丢弃
2. **旧缓冲区污染**: 旋转后旧的 FEC 组、分片缓冲区仍然存在，但数据已无效

### 解决方案

#### 1. 配置包优先级处理

**文件**: `scrcpy_py_ddlx/core/demuxer/udp_video.py`

```python
def _queue_packet(self, packet: VideoPacket) -> None:
    # PRIORITY: Config packets should never be dropped
    if packet.header.is_config:
        qsize = self._packet_queue.qsize()
        if qsize >= self._packet_queue.maxsize - 1:
            # Queue nearly full, clear old packets for config
            logger.warning(f"[CONFIG-PRIORITY] Queue full ({qsize}), clearing for config packet")
            while not self._packet_queue.empty():
                self._packet_queue.get_nowait()
```

#### 2. 配置变更时清理缓冲区

**文件**: `scrcpy_py_ddlx/core/demuxer/udp_video.py`

```python
def _clear_buffers_on_config_change(self) -> None:
    """Clear all buffers when config changes (screen rotation)."""
    # Clear fragment buffers
    self._fragment_buffers.clear()
    self._fec_fragment_metadata.clear()
    self._parity_fragment_buffers.clear()

    # Clear FEC decoder groups
    if self._fec_decoder is not None:
        self._fec_decoder.clear()
```

**文件**: `scrcpy_py_ddlx/core/demuxer/fec.py`

```python
def clear(self) -> dict:
    """Clear all groups and return stats before clearing."""
    stats = dict(self._stats)
    self._groups.clear()
    self._recent_failures = 0
    return stats
```

### 修改文件

| 文件 | 修改 |
|------|------|
| `udp_video.py` | 配置包优先级 + 旋转时清理缓冲区 |
| `fec.py` | 添加 clear() 方法 |

---

## 2026-02-23 修复: 屏幕旋转时崩溃 (Qt 线程问题)

### 问题描述

**症状**:
- 屏幕旋转时程序崩溃，日志在 `[VIDEO_HEADER]` 后突然结束
- 没有异常堆栈输出

**根本原因**:
`_on_frame_size_changed` 中的线程检测错误：
```python
# 错误代码：isVisible() 不会抛出异常！
try:
    _ = self.isVisible()  # 这里不会抛出异常
    # 所以代码错误地认为在主线程上
    self._do_frame_size_changed(width, height)  # GUI 操作在非 GUI 线程上执行 → 崩溃
except Exception as e:
    # 这个分支永远不会执行
    QTimer.singleShot(0, do_resize)
```

### 解决方案

使用 `QThread` 正确检测当前线程：

**文件**: `scrcpy_py_ddlx/core/player/video/video_window.py`

```python
from PySide6.QtCore import QThread, QCoreApplication

def _on_frame_size_changed(self, width: int, height: int) -> None:
    # 检查是否在 GUI 线程上
    app = QCoreApplication.instance()
    if app is not None:
        gui_thread = app.thread()
        current_thread = QThread.currentThread()
        if gui_thread != current_thread:
            # 不在 GUI 线程，使用 QTimer 调度到 GUI 线程
            QTimer.singleShot(0, lambda: self._do_frame_size_changed(width, height))
            return

    # 在 GUI 线程上，直接调用
    self._do_frame_size_changed(width, height)
```

### 教训

1. **Qt 属性访问不一定会抛出异常** - `isVisible()`, `width()`, `height()` 等方法可以在任何线程调用
2. **只有 GUI 修改操作会崩溃** - 读取可能安全，但修改窗口大小等操作必须 GUI 线程
3. **使用 QThread 检测线程** - 这是唯一可靠的方法

---

## ⚠️ 使用建议：丢包容忍度

### 推荐使用环境

| 丢包率 | 建议配置 | 预期效果 |
|--------|----------|----------|
| **≤1%** | K=4, M=1 或 K=2, M=1 | 正常使用，偶有轻微马赛克 |
| 1-2% | K=2, M=1 + 内容检测 | 可接受，有残影但快速恢复 |
| **>1%** | ⚠️ 不推荐 | 体验不佳 |

### 不推荐使用场景

**平均丢包率超过 1% 的网络环境不建议使用当前项目**

原因：
1. XOR FEC + H.264 的固有限制无法通过软件优化完全解决
2. 帧内容检测需要复杂调优，效果不稳定
3. 高丢包率下错误累积，画面质量持续下降

### 替代方案

对于高丢包率环境，建议：
1. **改善网络条件**：使用 5GHz WiFi、有线连接、更近的距离
2. **使用 ADB 隧道模式**：TCP 有重传，丢包自动恢复
3. **降低视频质量**：减小帧大小，降低分片数量
4. **使用 H.265**：更好的错误恢复能力

---

## 2026-02-23 修复: ADB 模式屏幕旋转时数据流错误

### 问题描述

**症状**:
- ADB 隧道模式下，屏幕旋转时客户端崩溃
- 错误日志：`Payload size 3684230449 exceeds maximum 16777216`
- 异常的 PTS 值：`2896437111613819232`

**根本原因**:

ADB 模式下旋转时，服务端重新发送了 12 字节的 codec header：
```
codec_id(4字节) + width(4字节) + height(4字节) = 12字节
```

但 `StreamingVideoDemuxer` 把这 12 字节当成了视频包 header 来解析：
```
pts_flags(8字节) + payload_size(4字节) = 12字节
```

导致：
- `codec_id` 被解析为 `pts_flags` 的高 4 字节
- `width` 被解析为 `pts_flags` 的低 4 字节
- `height` 被解析为 `payload_size`

### 分析

以 H.265 + 2400x1080 分辨率为例：

```
服务端发送的 codec header:
  codec_id = 0x68323635 ('h265')
  width    = 2400 (0x00000960)
  height   = 1080 (0x00000438)

被错误解析为视频包 header:
  pts_flags  = 0x6832363500000960 (异常大的 PTS)
  payload_size = 1080 (正常范围内!)

问题：payload_size 在正常范围内，但 PTS 异常
```

### 解决方案

在 `StreamingVideoDemuxer._recv_packet()` 中添加 codec header 检测：

**文件**: `scrcpy_py_ddlx/core/demuxer/video.py`

```python
# 已知的 codec ID（低 30 位，忽略高位）
_KNOWN_CODEC_IDS = {
    0x68323634: 'H264',  # 'h264'
    0x68323635: 'H265',  # 'h265'
    0x61763031: 'AV1',   # 'av01'
}

def _recv_packet(self):
    # 解析 header
    pts_flags, payload_size = struct.unpack('>QI', header_data)

    # 检测 codec header（旋转时服务端重新发送）
    potential_codec_id = (pts_flags >> 32) & 0xFFFFFFFF
    potential_codec_id_lower = potential_codec_id & 0x3FFFFFFF  # 忽略高位

    if potential_codec_id_lower in known_codec_ids_lower:
        width = pts_flags & 0xFFFFFFFF
        height = payload_size

        # 验证尺寸 + PTS 异常大
        if 100 <= width <= 4096 and 100 <= height <= 4096 and pts > 10**15:
            # 这是 codec header，不是视频包！
            # 通知回调，跳过这个"包"
            if self._frame_size_changed_callback:
                self._frame_size_changed_callback(width, height)
            return None
```

### 关键点

1. **使用低 30 位比较**: 因为 `0x68323635` 被解析后高位可能被错误设置，需要忽略
2. **PTS 异常大检测**: 正常 PTS < 10^15，codec header 解析出的 "PTS" > 10^15
3. **尺寸范围验证**: 100-4096 像素的合理范围

### 修改文件

| 文件 | 修改 |
|------|------|
| `video.py` (StreamingVideoDemuxer) | 添加 codec header 检测逻辑 |
| `video.py` | 添加 `set_frame_size_changed_callback()` 方法 |

### 测试结果

- ✅ 网络模式 (UDP) 旋转：正常工作
- ✅ ADB 隧道模式旋转：正常工作
- ✅ 两个不同手机测试通过

---

## 参考资料

- [PROTOCOL_SPEC.md](./PROTOCOL_SPEC.md) - 完整协议规范
- [FEC_PLI_PROTOCOL_SPEC.md](./FEC_PLI_PROTOCOL_SPEC.md) - FEC和PLI协议细节
- [PROTOCOL_CHANGE_CHECKLIST.md](./development/PROTOCOL_CHANGE_CHECKLIST.md) - 协议修改检查清单
