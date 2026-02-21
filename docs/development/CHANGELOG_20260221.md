# 2026-02-21 修改记录

## 1. 屏幕旋转帧尺寸检测增强

### 问题
屏幕旋转时，有时窗口大小不会正确更新，因为 OpenGL widget 的帧尺寸检测可能漏掉第一帧。

### 修复
在 `VideoDecoder` 中添加了帧尺寸变化检测，作为 OpenGL widget 检测的备份。

**修改文件：**
- `scrcpy_py_ddlx/core/decoder/video.py`
  - 添加 `_frame_size_changed_callback` 属性
  - 添加 `set_frame_size_changed_callback()` 方法
  - 在 `_decode_packet()` 中检测帧尺寸变化

- `scrcpy_py_ddlx/client/components.py`
  - 在 `create_video_window()` 中连接解码器回调

- `scrcpy_py_ddlx/core/player/video/video_window.py`
  - 使用 `QTimer.singleShot(0, callback)` 实现线程安全的 UI 更新
  - 添加 `_do_frame_size_changed()` 执行实际 UI 更新

---

## 2. 编码器检测修复

### 问题
grep 模式漏掉了 `avc`（H264 的另一种命名），导致某些设备 H264 检测失败。

### 修复
更新 grep 模式为 `(h264|avc|h265|hevc|av1)`

**修改文件：**
- `tests_gui/test_network_direct.py`
- `docs/development/known_issues/encoder_detection_fix.md`

---

## 3. 窗口大小记忆（横竖屏分别保存）

### 问题
手机投屏需要支持横竖屏切换，用户希望：
- 横屏时调整窗口大小，下次横屏时记住
- 竖屏时调整窗口大小，下次竖屏时记住
- 两个方向的大小独立保存

### 修复方案

1. **分别保存横竖屏大小**
```python
self._saved_sizes: dict = {}  # {'portrait': (w, h), 'landscape': (w, h)}
```

2. **旋转前保存当前窗口大小**（关键修复）
```python
# BEFORE updating _device_size, save current window size to old orientation
if self._device_size[0] > 0 and self._device_size[1] > 0:
    old_orientation = 'portrait' if old_frame_h > old_frame_w else 'landscape'
    self._saved_sizes[old_orientation] = (self.width(), self.height())
```

3. **防抖机制**
```python
self._resize_debounce_ms: int = 100  # 100ms 内相同尺寸的 resize 请求被忽略
```

4. **区分程序调整和用户手动调整**
```python
self._programmatic_resize: bool = False
# 程序调用 resize 前设置 True，resizeEvent 检查后跳过保存
```

### 修改文件
- `scrcpy_py_ddlx/core/player/video/video_window.py`
  - `VideoWindow` 和 `OpenGLVideoWindow` 两个类都更新

---

## 4. 第一帧绿色画面（待解决）

### 原因
OpenGL 纹理在第一帧到达前未初始化，显示为绿色。

### 可能的修复
在 `initializeGL()` 或帧尺寸变化时清除纹理为黑色。
