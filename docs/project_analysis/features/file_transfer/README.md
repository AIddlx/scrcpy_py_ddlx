# 文件传输

> PC 与设备之间的文件传输功能
>
> **更新**: 2026-03-01 - 添加 pull_file 自动路径功能

---

## 功能清单

| 功能 | 通道 | 文件 | 状态 |
|------|------|------|------|
| [ADB 文件操作](adb_file.md) | ADB | `core/file/file_ops.py` | ✅ |
| [网络文件通道](network_file.md) | TCP | `core/file/file_channel.py` | ✅ |
| 文件命令 | - | `core/file/file_commands.py` | ✅ |

---

## 通道对比

| 特性 | ADB 文件 | 网络文件 |
|------|---------|---------|
| 传输层 | ADB push/pull | 独立 TCP |
| 需要 USB | 是 (或 adb tcpip) | 否 |
| 速度 | 较快 | 快 |
| 断点续传 | 否 | 计划中 |
| 适用模式 | USB/网络 | 仅网络 |

---

## 使用方式

### Python API

```python
from scrcpy_py_ddlx import Client

client = Client(device="192.168.1.100:5555", network_mode=True)
client.start()

# 列出目录
files = client.list_dir("/sdcard/Download")

# 上传文件
client.push_file("local.txt", "/sdcard/Download/remote.txt")

# 下载文件 (指定路径)
client.pull_file("/sdcard/Download/remote.txt", "local.txt")

# 下载文件 (自动路径 → files/Download/remote.txt)
client.pull_file("/sdcard/DCIM/Camera/IMG.jpg")

# 删除文件
client.delete_file("/sdcard/Download/remote.txt")
```

### MCP 工具

```json
// 列出目录
{"tool": "list_dir", "arguments": {"path": "/sdcard/Download"}}

// 上传文件
{"tool": "push_file", "arguments": {"local": "local.txt", "remote": "/sdcard/Download/remote.txt"}}

// 下载文件 (指定路径)
{"tool": "pull_file", "arguments": {"device_path": "/sdcard/Download/remote.txt", "local_path": "local.txt"}}

// 下载文件 (自动路径)
{"tool": "pull_file", "arguments": {"device_path": "/sdcard/DCIM/Camera/IMG.jpg"}}
// → 保存到 files/DCIM/Camera/IMG.jpg

// 删除文件
{"tool": "delete_file", "arguments": {"path": "/sdcard/Download/remote.txt"}}
```

---

## 文件保存路径规范

| 功能 | 保存目录 | 示例 |
|------|----------|------|
| 截图 | `screenshots/` | `screenshot_20260301_232016.jpg` |
| 录音 | `recordings/` | `recording_20260301_231830.opus` |
| 视频 | `recordings/` | `video_20260301_231830.mp4` |
| 下载文件 | `files/<原路径>` | `files/DCIM/Camera/IMG.jpg` |

### pull_file 自动路径规则

| 设备路径 | 本地保存路径 |
|----------|--------------|
| `/sdcard/DCIM/Camera/IMG.jpg` | `files/DCIM/Camera/IMG.jpg` |
| `/storage/emulated/0/Download/test.txt` | `files/Download/test.txt` |
| `/data/app/file.txt` | `files/data/app/file.txt` |

---

## 相关文档

- [ADB 文件操作](adb_file.md)
- [网络文件通道](network_file.md)
- [文件传输开发文档](../../development/file_transfer/)
