# FEC + PLI 协议扩展规范

> **版本**: 1.0-draft
> **状态**: 草稿 - 待评审
> **创建日期**: 2026-02-13
> **基于**: PROTOCOL_SPEC.md v1.1

本文档定义了 scrcpy-py-ddlx 项目中 FEC（前向纠错）和 PLI（图像丢失指示）功能的协议扩展。

---

## 目录

1. [概述](#1-概述)
2. [设计原则](#2-设计原则)
3. [协议扩展](#3-协议扩展)
4. [服务端实现规范](#4-服务端实现规范)
5. [客户端实现规范](#5-客户端实现规范)
6. [新增 Demuxer 架构](#6-新增-demuxer-架构)
7. [交互流程](#7-交互流程)
8. [配置参数](#8-配置参数)
9. [测试用例](#9-测试用例)

---

## 1. 概述

### 1.1 问题背景

当前 UDP 实现的局限性：
- **无丢包恢复**: 丢包直接导致解码错误或花屏
- **无关键帧请求**: 严重丢包后无法快速恢复
- **无网络质量监测**: 无法根据网络状况调整策略

### 1.2 解决方案

| 机制 | 作用 | 触发条件 |
|-----|------|---------|
| **FEC (Forward Error Correction)** | 通过冗余数据恢复丢失包 | 少量随机丢包 (< FEC 冗余度) |
| **PLI (Picture Loss Indication)** | 请求服务端发送关键帧 | 连续丢包超过阈值，或解码错误 |

### 1.3 实现策略

```
丢包事件
    │
    ▼
┌─────────────────┐
│  尝试 FEC 恢复   │
└────────┬────────┘
         │
    ┌────┴────┐
    │ 恢复成功？│
    └────┬────┘
         │
    ┌────┴────┐
    │         │
   YES       NO
    │         │
    ▼         ▼
  继续    ┌─────────────┐
  解码    │ 发送 PLI 请求 │
          └──────┬──────┘
                 │
                 ▼
          ┌─────────────┐
          │ 等待关键帧   │
          └─────────────┘
```

---

## 2. 设计原则

### 2.1 向后兼容

- 新增功能为**可选**，不影响现有实现
- 客户端/服务端通过**协商**确定是否启用 FEC/PLI
- 旧版本客户端/服务端应能正常工作（降级为无 FEC/PLI）

### 2.2 渐进增强

```
Level 0: 当前实现（无 FEC/PLI）
    ↓
Level 1: PLI only（通过 TCP 控制通道）
    ↓
Level 2: PLI + 简单 XOR FEC
    ↓
Level 3: PLI + Reed-Solomon FEC
```

### 2.3 架构分离

- **ADB 隧道模式**: 使用现有 `StreamingVideoDemuxer`
- **UDP 网络模式**: 使用新增 `UdpVideoDemuxer`（专用于 UDP）

---

## 3. 协议扩展

### 3.1 UDP 头部 FLAGS 扩展

**当前定义** (16 字节 UDP 头部):
```
Offset  Field      Size    Description
------  ----       ----    -----------
0       sequence   4       包序列号 (big-endian)
4       timestamp  8       时间戳 (big-endian)
12      flags      4       标志位 (big-endian)
```

**FLAGS 位分配 (扩展后)**:

| Bit | 名称 | 值 | 描述 |
|-----|------|-----|------|
| 0 | KEY_FRAME | 0x00000001 | 关键帧标志 |
| 1 | CONFIG | 0x00000002 | 配置包标志 |
| 2 | **FEC_DATA** | 0x00000004 | **[新增] FEC 数据包** |
| 3 | **FEC_PARITY** | 0x00000008 | **[新增] FEC 校验包** |
| 4-30 | **RESERVED** | - | **保留，设为 0** |
| 31 | FRAGMENTED | 0x80000000 | 分片包标志 |

**判断逻辑**:
```python
def get_packet_type(flags: int) -> str:
    if flags & 0x80000000:  # bit 31
        return "FRAGMENTED"
    if flags & 0x00000008:  # bit 3
        return "FEC_PARITY"
    if flags & 0x00000004:  # bit 2
        return "FEC_DATA"
    return "NORMAL"
```

### 3.2 FEC 数据包格式

#### 3.2.1 FEC 组概念

```
FEC Group (组):
├── Data Packet 0  [seq=N+0, ts=T, flags=0x04]
├── Data Packet 1  [seq=N+1, ts=T, flags=0x04]
├── Data Packet 2  [seq=N+2, ts=T, flags=0x04]
├── Data Packet 3  [seq=N+3, ts=T, flags=0x04]
├── Parity Packet 0 [seq=N+4, ts=T, flags=0x08]  # XOR of 0-3
└── Parity Packet 1 [seq=N+5, ts=T, flags=0x08]  # 可选，更强的恢复能力

参数:
- K = 4 (数据包数)
- M = 2 (校验包数)
- 可恢复任意 min(M, K) 个丢失包
```

#### 3.2.2 FEC 数据包格式 (flags bit 2 = 1)

```
[UDP Header: 16B] [FEC Group Header: 4B] [Scrcpy Header: 12B] [Payload: NB]

FEC Group Header:
  Offset 0-1:   group_id (uint16, big-endian) - 组标识
  Offset 2:     packet_idx (uint8) - 包在组内的索引 (0..K-1)
  Offset 3:     total_packets (uint8) - 组内总包数 (K)
```

#### 3.2.3 FEC 校验包格式 (flags bit 3 = 1)

```
[UDP Header: 16B] [FEC Parity Header: 4B] [Parity Data: NB]

FEC Parity Header:
  Offset 0-1:   group_id (uint16, big-endian) - 组标识
  Offset 2:     parity_idx (uint8) - 校验包索引 (0..M-1)
  Offset 3:     total_packets (uint8) - 组内数据包数 (K)
```

#### 3.2.4 FEC 校验包分片格式 (flags bit 3 = 1 且 bit 31 = 1)

> **注意**: 当 parity 数据超过 UDP MTU (~65KB) 时，需要分片发送。
> 这在 frame-level FEC 且帧数据较大时（如快速运动场景）很常见。

```
[UDP Header: 16B] [Fragment Index: 4B] [FEC Parity Header: 5B] [Parity Fragment: NB]

UDP Header:
  Offset 0-3:   sequence (uint32, big-endian)
  Offset 4-11:  timestamp (uint64, big-endian)
  Offset 12-15: flags (uint32, big-endian)
                - bit 3: FLAG_FEC_PARITY = 1
                - bit 31: FLAG_FRAGMENTED = 1

Fragment Index:
  Offset 0-3:   frag_idx (uint32, big-endian) - 分片索引 (0, 1, 2, ...)

FEC Parity Header:
  Offset 0-1:   group_id (uint16, big-endian) - 组标识
  Offset 2:     parity_idx (uint8) - 校验包索引 (0..M-1)
  Offset 3:     total_data (uint8) - 组内数据帧数 (K)
  Offset 4:     total_parity (uint8) - 组内校验包数 (M)
```

**分片重组逻辑**:
1. 客户端按 `timestamp` 分组缓存分片
2. 每个 `frag_idx` 对应一个分片
3. 检测到连续分片 (0, 1, 2, ..., N) 后重组
4. 重组后按普通 parity packet 处理

**示例**:
```
# 服务端日志
FEC generateParity: groupId=300, frames=4, maxSize=71293
FEC parity fragmenting: total=71298 bytes into 2 fragments
FEC parity fragments sent: 2 fragments

# 客户端收到
[FEC-PARITY-FRAG] ts=105961198675, frag_idx=0, frag_size=65482
[FEC-PARITY-FRAG] ts=105961198675, frag_idx=1, frag_size=50451
[FEC-PARITY] Reassembled 2 fragments, total size=115933 bytes
```

### 3.3 PLI 控制消息

#### 3.3.1 复用现有机制

**方案 A (推荐)**: 复用 `TYPE_RESET_VIDEO = 17`

```
客户端 → 服务端:
  [type: 1B] [length: 4B]
  0x11 00 00 00 00

服务端处理:
  - 触发 ScreenEncoder 重新初始化
  - 生成新的关键帧
```

**方案 B**: 新增 `TYPE_PLI_REQUEST = 20`

```java
// ControlMessage.java
public static final int TYPE_PLI_REQUEST = 20;

// 客户端 → 服务端
[type: 1B] [length: 4B] [payload: 0B]
0x14 00 00 00 00

// 服务端处理
case ControlMessage.TYPE_PLI_REQUEST:
    requestKeyFrame();
    break;
```

#### 3.3.2 PLI 请求时机

| 条件 | 描述 |
|-----|------|
| 连续丢包 >= N | `consecutive_drops >= pli_threshold` (默认 10) |
| 解码错误 | Decoder 报告无法解码当前帧 |
| 关键帧丢失 | 检测到 KEY_FRAME 包丢失 |
| FEC 恢复失败 | FEC 组内丢失包数 > 冗余度 |

---

## 4. 服务端实现规范

### 4.1 新增/修改文件清单

| 文件 | 修改类型 | 描述 |
|------|---------|------|
| `udp/UdpMediaSender.java` | **修改** | 添加 FEC 包发送逻辑 |
| `udp/FecEncoder.java` | **新增** | FEC 编码器 (XOR 或 Reed-Solomon) |
| `device/Streamer.java` | **修改** | 集成 FEC 编码器 |
| `control/ControlMessage.java` | **修改** | 添加 PLI 消息类型 (可选) |
| `control/Controller.java` | **修改** | 处理 PLI 请求 |
| `video/SurfaceEncoder.java` | **修改** | 添加 `requestKeyFrame()` 方法 |
| `Options.java` | **修改** | 添加 FEC/PLI 命令行参数 |

### 4.2 UdpMediaSender 扩展

```java
// UdpMediaSender.java - 新增常量
public static final long FLAG_FEC_DATA = 1L << 2;     // bit 2
public static final long FLAG_FEC_PARITY = 1L << 3;   // bit 3

// 新增字段
private final FecEncoder fecEncoder;
private final int fecGroupSize;    // K
private final int fecParityCount;  // M

// 新增方法
public void sendFecGroup(List<ByteBuffer> dataPackets, long timestamp, long flags)
    throws IOException {
    // 1. 发送数据包 (flags |= FLAG_FEC_DATA)
    // 2. 生成校验包
    // 3. 发送校验包 (flags |= FLAG_FEC_PARITY)
}
```

### 4.3 FecEncoder 接口

```java
// FecEncoder.java - FEC 编码器接口
public interface FecEncoder {
    /**
     * 编码一组数据包，生成校验包
     *
     * @param dataPackets 数据包列表 (K 个)
     * @param parityCount 需要生成的校验包数 (M)
     * @return 校验包列表 (M 个)
     */
    List<ByteBuffer> encode(List<ByteBuffer> dataPackets, int parityCount);

    /**
     * 获取推荐的数据包大小（用于对齐）
     */
    int getPacketSize();
}

// SimpleXorFecEncoder.java - 简单 XOR 实现
public class SimpleXorFecEncoder implements FecEncoder {
    @Override
    public List<ByteBuffer> encode(List<ByteBuffer> dataPackets, int parityCount) {
        // XOR 所有数据包生成校验包
        // 如果 parityCount > 1，使用不同的偏移生成多个校验包
    }
}
```

### 4.4 PLI 处理

```java
// Controller.java - 处理 PLI 请求
case ControlMessage.TYPE_RESET_VIDEO:  // 复用现有消息
case ControlMessage.TYPE_PLI_REQUEST:  // 或新增消息
    handlePliRequest();
    break;

private void handlePliRequest() {
    Ln.i("PLI request received, requesting key frame");
    if (videoEncoder != null) {
        videoEncoder.requestKeyFrame();
    }
}

// SurfaceEncoder.java - 关键帧请求
public void requestKeyFrame() {
    // 方法 1: 设置 MediaCodec 参数 (某些设备支持)
    // Bundle params = new Bundle();
    // params.putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0);
    // codec.setParameters(params);

    // 方法 2: 重新配置编码器强制关键帧 (通用)
    needKeyFrame = true;
}
```

---

## 5. 客户端实现规范

### 5.1 新增/修改文件清单

| 文件 | 修改类型 | 描述 |
|------|---------|------|
| `demuxer/udp_video.py` | **新增** | UDP 专用视频解复用器 |
| `demuxer/udp_audio.py` | **新增** | UDP 专用音频解复用器 |
| `demuxer/fec.py` | **新增** | FEC 解码器 |
| `demuxer/pli.py` | **新增** | PLI 请求生成器 |
| `demuxer/factory.py` | **修改** | 根据 mode 创建正确的 demuxer |
| `core/protocol.py` | **修改** | 添加 FEC/PLI 常量 |
| `client/config.py` | **修改** | 添加 FEC/PLI 配置选项 |
| `client/client.py` | **修改** | 集成 UdpVideoDemuxer |

### 5.2 UdpVideoDemuxer 类设计

```python
# demuxer/udp_video.py

class UdpVideoDemuxer:
    """
    UDP 专用视频解复用器。

    与 StreamingVideoDemuxer 的区别:
    1. 直接处理 UDP 包（不模拟 TCP 流）
    2. 内置丢包检测
    3. 集成 FEC 解码
    4. 自动生成 PLI 请求
    """

    # UDP 头部大小
    UDP_HEADER_SIZE = 16

    # FEC 配置
    DEFAULT_FEC_GROUP_SIZE = 4
    DEFAULT_FEC_PARITY_COUNT = 1

    def __init__(
        self,
        udp_socket: socket.socket,
        packet_queue: Queue,
        codec_id: int,
        control_channel,  # 用于发送 PLI
        fec_enabled: bool = True,
        pli_enabled: bool = True,
        pli_threshold: int = 10,
    ):
        self._socket = udp_socket
        self._packet_queue = packet_queue
        self._codec_id = codec_id
        self._control_channel = control_channel

        # FEC 解码器
        self._fec_decoder = FecDecoder() if fec_enabled else None
        self._fec_enabled = fec_enabled

        # PLI 配置
        self._pli_enabled = pli_enabled
        self._pli_threshold = pli_threshold
        self._consecutive_drops = 0

        # 状态追踪
        self._expected_seq = 0
        self._fec_buffer: Dict[int, FecGroup] = {}

    def _run_loop(self):
        """主循环 - 直接处理 UDP 包"""
        while not self._stopped:
            packet, addr = self._socket.recvfrom(65507)

            # 1. 解析 UDP 头部
            seq, ts, flags = self._parse_udp_header(packet[:16])
            payload = packet[16:]

            # 2. 丢包检测
            self._detect_loss(seq)

            # 3. 根据包类型分发
            if flags & 0x08:  # FEC_PARITY
                self._handle_fec_parity(ts, payload)
            elif flags & 0x04:  # FEC_DATA
                self._handle_fec_data(ts, payload)
            elif flags & 0x80000000:  # FRAGMENTED
                self._handle_fragment(ts, flags, payload)
            else:  # NORMAL
                self._handle_normal_packet(payload)

    def _detect_loss(self, seq: int):
        """检测丢包"""
        if seq > self._expected_seq:
            loss_count = seq - self._expected_seq
            self._consecutive_drops += loss_count
            logger.warning(f"Packet loss detected: {loss_count} packets, "
                          f"consecutive: {self._consecutive_drops}")

            # 触发 PLI
            if self._pli_enabled and self._consecutive_drops >= self._pli_threshold:
                self._send_pli()
                self._consecutive_drops = 0

        self._expected_seq = seq + 1

    def _send_pli(self):
        """发送 PLI 请求"""
        if self._control_channel:
            # 复用 TYPE_RESET_VIDEO
            msg = struct.pack('>BI', 0x11, 0)
            self._control_channel.send(msg)
            logger.info("PLI request sent")
```

### 5.3 FecDecoder 类设计

```python
# demuxer/fec.py

@dataclass
class FecGroup:
    """FEC 组缓冲区"""
    group_id: int
    total_packets: int
    data_packets: Dict[int, bytes] = field(default_factory=dict)
    parity_packets: Dict[int, bytes] = field(default_factory=dict)
    received_at: float = field(default_factory=time.time)

    def is_complete(self) -> bool:
        return len(self.data_packets) == self.total_packets

    def can_recover(self, loss_count: int) -> bool:
        return len(self.parity_packets) >= loss_count


class FecDecoder:
    """FEC 解码器"""

    def __init__(self, max_groups: int = 100, timeout: float = 1.0):
        self._groups: Dict[int, FecGroup] = {}
        self._max_groups = max_groups
        self._timeout = timeout

    def add_data_packet(self, group_id: int, packet_idx: int,
                        total: int, data: bytes) -> Optional[bytes]:
        """添加数据包，返回完整帧（如果组完成）"""
        group = self._get_or_create_group(group_id, total)
        group.data_packets[packet_idx] = data

        if group.is_complete():
            return self._reassemble(group)
        return None

    def add_parity_packet(self, group_id: int, parity_idx: int,
                          total: int, data: bytes) -> Optional[bytes]:
        """添加校验包，尝试恢复"""
        group = self._get_or_create_group(group_id, total)
        group.parity_packets[parity_idx] = data

        # 检查是否可以恢复
        missing = set(range(total)) - set(group.data_packets.keys())
        if missing and group.can_recover(len(missing)):
            return self._recover(group, missing)
        return None

    def _recover(self, group: FecGroup, missing: Set[int]) -> Optional[bytes]:
        """使用 XOR 恢复丢失包"""
        if len(missing) > len(group.parity_packets):
            return None  # 无法恢复

        # XOR 恢复算法
        # recovered = all_data XOR all_parity
        recovered = None
        for data in group.data_packets.values():
            if recovered is None:
                recovered = bytearray(data)
            else:
                self._xor_bytes(recovered, data)

        for parity in group.parity_packets.values():
            self._xor_bytes(recovered, parity)

        # 将恢复的包添加到组中
        missing_idx = missing.pop()
        group.data_packets[missing_idx] = bytes(recovered)

        if group.is_complete():
            return self._reassemble(group)
        return None

    @staticmethod
    def _xor_bytes(a: bytearray, b: bytes):
        """XOR 两个字节数组"""
        for i in range(min(len(a), len(b))):
            a[i] ^= b[i]
```

### 5.4 协议常量扩展

```python
# core/protocol.py - 新增常量

# =============================================================================
# UDP 扩展标志位
# =============================================================================

# UDP 头部 FLAGS 位定义 (与 UdpMediaSender.java 保持一致)
UDP_FLAG_KEY_FRAME = 1 << 0       # bit 0: 关键帧
UDP_FLAG_CONFIG = 1 << 1          # bit 1: 配置包
UDP_FLAG_FEC_DATA = 1 << 2        # bit 2: FEC 数据包 [新增]
UDP_FLAG_FEC_PARITY = 1 << 3      # bit 3: FEC 校验包 [新增]
UDP_FLAG_RESERVED = 0x7FFFFF00    # bit 4-30: 保留
UDP_FLAG_FRAGMENTED = 1 << 31     # bit 31: 分片包

# =============================================================================
# FEC 配置
# =============================================================================

# 默认 FEC 参数
DEFAULT_FEC_GROUP_SIZE = 4        # K: 每组数据包数
DEFAULT_FEC_PARITY_COUNT = 1      # M: 每组校验包数

# FEC 组头部大小
FEC_GROUP_HEADER_SIZE = 4         # group_id(2) + packet_idx(1) + total(1)

# =============================================================================
# PLI 配置
# =============================================================================

# PLI 控制消息类型
# 方案 A: 复用现有消息
CONTROL_TYPE_RESET_VIDEO = 0x11   # TYPE_RESET_VIDEO = 17

# 方案 B: 新增消息 (可选)
CONTROL_TYPE_PLI_REQUEST = 0x14   # TYPE_PLI_REQUEST = 20

# PLI 触发阈值
DEFAULT_PLI_THRESHOLD = 10        # 连续丢包多少次后发送 PLI
DEFAULT_PLI_COOLDOWN = 1.0        # PLI 请求冷却时间（秒）
```

---

## 6. 新增 Demuxer 架构

### 6.1 架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Demuxer 架构 (扩展后)                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   ADB 隧道 / TCP 模式                    UDP 网络模式                    │
│   ┌──────────────────┐                   ┌──────────────────┐           │
│   │ TCP Socket       │                   │ UDP Socket       │           │
│   └────────┬─────────┘                   └────────┬─────────┘           │
│            │                                      │                      │
│            ▼                                      ▼                      │
│   ┌──────────────────┐                   ┌──────────────────┐           │
│   │ StreamingVideo   │                   │ UdpVideoDemuxer  │           │
│   │ Demuxer          │                   │                  │           │
│   │                  │                   │ ├─ UDP 头部解析   │           │
│   │ - _recv_exact()  │                   │ ├─ 丢包检测      │           │
│   │ - 简单可靠       │                   │ ├─ FEC 解码      │           │
│   │                  │                   │ ├─ 分片重组      │           │
│   └────────┬─────────┘                   │ ├─ PLI 生成      │           │
│            │                             │ └─ 统计/监控     │           │
│            │                             └────────┬─────────┘           │
│            │                                      │                      │
│            ▼                                      ▼                      │
│   ┌──────────────────────────────────────────────────────────┐          │
│   │                   VideoPacket Queue                       │          │
│   └──────────────────────────────────────────────────────────┘          │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 6.2 类继承关系

```
                    ┌─────────────────┐
                    │  Thread/Runnable │
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  BaseDemuxer    │ │StreamingDemuxer │ │UdpVideoDemuxer  │
│  (buffer-based) │ │     Base        │ │   (新增)        │
└────────┬────────┘ └────────┬────────┘ └─────────────────┘
         │                   │
         ▼                   ▼
┌─────────────────┐ ┌─────────────────┐
│  VideoDemuxer   │ │ StreamingVideo  │
│                 │ │    Demuxer      │
└─────────────────┘ └─────────────────┘

说明:
- UdpVideoDemuxer 不继承 StreamingDemuxerBase（因为不使用 _recv_exact）
- UdpVideoDemuxer 直接处理 UDP packet，实现完全不同的读取策略
```

### 6.3 工厂方法更新

```python
# demuxer/factory.py

def create_video_demuxer_for_mode(
    mode: str,
    sock: socket.socket,
    codec_id: int,
    packet_queue_size: int = 1,
    **kwargs
) -> tuple:
    """
    根据连接模式创建合适的 demuxer。

    Args:
        mode: 连接模式
            - 'adb': ADB 隧道
            - 'tcp': 网络 TCP
            - 'udp': 网络 UDP
        sock: Socket (TCP 或 UDP)
        codec_id: 视频编码 ID
        **kwargs: 额外参数
            - control_channel: 控制通道（用于 PLI）
            - fec_enabled: 是否启用 FEC
            - pli_enabled: 是否启用 PLI
            - pli_threshold: PLI 触发阈值

    Returns:
        (demuxer, packet_queue)
    """
    packet_queue = Queue(maxsize=packet_queue_size)

    if mode == 'udp':
        from .udp_video import UdpVideoDemuxer
        demuxer = UdpVideoDemuxer(
            udp_socket=sock,
            packet_queue=packet_queue,
            codec_id=codec_id,
            control_channel=kwargs.get('control_channel'),
            fec_enabled=kwargs.get('fec_enabled', True),
            pli_enabled=kwargs.get('pli_enabled', True),
            pli_threshold=kwargs.get('pli_threshold', 10),
        )
    else:
        # ADB 和 TCP 使用相同的流式 demuxer
        from .video import StreamingVideoDemuxer
        demuxer = StreamingVideoDemuxer(sock, packet_queue, codec_id)

    return demuxer, packet_queue
```

---

## 7. 交互流程

### 7.1 正常流程（无丢包）

```
服务端                                      客户端
  │                                           │
  │  [UDP Header] [FEC Header] [Scrcpy] [Data] │
  │────────────────────────────────────────────>│
  │                                           │
  │                     解析 UDP 头部           │
  │                     解析 FEC 头部           │
  │                     解析 Scrcpy 头部        │
  │                     解码视频               │
  │                                           │
```

### 7.2 FEC 恢复流程

```
服务端                                      客户端
  │                                           │
  │  Data[0]                                  │
  │────────────────────────────────────────────>│ ✓ 收到
  │                                           │
  │  Data[1]                                  │
  │────────────────────────────────────────────>│ ✗ 丢失
  │                                           │
  │  Data[2]                                  │
  │────────────────────────────────────────────>│ ✓ 收到
  │                                           │
  │  Data[3]                                  │
  │────────────────────────────────────────────>│ ✓ 收到
  │                                           │
  │  Parity[0]                                │
  │────────────────────────────────────────────>│ ✓ 收到
  │                                           │
  │                      检测到 Data[1] 丢失   │
  │                      使用 Parity[0] 恢复   │
  │                      Data[1] = Data[0]^Data[2]^Data[3]^Parity[0]
  │                                           │
  │                      重组完整帧            │
  │                      解码成功             │
  │                                           │
```

### 7.3 PLI 请求流程

```
服务端                                      客户端
  │                                           │
  │  Data[0]                                  │
  │────────────────────────────────────────────>│ ✓
  │                                           │
  │  Data[1]                                  │
  │────────────────────────────────────────────>│ ✗
  │                                           │
  │  Data[2]                                  │
  │────────────────────────────────────────────>│ ✗
  │                                           │
  │  ... 连续丢失 >= 10 个包                   │
  │                                           │
  │                      触发 PLI 请求         │
  │  <─────────────────────────────────────────│
  │  [TCP] TYPE_RESET_VIDEO                   │
  │                                           │
  │  生成关键帧                               │
  │  KeyFrame                                 │
  │────────────────────────────────────────────>│
  │                                           │
  │                      解码恢复             │
  │                                           │
```

---

## 8. 配置参数

### 8.1 服务端命令行参数

```
--fec-enabled           启用 FEC (默认: false)
--fec-group-size=N      FEC 组大小 K (默认: 4)
--fec-parity-count=M    FEC 校验包数 M (默认: 1)
--pli-enabled           启用 PLI 响应 (默认: true)
```

### 8.2 客户端配置

```python
# config.py
@dataclass
class ClientConfig:
    # ... 现有字段 ...

    # FEC 配置
    fec_enabled: bool = False
    fec_group_size: int = 4      # K
    fec_parity_count: int = 1    # M

    # PLI 配置
    pli_enabled: bool = True
    pli_threshold: int = 10      # 连续丢包阈值
    pli_cooldown: float = 1.0    # 冷却时间（秒）
```

### 8.3 配置协商

```
连接建立时:

1. 客户端发送配置请求（通过 TCP 控制通道）
   {
       "fec_enabled": true,
       "fec_group_size": 4,
       "fec_parity_count": 1
   }

2. 服务端响应支持的配置
   {
       "fec_enabled": true,
       "fec_group_size": 4,
       "fec_parity_count": 1
   }

3. 双方使用协商后的配置开始传输
```

---

## 9. 测试用例

### 9.1 单元测试

```python
# tests/test_fec.py

def test_xor_fec_encode_decode():
    """测试 XOR FEC 编解码"""
    encoder = SimpleXorFecEncoder()
    decoder = FecDecoder()

    # 原始数据
    data_packets = [
        b'packet_0_data',
        b'packet_1_data',
        b'packet_2_data',
        b'packet_3_data',
    ]

    # 编码
    parity = encoder.encode(data_packets, 1)

    # 模拟丢失 packet_1
    received = [data_packets[0], None, data_packets[2], data_packets[3]]
    parity_received = parity[0]

    # 解码恢复
    decoder.add_data_packet(0, 0, 4, data_packets[0])
    decoder.add_data_packet(0, 2, 4, data_packets[2])
    decoder.add_data_packet(0, 3, 4, data_packets[3])
    decoder.add_parity_packet(0, 0, 4, parity_received)

    # 验证恢复
    assert decoder.can_recover(0, 1)  # group 0, 1 个丢失


def test_pli_trigger():
    """测试 PLI 触发"""
    demuxer = UdpVideoDemuxer(..., pli_threshold=3)

    # 模拟连续丢包
    demuxer._detect_loss(5)  # seq 0 丢失，跳到 5
    assert demuxer._consecutive_drops == 5
    assert demuxer._pli_sent == True
```

### 9.2 集成测试

```python
# tests/test_udp_with_fec.py

def test_udp_video_with_fec():
    """测试 UDP 视频流 + FEC"""
    # 1. 启动模拟服务端（带 FEC）
    # 2. 启动客户端
    # 3. 模拟网络丢包
    # 4. 验证视频流正常
    pass

def test_pli_request_response():
    """测试 PLI 请求-响应"""
    # 1. 启动服务端
    # 2. 启动客户端
    # 3. 触发 PLI
    # 4. 验证服务端收到请求并发送关键帧
    pass
```

---

## 附录 A: 数据包格式快速参考

### A.1 普通包

```
[UDP Header: 16B]
  seq: 4B (uint32, BE)
  ts:  8B (int64, BE)
  flags: 4B (uint32, BE)
[Scrcpy Header: 12B]
  pts_flags: 8B (uint64, BE)
  size: 4B (uint32, BE)
[Payload: NB]
```

### A.2 FEC 数据包

```
[UDP Header: 16B, flags |= 0x04]
[FEC Group Header: 4B]
  group_id: 2B (uint16, BE)
  packet_idx: 1B (uint8)
  total: 1B (uint8)
[Scrcpy Header: 12B]
[Payload: NB]
```

### A.3 FEC 校验包

```
[UDP Header: 16B, flags |= 0x08]
[FEC Parity Header: 4B]
  group_id: 2B (uint16, BE)
  parity_idx: 1B (uint8)
  total: 1B (uint8)
[Parity Data: NB]
```

### A.4 分片包（与现有格式相同）

```
[UDP Header: 16B, flags |= 0x80000000]
[frag_idx: 4B]
[fragment data: NB]
```

---

## 附录 B: 版本兼容性

| 版本 | FEC | PLI | 描述 |
|-----|-----|-----|------|
| 1.0 | ✗ | ✗ | 当前版本 |
| 1.1 | ✗ | ✓ | 仅 PLI |
| 1.2 | ✓ | ✓ | FEC + PLI |

**兼容性规则**:
- 客户端 v1.2 连接服务端 v1.0: 降级为无 FEC/PLI
- 客户端 v1.0 连接服务端 v1.2: 正常工作，忽略 FEC 包

---

**文档维护者**: Claude AI
**确认状态**: ⚠️ 待评审
