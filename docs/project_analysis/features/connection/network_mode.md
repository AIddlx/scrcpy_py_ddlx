# 网络模式 (TCP/UDP)

> 通过 WiFi/以太网直接连接设备，实现低延迟无线投屏

---

## 概述

网络模式使用 TCP + UDP 混合架构：
- **TCP**: 控制命令、能力协商、心跳
- **UDP**: 视频和音频流媒体数据

## 技术细节

### 通道结构

| 通道 | 协议 | 端口 | 用途 |
|------|------|------|------|
| 控制 | TCP | 27183 | 触摸/键盘命令、心跳 |
| 视频 | UDP | 27184 | H.264/H.265 视频流 |
| 音频 | UDP | 27184 | OPUS 音频流 (与视频复用) |
| 文件 | TCP | 27185 | 文件传输 |

### UDP 包格式

```
┌──────────────────────────────────────────────────────────────┐
│                      UDP Header (24 bytes)                    │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│ 类型(1)  │ 标志(1)  │ 序号(4)  │ 时间戳(8) │ 发送时间(8)    │
│ type     │ flags    │ seq      │ pts_ns   │ send_time_ns   │
├──────────┴──────────┴──────────┴──────────┴─────────────────┤
│ FEC 头 (4 bytes, 可选)                                        │
├──────────┬──────────┬────────────────────────────────────────┤
│ fec_idx  │ fec_k    │ 负载数据 (最大 ~1400 bytes)             │
└──────────┴──────────┴────────────────────────────────────────┘
```

### 心跳机制

```
客户端                                服务端
   │                                    │
   │──── PING (25 bytes) ─────────────►│
   │◄─── PONG (5 bytes) ───────────────│
   │                                    │
   │   间隔: 5 秒                        │
   │   超时: 15 秒无响应则断开            │
```

## 代码位置

| 组件 | 文件 |
|------|------|
| TCP 连接 | `client/connection.py:connect_network()` |
| UDP 接收 | `client/udp_packet_reader.py` |
| 视频解复用 | `core/demuxer/udp_video.py` |
| 音频解复用 | `core/demuxer/udp_audio.py` |
| 心跳处理 | `core/heartbeat.py` |

## 使用方式

```python
from scrcpy_py_ddlx import Client

# 网络模式连接
client = Client(
    device="192.168.1.100:5555",
    network_mode=True
)
client.start()
```

### 命令行选项

```bash
# 基本连接
python tests_gui/test_network_direct.py --ip 192.168.1.100

# Hot-connect 自动发现（无需指定 IP）
python tests_gui/test_network_direct.py --hot-connect

# Stay-alive 模式（支持多客户端连接）
python tests_gui/test_network_direct.py --stay-alive --ip 192.168.1.100

# 禁用认证
python tests_gui/test_network_direct.py --no-auth --ip 192.168.1.100
```

### 进程会话管理 (setsid)

**网络模式始终使用 `setsid`** 创建新会话，使服务端进程与 ADB shell 进程组分离：

```
ADB shell ──► nohup setsid sh -c 'server_cmd' &
                 │
                 └──► Server (独立会话)
                      │
                      └── USB 断开后仍运行
```

这意味着：
- **USB 拔插不会导致服务端终止** - 服务端在独立会话中运行，不受 ADB 连接状态影响
- `stay_alive` 参数控制服务端是否支持多客户端连接，而非控制进程会话行为

### Hot-Connect 自动发现

无需指定 IP，自动发现网络中的设备：

```bash
python tests_gui/test_network_direct.py --hot-connect
# 自动发现并连接第一个设备
```

## 优缺点

### 优点
- 无需数据线
- 延迟更低 (UDP 直接传输)
- 支持远程访问

### 缺点
- 需要网络认证
- 网络不稳定时可能丢包
- 需要设备在同一局域网或可达

## 相关文档

- [USB 模式](usb_mode.md)
- [网络认证](auth.md)
- [协议规范](../../../PROTOCOL_SPEC.md)
- [网络管道详解](../../../development/NETWORK_PIPELINE.md)
