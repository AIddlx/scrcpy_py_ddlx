# scrcpy-py-ddlx

纯 Python 实现的 scrcpy 客户端，支持 MCP 服务器，用于 Android 设备镜像和控制。

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 运行测试脚本

```bash
python tests_gui/test_direct.py
```

---

## 使用模式

| 模式 | 命令 | 说明 |
|------|------|------|
| **Python API** | `from scrcpy_py_ddlx import ScrcpyClient` | 作为 Python 库使用 |
| **MCP GUI** | `python scrcpy_mcp_gui.py` | Claude Code 可视化界面 |
| **HTTP MCP** | `python scrcpy_http_mcp_server.py` | HTTP MCP 服务器 |
| **Direct Test** | `python tests_gui/test_direct.py` | 快速测试（带视频窗口） |

---

## Python API 示例

```python
from scrcpy_py_ddlx import ScrcpyClient, ClientConfig

config = ClientConfig(
    server_jar="scrcpy-server",
    show_window=True,
    audio=True,
)

client = ScrcpyClient(config)
client.connect()

# 控制设备
client.tap(500, 1000)
client.home()
client.text("Hello World")

client.disconnect()
```

---

## 功能特性

- 🎥 **视频流** - 支持 H.264/H.265/AV1 编解码器
- 🔊 **音频流** - OPUS/AAC/FLAC，支持播放和录制
- 📋 **剪贴板同步** - PC 与设备自动同步
- 📱 **应用列表获取** - 获取设备已安装应用
- 🖱️ **完整控制** - 触摸、键盘、滚动、文字输入
- 🌐 **无线 ADB** - 无需 USB 连接
- 🤖 **MCP 服务器** - Claude Code 集成

---

## 文档

### 用户文档
- [快速开始](docs/user/quickstart.md) - 5 分钟上手
- [安装指南](docs/user/installation.md) - 完整安装说明
- [使用模式](docs/user/modes/) - 不同的使用方式
- [故障排除](docs/user/troubleshooting.md) - 常见问题

### API 文档
- [控制方法](docs/api/control.md) - 控制接口
- [协议说明](docs/api/protocol.md) - 协议参考

---

## 系统要求

- Python 3.8+
- Android 设备（API 21+）
- ADB（Android SDK Platform Tools）

### Python 依赖

#### 基础依赖（必需）

```bash
pip install av numpy
```

#### 可选依赖（按需安装）

```bash
# 视频窗口（GUI）
pip install PySide6

# 音频播放
pip install sounddevice

# HTTP MCP 服务器
pip install starlette uvicorn[standard]
```

#### 一键安装所有依赖

```bash
pip install -r requirements.txt
```

---

## 项目结构

```
scrcpy-py-ddlx/
├── scrcpy_py_ddlx/          # Python 包
├── scrcpy/                   # Server 源码（修改版 scrcpy）
├── scrcpy-server            # 预编译 server（可直接使用）
├── yadb                     # ADB 增强（支持网络 ADB 直连）
├── scrcpy_mcp_gui.py        # MCP GUI 服务器
├── scrcpy_http_mcp_server.py # HTTP MCP 服务器
├── tests_gui/               # 测试脚本
└── docs/                    # 文档
```

### 工具说明

- **scrcpy-server**: 修改版 scrcpy server，支持获取应用列表等扩展功能
- **yadb**: 自编译 ADB 工具，支持网络 ADB 无线调试（无需 USB 数据线）

---

## 许可证

MIT License

---

## 参考资料

- **[官方 scrcpy](https://github.com/Genymobile/scrcpy)** - Android 镜像与控制工具（原项目）
- **[yadb](https://github.com/ysbing/yadb)** - ADB 增强，支持网络 ADB 无线调试
- **[本仓库](https://github.com/AIddlx/scrcpy_py_ddlx)** - Python 客户端实现
