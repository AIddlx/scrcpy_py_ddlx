# 预览窗口跨进程事件驱动架构设计

## 1. 当前架构分析

### 1.1 主窗口架构（同进程，事件驱动）

```
┌─────────────────────────────────────────────────────────────────┐
│                        主进程                                    │
│                                                                 │
│  解码线程                        主线程 (Qt GUI)                │
│  ┌──────────────┐               ┌──────────────────────┐       │
│  │ Decoder      │               │ OpenGLVideoWindow    │       │
│  │     │        │   Signal      │     │                │       │
│  │     ├─解码帧─┼───────────────┼→ frame_ready         │       │
│  │     │        │   (跨线程)    │     │                │       │
│  │     └─→DelayBuffer           │     ├─→ update()     │       │
│  └──────────────┘               │     └─→ render()     │       │
│                                 └──────────────────────┘       │
│                                                                 │
│  CPU: ~1.5%（解码 + 渲染）                                       │
│  GIL: 解码和 GUI 在不同线程，竞争可控                            │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 MCP 预览窗口架构（独立进程，轮询）

```
┌─────────────────────┐              ┌─────────────────────┐
│     MCP 主进程       │              │     预览进程         │
│                     │              │                     │
│  ┌──────────────┐   │   SHM        │  ┌──────────────┐   │
│  │ 解码线程     │   │  ────────→   │  │ QTimer       │   │
│  │     │        │   │   写帧       │  │   16ms 轮询  │   │
│  │     └─→ SHM  │   │              │  │     │        │   │
│  └──────────────┘   │              │  │     ↓        │   │
│                     │              │  │ read_frame() │   │
│                     │              │  │     │        │   │
│                     │              │  │     ↓        │   │
│                     │              │  │ render()     │   │
│                     │              │  └──────────────┘   │
│                     │              │                     │
│  CPU: ~8%（解码 + 网络）            │  CPU: ~8%（轮询）    │
│  GIL: 无 GUI 竞争                  │  GIL: 单进程，无竞争  │
└─────────────────────┘              └─────────────────────┘
```

### 1.3 为什么 MCP 必须用独立预览进程

```
如果 MCP 服务器直接显示 GUI：

┌─────────────────────────────────────────────────────────────────┐
│                      MCP 服务器进程                              │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │
│  │ uvicorn  │  │ 网络线程 │  │ 解码线程 │  │ Qt GUI 线程  │    │
│  │ asyncio  │  │ UDP/TCP  │  │  PyAV    │  │  主线程      │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────┘    │
│       ↓             ↓             ↓              ↓              │
│  ════════════════════════════════════════════════════════════   │
│                         GIL 竞争区域                            │
│  ════════════════════════════════════════════════════════════   │
│                                                                 │
│  问题：                                                         │
│  1. Qt GUI 阻塞 → 解码线程等待 GIL → 丢帧                      │
│  2. 解码线程持有 GIL → GUI 卡顿 → 操作延迟                      │
│  3. 网络线程等待 GIL → UDP 缓冲区溢出                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 改进后架构

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  ┌─────────────────────┐              ┌─────────────────────┐   │
│  │     MCP 主进程       │              │     预览进程         │   │
│  │                     │              │                     │   │
│  │  ┌──────────────┐   │   SHM        │  ┌──────────────┐   │   │
│  │  │ 解码线程     │   │  ═════════   │  │ QSocketNotif │   │   │
│  │  │     │        │   │  写帧数据    │  │   (等待通知) │   │   │
│  │  │     └─→ SHM  │   │              │  │     │        │   │   │
│  │  └──────────────┘   │              │  │     │        │   │   │
│  │         │           │              │  │     ↓        │   │   │
│  │         │           │  socketpair  │  │ 读 SHM 帧    │   │   │
│  │         ↓           │  ──────────  │  │     │        │   │   │
│  │  ┌──────────────┐   │  1字节通知   │  │     ↓        │   │   │
│  │  │ notify.send()│   ╞══════════════╡  │ render()     │   │   │
│  │  └──────────────┘   │              │  └──────────────┘   │   │
│  │                     │              │                     │   │
│  │  CPU: ~8%           │              │  CPU: ~1.5%         │   │
│  │  GIL: 无 GUI 竞争   │              │  空闲时: ~0%        │   │
│  └─────────────────────┘              └─────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 事件通知流程

```
时间线：
─────────────────────────────────────────────────────────────→ t

主进程:
    解码完成
        │
        ├─→ SHM.write_frame(frame)
        │       │
        │       └─→ 写入帧数据到共享内存
        │
        └─→ notify_socket.send(b'1')
                │
                └─→ 发送 1 字节通知

预览进程:
    QSocketNotifier 等待中（零 CPU）
                │
                ↓ 收到通知
        notify_socket.recv(1)
                │
                ├─→ SHM.read_frame()
                │       │
                │       └─→ 读取帧数据
                │
                └─→ trigger_render()
                        │
                        └─→ GPU 渲染
```

### 2.3 关键组件设计

#### 2.3.1 PreviewManager（主进程侧）

```python
class PreviewManager:
    def __init__(self):
        # 创建 socket pair
        self._notify_socket, self._child_notify_socket = socket.socketpair()

        # 传递给子进程的 fd
        self._child_notify_fd = self._child_notify_socket.fileno()

    def send_frame(self, frame, ...):
        # 1. 写入 SHM
        self._shm_writer.write_frame(frame, ...)

        # 2. 发送通知（非阻塞）
        try:
            self._notify_socket.send(b'1')
        except BlockingIOError:
            pass  # 缓冲区满，说明子进程在处理
```

#### 2.3.2 PreviewWindow（预览进程侧）

```python
class PreviewWindow(QMainWindow):
    def __init__(self, notify_fd, ...):
        # 从 fd 重建 socket
        self._notify_socket = socket.fromfd(notify_fd, ...)

        # 创建 QSocketNotifier
        self._notifier = QSocketNotifier(
            self._notify_socket.fileno(),
            QSocketNotifier.Type.Read
        )
        self._notifier.activated.connect(self._on_frame_notify)

    def _on_frame_notify(self, fd):
        # 读取通知字节
        self._notify_socket.recv(1)

        # 读取帧并渲染
        frame = self._shm_reader.read_frame_ex()
        if frame:
            self._widget.update_frame(frame)
            self._widget.trigger_render()
```

---

## 3. 跨平台兼容性

### 3.1 QSocketNotifier 平台支持

| 平台 | 支持 | 注意事项 |
|------|------|----------|
| Linux | ✅ | 原生支持 |
| macOS | ✅ | 原生支持 |
| Windows | ✅ | 需要 socket，不支持 Pipe |

### 3.2 socketpair 实现

```python
import socket

# 跨平台创建 socket pair
def create_socketpair():
    """
    创建 socket pair，用于跨进程通知。

    返回: (parent_socket, child_socket)
    """
    # Windows 和 Unix 都支持
    parent, child = socket.socketpair()

    # 设置非阻塞（可选）
    parent.setblocking(False)
    child.setblocking(False)

    return parent, child
```

### 3.3 子进程 socket 传递

```python
import multiprocessing as mp

# 方式 1：传递 fileno
def preview_process(notify_fd, ...):
    notify_socket = socket.fromfd(notify_fd, socket.AF_UNIX, socket.SOCK_STREAM)
    ...

# 方式 2：传递 socket（multiprocessing 会自动处理）
def preview_process(notify_socket, ...):
    # socket 在子进程中可用
    ...
```

---

## 4. 错误处理

### 4.1 通知丢失

**场景**：通知字节发送失败或被覆盖

**解决方案**：
```python
def _on_frame_notify(self, fd):
    # 清空通知缓冲区（可能有多个通知）
    while True:
        try:
            self._notify_socket.recv(1024)
        except BlockingIOError:
            break

    # SHM 有 counter，不会丢失帧
    while True:
        frame = self._shm_reader.read_frame_ex()
        if frame is None:
            break
        self._render_frame(frame)
```

### 4.2 子进程崩溃

**场景**：预览进程意外退出

**解决方案**：
```python
def send_frame(self, frame, ...):
    try:
        self._notify_socket.send(b'1')
    except (BrokenPipeError, ConnectionError):
        # 子进程已退出，清理资源
        self._cleanup()
```

### 4.3 缓冲区溢出

**场景**：发送速度 > 处理速度

**解决方案**：
```python
# 设置发送缓冲区大小
self._notify_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1)

# 非阻塞发送，忽略缓冲区满
try:
    self._notify_socket.send(b'1')
except BlockingIOError:
    pass  # 子进程还在处理上一帧
```

---

## 5. 性能分析

### 5.1 CPU 占用对比

| 操作 | 轮询模式 | 事件驱动模式 |
|------|----------|--------------|
| 空闲等待 | QTimer 中断 ~3% | QSocketNotifier ~0% |
| 通知开销 | 无 | recv() ~0.01ms |
| 帧读取 | 62.5/s × 0.3ms = ~2% | 60/s × 0.3ms = ~1.5% |
| 渲染 | 60/s × 1.2ms = ~1.5% | 60/s × 1.2ms = ~1.5% |
| **总计** | **~8%** | **~1.5-2%** |

### 5.2 延迟分析

| 延迟来源 | 轮询模式 | 事件驱动模式 |
|----------|----------|--------------|
| 帧可用到检测 | 平均 8ms | <0.1ms |
| 通知传输 | N/A | <0.01ms |
| 渲染 | ~1.2ms | ~1.2ms |
| **总延迟** | **~9ms** | **~1.3ms** |

---

## 6. 配置选项

```python
class PreviewManager:
    def __init__(
        self,
        event_driven: bool = True,  # 启用事件驱动模式
        notify_buffer_size: int = 1,  # 通知缓冲区大小
    ):
        ...
```

---

**创建日期**: 2026-02-23
**最后更新**: 2026-02-23
