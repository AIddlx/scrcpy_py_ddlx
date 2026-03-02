# file_channel.py

> **文件**: `core/file/file_channel.py`
> **功能**: 独立 TCP 文件通道客户端

---

## 概述

`FileChannel` 提供网络模式下的文件传输功能，与 `FileServer.java` 配合。

---

## FileInfo 数据类

```python
@dataclass
class FileInfo:
    name: str       # 文件名
    type: str       # "file" 或 "directory"
    size: int       # 大小
    mtime: int      # 修改时间
```

---

## FileChannel 类

```python
class FileChannel:
    def __init__(self)

    # 连接模式 1: 主动连接
    def connect(self, host: str, port: int, session_id: int) -> bool

    # 连接模式 2: 使用已连接的 socket
    def set_connected_socket(self, sock: socket.socket) -> None

    # 文件操作
    def list_dir(self, path: str) -> List[FileInfo]
    def pull_file(self, remote_path: str, local_path: str | None = None) -> bool  # v1.5: local_path 可选
    def push_file(self, local_path: str, remote_path: str) -> bool
    def delete_file(self, path: str) -> bool
    def stat(self, path: str) -> Optional[FileInfo]

    # 关闭连接
    def close(self) -> None
```

---

## 两种连接模式

### 模式 1: 主动连接

```python
channel = FileChannel()
channel.connect("192.168.1.100", 27185, session_id=12345)
```

### 模式 2: 使用预建立的 socket

```python
# 网络模式第 4 条 socket
file_socket = connection.get_file_socket()
channel = FileChannel()
channel.set_connected_socket(file_socket)
```

---

## 命令格式

### 请求

```
[cmd: 1B][length: 4B][payload: NB]
```

### 响应

```
[cmd | 0x80: 1B][status: 1B][length: 4B][payload: NB]
```

---

## 使用示例

```python
from scrcpy_py_ddlx.core.file import FileChannel

# 连接
channel = FileChannel()
channel.connect("192.168.1.100", 27185, session_id)

# 列出目录
files = channel.list_dir("/sdcard/Download")
for f in files:
    print(f"{f.name} ({f.size} bytes)")

# 下载文件
channel.pull_file("/sdcard/Download/test.txt", "local.txt")

# v1.5: 自动路径 (local_path 省略 → files/Download/test.txt)
channel.pull_file("/sdcard/Download/test.txt")

# 上传文件
channel.push_file("local.txt", "/sdcard/Download/remote.txt")

# 删除文件
channel.delete_file("/sdcard/Download/remote.txt")

# 关闭
channel.close()
```

---

## 自动路径规则 (v1.5)

当 `pull_file` 的 `local_path` 参数省略时，文件会自动保存到 `files/` 目录，并保持原有目录结构：

| 设备路径 | 自动保存路径 |
|---------|-------------|
| `/sdcard/DCIM/Camera/IMG.jpg` | `files/DCIM/Camera/IMG.jpg` |
| `/storage/emulated/0/Download/test.txt` | `files/Download/test.txt` |
| `/data/app/file.txt` | `files/data/app/file.txt` |

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `file_channel.py` | 通道客户端 |
| `file_commands.py` | 命令常量 |
| `file_ops.py` | ADB 文件操作 |

---

## 相关文档

- [FileServer.md](../server/FileServer.md) - 服务端文件服务器
- [network_file.md](../../features/file_transfer/network_file.md) - 网络文件传输
