# 文件传输功能实现指南

> **状态**: ✅ 已完成
> **日期**: 2026-02-25

---

## 功能概述

将 PC 上的文件通过拖放传输到 Android 设备：
- **APK 文件** → 自动安装到设备
- **其他文件** → 推送到 `/sdcard/Download/`

## 架构设计

### 实现方式

scrcpy 的文件传输是通过 **ADB 命令** 实现的，不是通过 scrcpy 协议：

```
┌─────────────────┐    拖放     ┌─────────────────┐    ADB 命令    ┌─────────────────┐
│  PC 文件系统    │  ═════════► │  Qt 窗口        │  ═════════════► │  Android 设备   │
│  (拖放源)       │             │  (拖放目标)     │  adb push/install│  /sdcard/Download│
└─────────────────┘             └─────────────────┘                 └─────────────────┘
```

### 为什么用 ADB 而不是 scrcpy 协议？

1. **简单可靠** - ADB 命令成熟稳定，无需额外协议设计
2. **官方做法** - scrcpy 官方也是通过 ADB 实现文件传输
3. **后台执行** - 不阻塞主线程和视频渲染

---

## 代码结构

### 新增文件

1. **`scrcpy_py_ddlx/core/file_pusher.py`**
   - `FilePusher` 类：后台线程处理文件推送请求
   - `FilePusherAction` 枚举：区分安装 APK 和推送文件
   - 支持完成回调通知

### 修改文件

1. **`scrcpy_py_ddlx/core/player/video/video_window.py`**
   - `OpenGLVideoWindow` 类添加拖放事件处理
   - `eventFilter` 转发子组件的拖放事件
   - `dragEnterEvent`, `dropEvent` 等事件处理

2. **`scrcpy_py_ddlx/core/decoder/video.py`**
   - 添加 `configure_content_detection()` 方法
   - 添加 `set_on_decode_error_callback()` 方法

3. **`tests_gui/test_direct.py`**
   - 初始化文件推送器
   - 添加文件传输完成回调

---

## 关键实现细节

### 1. QOpenGLWindow 的拖放问题

**问题**：`QOpenGLWindow` 继承自 `QWindow`，而 PySide6 的 `QWindow` 没有绑定 `setAcceptDrops` 方法。

**解决方案**：
- `QOpenGLWindow` 通过 `QWidget.createWindowContainer()` 包装到 `QWidget` 中
- 在外层 `QMainWindow` 上处理拖放事件
- 使用 `eventFilter` 转发容器 widget 的拖放事件

```python
# video_window.py
class OpenGLVideoWindow(QMainWindow):
    def __init__(self):
        # 创建容器 widget
        self._video_widget = QWidget.createWindowContainer(self._opengl_renderer)

        # 关键：在容器上启用拖放
        self._video_widget.setAcceptDrops(True)

        # 关键：安装事件过滤器转发事件
        self._video_widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        """转发拖放事件到主窗口处理"""
        if obj == self._video_widget:
            if event.type() == QEvent.Type.Drop:
                self.dropEvent(event)
                return True
        return super().eventFilter(obj, event)
```

### 2. 后台线程处理

**问题**：`adb push/install` 命令可能需要较长时间，不能阻塞 UI。

**解决方案**：使用后台线程 + 队列

```python
# file_pusher.py
class FilePusher:
    def __init__(self):
        self._queue = Queue()
        self._thread = None

    def request(self, file_path: str) -> bool:
        """非阻塞请求，立即返回"""
        self._queue.put((action, file_path))
        return True

    def _run_loop(self):
        """后台线程处理"""
        while True:
            action, file_path = self._queue.get()
            if action == INSTALL_APK:
                subprocess.run(["adb", "install", "-r", file_path])
            else:
                subprocess.run(["adb", "push", file_path, "/sdcard/Download/"])
```

### 3. VideoDecoder 缺失方法

**问题**：`components.py` 调用了 `decoder.configure_content_detection()` 但方法不存在。

**解决方案**：添加方法到 `VideoDecoder` 类

```python
# video.py
def configure_content_detection(self, enabled=True, interval=5, ...):
    """配置内容检测设置"""
    self._content_detection_enabled = enabled
    self._content_detection_interval = interval

def set_on_decode_error_callback(self, callback):
    """设置解码错误回调（用于触发 PLI）"""
    self._on_decode_error_callback = callback
```

---

## 使用方式

### 基本使用

1. 运行测试脚本：
   ```bash
   python -X utf8 tests_gui/test_direct.py
   ```

2. 拖放文件到预览窗口：
   - **APK 文件** → 自动安装
   - **其他文件** → 推送到 `/sdcard/Download/`

### 批量拖放

支持同时拖放多个文件，会依次推送到设备。

---

## 调试日志

启用拖放调试日志：

```python
logging.getLogger('scrcpy_py_ddlx.core.player.video.video_window').setLevel(logging.DEBUG)
```

日志示例：
```
[DND] dragEnterEvent triggered
[DND] Accepted drag with 1 file(s)
[DND] dropEvent triggered
[DND] Dropped 1 file(s)
[DND] Processing file: C:/path/to/file.txt
[DND] _push_file called: C:/path/to/file.txt
[DND] Using global file pusher
Request to push C:/path/to/file.txt
File pusher thread started
Pushing C:/path/to/file.txt to /sdcard/Download/...
Successfully pushed C:/path/to/file.txt to /sdcard/Download/
```

---

## 遇到的问题及解决

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `setAcceptDrops` 不存在 | PySide6 的 `QWindow` 没有此方法 | 在 `QMainWindow` 容器上处理 |
| 拖放事件不触发 | 容器 widget 覆盖了整个窗口 | 使用 `eventFilter` 转发事件 |
| `configure_content_detection` 不存在 | 方法未实现 | 添加到 `VideoDecoder` 类 |
| UI 阻塞 | ADB 命令同步执行 | 使用后台线程 + 队列 |

---

## 扩展建议

### 1. 进度显示

可以扩展 `FilePusher` 支持进度回调：

```python
def request(self, file_path: str, on_progress: Callable[[int, int], None] = None):
    """支持进度回调的请求"""
    pass
```

### 2. 拖放目标目录

可以支持用户指定推送目标：

```python
def set_push_target(self, target: str):
    """设置推送目标目录"""
    self._push_target = target
```

### 3. 传输队列管理

可以添加队列查询和取消功能：

```python
def get_pending_count(self) -> int:
    """获取待传输文件数量"""
    return self._queue.qsize()

def cancel_all(self):
    """取消所有待传输文件"""
    # 清空队列
```

---

## 参考

- [Qt Drag and Drop](https://doc.qt.io/qt-6/dnd.html)
- [ADB Commands Reference](https://developer.android.com/studio/command-line/adb)
- [scrcpy file_pusher.c](https://github.com/Genymobile/scrcpy/blob/master/app/src/file_pusher.c)
