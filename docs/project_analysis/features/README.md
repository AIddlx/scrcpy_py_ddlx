# scrcpy-py-ddlx 功能清单

> **版本**: v1.4
> **更新日期**: 2026-03-01
> **用途**: 项目功能全景图

---

## 目录结构

```
features/
├── README.md              ← 你在这里 (总览)
│
├── entry_points/          # 运行入口 ⭐ 新增
│   ├── README.md         # 入口总览
│   ├── usb_mode.md       # test_direct.py (USB 模式)
│   ├── network_mode.md   # test_network_direct.py (网络模式)
│   ├── mcp_http.md       # scrcpy_http_mcp_server.py
│   ├── mcp_stdio.md      # mcp_stdio.py
│   └── gui_app.md        # scrcpy_mcp_gui.py
│
├── connection/            # 连接管理
├── media/                 # 音视频管道
├── control/               # 控制功能
├── file_transfer/         # 文件传输
├── mcp/                   # MCP 服务
├── gui/                   # GUI 模块
├── experimental/          # 实验性功能
│
└── server/                # 服务端 (Android/Java) ⭐ 改写内容
    ├── README.md
    ├── udp_sender.md      # UDP 媒体发送 (改写)
    ├── fec_encoder.md     # FEC 编码器 (新增)
    ├── discovery.md       # UDP 发现/终止 (新增)
    ├── file_server.md     # 文件服务器 (新增)
    ├── auth_handler.md    # 认证处理器 (新增)
    └── server_main.md     # Server.java 改写
```

---

## 运行入口

| 入口文件 | 模式 | 说明 |
|---------|------|------|
| `tests_gui/test_direct.py` | USB | ADB Tunnel 模式，自动发现设备 |
| `tests_gui/test_network_direct.py` | 网络 | TCP 控制 + UDP 媒体，支持 FEC/认证 |
| `scrcpy_http_mcp_server.py` | MCP HTTP | HTTP JSON-RPC MCP 服务器 |
| `mcp_stdio.py` | MCP STDIO | 标准 MCP 服务器 (Claude Desktop) |
| `scrcpy_mcp_gui.py` | GUI | PyQt6 桌面应用 |

---

## 服务端改写内容 (Android/Java)

### 新增模块

| 模块 | 文件 | 功能 |
|------|------|------|
| **UDP 媒体发送** | `udp/UdpMediaSender.java` | UDP 视频音频发送，FEC 支持 |
| **FEC 编码器** | `udp/SimpleXorFecEncoder.java` | XOR 前向纠错编码 |
| **UDP 发现** | `udp/UdpDiscoveryReceiver.java` | 设备发现 + 远程终止 |
| **文件服务器** | `file/FileServer.java` | 网络文件传输服务 |
| **文件通道** | `file/FileChannelHandler.java` | TCP 文件通道处理 |
| **认证处理** | `AuthHandler.java` | HMAC-SHA256 认证 |

### 改写模块

| 模块 | 文件 | 改写内容 |
|------|------|---------|
| **Server 主类** | `Server.java` | setsid 进程控制、stay-alive 多客户端、UDP 发现集成 |
| **Options** | `Options.java` | 大量新参数 (FEC/认证/网络端口等) |
| **SurfaceEncoder** | `video/SurfaceEncoder.java` | UDP 发送集成 |

---

## 功能概览

### 按类别统计

| 类别 | 子功能数 | 状态 |
|------|---------|------|
| 运行入口 | 5 | ✅ |
| 连接管理 | 8 | ✅ |
| 音视频管道 | 12 | ✅ |
| 控制功能 | 6 | ✅ |
| 文件传输 | 4 | ✅ |
| MCP 服务 | 16 | ✅ |
| 服务端改写 | 10+ | ✅ |

---

## 版本历史

| 版本 | 日期 | 主要变更 |
|-----|------|---------|
| v1.5 | 2026-03-02 | 进程控制模式明确 (setsid vs stay_alive) |
| v1.4 | 2026-02-28 | HMAC-SHA256 认证 + 横屏截图修复 |
| v1.3 | 2026-02-20 | TCP 心跳机制 (PING/PONG) |
| v1.2 | 2026-02-20 | UDP header 24 字节，E2E 延迟追踪 |
| v1.1 | 2026-02-15 | 能力协商，FEC 支持 |
| v1.0 | 2026-02-13 | 网络UDP模式基础实现 |

---

## 快速导航

### 我是用户

- USB 连接: [entry_points/usb_mode.md](entry_points/usb_mode.md)
- 网络连接: [entry_points/network_mode.md](entry_points/network_mode.md)
- MCP 配置: [mcp/README.md](mcp/README.md)

### 我是开发者

- 协议规范: [PROTOCOL_SPEC.md](../../PROTOCOL_SPEC.md)
- 服务端改写: [server/README.md](server/README.md)
- 已知问题: [known_issues/](../../development/known_issues/)
