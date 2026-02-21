# UDP 音频破音问题修复记录

**日期**: 2026-02-15
**问题**: UDP 网络模式下音频播放出现严重破音
**状态**: 已解决

---

## 1. 问题描述

在 UDP 网络模式下，视频播放正常，但音频出现严重的"破音"现象，声音完全无法辨认。

### 现象
- FEC 恢复正常工作（显示有 167 次恢复）
- 音频解码器正常解码（每帧 960 samples）
- 音频播放器有数据（pushed 337, played 166）
- 有 68 次 underrun
- 但播放出的声音是破损的

---

## 2. 错误的排查思路

### 2.1 尝试过的错误方法

| 方法 | 参数变化 | 结果 |
|-----|---------|------|
| 增加预缓冲 | 100ms → 200ms → 500ms → 1000ms | 破音时间变长，问题依旧 |
| 调整输出缓冲 | 20ms → 50ms | 无改善 |
| 增加 FEC 冗余 | M=1 → M=2 | 无改善（FEC 本来就正常） |

### 2.2 为什么这些方法无效？

**增加缓冲只是让错误的数据播放更久**，并没有解决数据本身的问题。

> 用户反馈："增加缓存也只是让破音播放的时间越来越长"

---

## 3. 正确的分析方法：数据流追踪

### 3.1 对比视频端（正常工作）的数据流

```
UDP包接收
    ↓
VideoDemuxer._process_packet()
    ↓
StreamParser.parse_packet()  ← 关键：这里解析并剥离头部
    ↓
VideoPacket.data = 纯H264数据（不含scrcpy头部）
    ↓
VideoDecoder._decode_packet()
    ↓
av.Packet(packet.data)  ← 解码器收到纯编码数据
    ↓
解码正常 ✓
```

### 3.2 检查音频端（有问题）的数据流

```
UDP包接收
    ↓
UdpAudioDemuxer._handle_normal_packet()
    ↓
self._packet_queue.put(payload)  ← 问题：直接放入完整包
    ↓
payload = [scrcpy头12B] + [OPUS数据]
    ↓
AudioDecoder._decode_packet()
    ↓
self._codec.decode(packet)  ← 解码器收到包含头部的数据
    ↓
OpusDecoder.decode() → av.Packet(data)
    ↓
FFmpeg把前12字节当OPUS数据解码 → 破音！✗
```

### 3.3 发现问题

**数据格式不匹配**：
- **服务端发送**: `[UDP头16B] + [scrcpy头12B] + [OPUS数据]`
- **音频解码器期望**: `[纯OPUS数据]`
- **实际收到**: `[scrcpy头12B] + [OPUS数据]`

OPUS 解码器把 scrcpy 头部的 12 字节当作音频数据来解码，导致解码出的音频完全损坏。

---

## 4. 解决方案

### 4.1 修改文件

`scrcpy_py_ddlx/core/demuxer/udp_audio.py`

### 4.2 修改内容

#### 4.2.1 `_handle_normal_packet` - 剥离 scrcpy 头部

```python
# 修复前（错误）：
audio_packet = payload[:PACKET_HEADER_SIZE + payload_size]
self._packet_queue.put(audio_packet, timeout=0.1)

# 修复后（正确）：
# Extract pure OPUS data (skip scrcpy header)
opus_data = payload[PACKET_HEADER_SIZE:PACKET_HEADER_SIZE + payload_size]
self._packet_queue.put(opus_data, timeout=0.1)
```

#### 4.2.2 `_handle_fec_parity` - 修复头部大小

FEC parity 包头部是 **5 字节**，不是 7 字节：

```python
# 修复前（错误）：
if len(payload) < 7:
    ...
original_size = struct.unpack('>H', payload[5:7])[0]  # parity包没有这个字段！
parity_data = payload[7:]

# 修复后（正确）：
if len(payload) < 5:
    ...
# Parity header: group_id(2) + parity_idx(1) + total_data(1) + total_parity(1) = 5 bytes
parity_data = payload[5:]
```

#### 4.2.3 `_process_recovered_packets` - 剥离 FEC 恢复包的 scrcpy 头部

```python
def _process_recovered_packets(self, packets: list) -> None:
    for packet_data in packets:
        # FEC 恢复的数据包包含 scrcpy 头部，需要剥离
        if len(packet_data) < PACKET_HEADER_SIZE:
            continue

        pts_flags, payload_size = struct.unpack('>QI', packet_data[:PACKET_HEADER_SIZE])
        opus_data = packet_data[PACKET_HEADER_SIZE:PACKET_HEADER_SIZE + payload_size]

        self._packet_queue.put(opus_data, timeout=0.1)
```

#### 4.2.4 `_handle_config_packet` - 不发送配置包到解码队列

OPUS 解码器不需要配置包（配置包只是告诉客户端使用 OPUS 编解码器）：

```python
# 修复前（错误）：
self._packet_queue.put(scrcpy_packet, timeout=0.5)  # 配置包被发送到解码器

# 修复后（正确）：
# OPUS decoder doesn't need config packets - don't queue
return  # 只记录日志，不发送到队列
```

---

## 5. 数据包格式参考

### 5.1 Scrcpy 音频包格式

```
┌─────────────────────────────────────────────────────────────┐
│                    Scrcpy Audio Packet                       │
├──────────────┬──────────────┬───────────────────────────────┤
│  pts_flags   │ payload_size │         audio_data            │
│   (8 bytes)  │   (4 bytes)  │         (N bytes)             │
└──────────────┴──────────────┴───────────────────────────────┘
     ↑                ↑                      ↑
     │                │                      │
   PTS + 标志      OPUS数据长度           纯OPUS数据
   (CONFIG=1<<63)                        (解码器只需要这部分)
```

### 5.2 UDP + FEC 音频包格式

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    UDP Audio Packet with FEC                             │
├────────────────┬───────────────────┬────────────────┬───────────────────┤
│   UDP Header   │    FEC Header     │ Scrcpy Header  │    OPUS Data      │
│   (16 bytes)   │    (7 bytes)      │   (12 bytes)   │    (N bytes)      │
├────────────────┼───────────────────┼────────────────┼───────────────────┤
│ seq (4B)       │ group_id (2B)     │ pts_flags (8B) │                   │
│ ts   (8B)      │ packet_idx (1B)   │ size (4B)      │  纯OPUS数据        │
│ flags(4B)      │ total_data (1B)   │                │  (解码器需要)      │
│                │ total_parity(1B)  │                │                   │
│                │ original_size(2B) │                │                   │
└────────────────┴───────────────────┴────────────────┴───────────────────┘
```

### 5.3 FEC Parity 包格式（注意：只有 5 字节头部）

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    FEC Parity Packet                                     │
├────────────────┬───────────────────┬────────────────────────────────────┤
│   UDP Header   │  FEC Parity Header│         Parity Data                │
│   (16 bytes)   │    (5 bytes)      │         (N bytes)                  │
├────────────────┼───────────────────┼────────────────────────────────────┤
│ seq (4B)       │ group_id (2B)     │                                    │
│ ts   (8B)      │ parity_idx (1B)   │    XOR校验数据                      │
│ flags(4B)      │ total_data (1B)   │                                    │
│                │ total_parity(1B)  │                                    │
│                │ (无original_size) │                                    │
└────────────────┴───────────────────┴────────────────────────────────────┘
```

---

## 6. 经验教训

### 6.1 分析问题的优先级

```
1. 数据格式是否正确？     ← 最重要！首先检查
2. 数据流是否完整？
3. 组件间接口是否匹配？
4. 缓冲/时序问题？        ← 最后才考虑
```

### 6.2 调试技巧

1. **对比已工作的代码**
   - 视频端能工作 → 检查视频端如何处理数据
   - 音频端是新代码 → 可能没有复用正确的逻辑

2. **追踪数据流**
   - 从数据源开始，逐步跟踪数据经过的每个处理阶段
   - 检查每个阶段的输入/输出格式是否匹配

3. **理解编解码器的期望**
   - 编解码器（OPUS/H264）只认识纯编码数据
   - 不会识别应用层协议头部（如 scrcpy 头部）

### 6.3 避免"盲目调参"

| 错误做法 | 正确做法 |
|---------|---------|
| 增加缓冲大小 | 检查数据格式 |
| 调整超时时间 | 追踪数据流 |
| 增加重试次数 | 分析接口匹配 |
| 添加更多日志 | 理解协议格式 |

---

## 7. 相关文件

- `scrcpy_py_ddlx/core/demuxer/udp_audio.py` - UDP 音频解复用器
- `scrcpy_py_ddlx/core/audio/decoder.py` - 音频解码器
- `scrcpy_py_ddlx/core/audio/codecs/base.py` - OPUS 解码器实现
- `scrcpy_py_ddlx/core/stream.py` - 流解析器（参考实现）
- `scrcpy_py_ddlx/core/demuxer/udp_video.py` - UDP 视频解复用器（参考实现）

---

## 8. 测试验证

```bash
# 构建服务端
cd scrcpy/server && ./build_without_gradle.sh && cd ../..

# 测试音频
python -X utf8 tests_gui/test_network_direct.py --audio

# 测试音频 + FEC
python -X utf8 tests_gui/test_network_direct.py --audio --fec-k 4 --fec-m 1
```

---

**结论**: 思路对了比局限在某些地方试错更重要。在调试问题时，首先要理解数据流和格式，而不是盲目调整参数。
