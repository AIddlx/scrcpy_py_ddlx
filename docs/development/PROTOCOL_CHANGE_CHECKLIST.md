# UDP 协议修改检查清单

> **版本**: 1.2
> **最后更新**: 2026-02-20
> **相关文档**: [PROTOCOL_SPEC.md](../PROTOCOL_SPEC.md)

本文档列出了修改 UDP 协议时**必须同步更新**的所有文件。任何遗漏都可能导致客户端-服务端通信失败。

---

## 当前协议常量

```
UDP_HEADER_SIZE = 24 字节
SCRCPY_HEADER_SIZE = 12 字节
```

**UDP Header 结构 (24字节)**:
```
偏移量   字段          大小
------   ----          ----
0        sequence      4B
4        timestamp     8B
12       flags         4B
16       send_time_ns  8B  [v1.2新增]
```

**关键偏移量**:
- UDP header: 0-23
- Scrcpy header: 24-35
- Payload: 36+

---

## 修改检查清单

修改 UDP header 或数据格式时，**按顺序检查以下所有文件**：

### 服务端 (Java)

| # | 文件 | 检查内容 |
|---|------|----------|
| 1 | `scrcpy/server/.../udp/UdpMediaSender.java` | `HEADER_SIZE` 常量、所有发送方法 |

### 客户端 (Python)

| # | 文件 | 检查内容 |
|---|------|----------|
| 2 | `scrcpy_py_ddlx/core/protocol.py` | `UDP_HEADER_SIZE` 常量 |
| 3 | `scrcpy_py_ddlx/core/demuxer/udp_video.py` | `_parse_udp_header()`、`UdpPacketHeader` 类 |
| 4 | `scrcpy_py_ddlx/client/client.py` | 第一个 UDP 包解析的**硬编码偏移量** |
| 5 | `scrcpy_py_ddlx/core/decoder/delay_buffer.py` | `FrameWithMetadata` 字段 |
| 6 | `scrcpy_py_ddlx/core/stream.py` | `VideoPacket` 字段 |
| 7 | `scrcpy_py_ddlx/core/decoder/video.py` | `push()` 调用参数 |
| 8 | `scrcpy_py_ddlx/core/player/video/opengl_widget.py` | E2E 延迟计算逻辑 |

### 文档

| # | 文件 | 检查内容 |
|---|------|----------|
| 9 | `docs/PROTOCOL_SPEC.md` | UDP 头部格式、版本号 |
| 10 | `docs/development/PROTOCOL_CHANGE_CHECKLIST.md` | 本文档 |

---

## 修改流程

```
┌─────────────────────────────────────────────────────────────┐
│                      修改前                                  │
├─────────────────────────────────────────────────────────────┤
│ 1. 阅读 docs/PROTOCOL_SPEC.md                               │
│ 2. 阅读本文档的检查清单                                      │
│ 3. 规划需要修改的文件                                        │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      修改中                                  │
├─────────────────────────────────────────────────────────────┤
│ 1. 按检查清单顺序修改所有相关文件                            │
│ 2. 注意硬编码偏移量（最容易遗漏）                            │
│ 3. 更新常量定义                                              │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      修改后                                  │
├─────────────────────────────────────────────────────────────┤
│ 1. 更新 docs/PROTOCOL_SPEC.md 版本号                        │
│ 2. 重新编译服务端: cd scrcpy/release && bash build_server.sh │
│ 3. 运行测试验证                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 历史问题记录

| 日期 | 问题 | 原因 | 修复 |
|------|------|------|------|
| 2026-02-20 | 连接失败 | UDP header 16→24 字节，client.py 偏移量未更新 | 更新偏移量 16→24, 28→36 |
| 2026-02-13 | GUI黑屏 | payload_size 解析错误 | 添加协议文档 |

---

## 相关文档

- [PROTOCOL_SPEC.md](../PROTOCOL_SPEC.md) - 完整协议规范
- [FEC_PLI_PROTOCOL_SPEC.md](../FEC_PLI_PROTOCOL_SPEC.md) - FEC 协议规范
- [NETWORK_PIPELINE.md](NETWORK_PIPELINE.md) - 网络管道详解
