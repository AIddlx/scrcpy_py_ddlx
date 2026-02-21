# 预览窗口处理

## 概述

本文档说明预览窗口的关键实现细节，包括窗口位置、尺寸调整、横竖屏切换和触控坐标处理。

## 文件位置

`scrcpy_py_ddlx/preview_process.py`

## 窗口居中

预览窗口启动时自动居中于主显示器：

```python
def _center_on_screen(self):
    """Center the window on the primary screen."""
    screen = QApplication.primaryScreen()
    if screen:
        screen_geometry = screen.availableGeometry()
        window_geometry = self.frameGeometry()
        x = (screen_geometry.width() - window_geometry.width()) // 2
        y = (screen_geometry.height() - window_geometry.height()) // 2
        self.move(x + screen_geometry.x(), y + screen_geometry.y())
```

## 宽高比维持

### resizeEvent 实现

用户拖动窗口边框时，自动调整以维持视频宽高比：

```python
def resizeEvent(self, event):
    # 根据拖动方向判断
    width_change = abs(new_width - old_width)
    height_change = abs(new_height - old_height)

    if width_change >= height_change:
        # 拖动竖边/角 → 保持宽度，调整高度
        corrected_height = int(new_width / device_aspect)
    else:
        # 拖动横边 → 保持高度，调整宽度
        corrected_width = int(new_height * device_aspect)
```

### 支持的操作

| 操作 | 效果 |
|-----|------|
| 拖动左/右边框 | 保持宽度，调整高度 |
| 拖动上/下边框 | 保持高度，调整宽度 |
| 拖动四个角 | 根据变化量判断 |

## 横竖屏切换

### 自动检测

系统自动检测帧尺寸变化：

```python
def _update_frame(self):
    # 从帧中提取尺寸
    h, w = frame.shape[:2]
    if (w, h) != self._device_size:
        self.set_device_size(w, h)  # 触发窗口调整
```

### 窗口调整

```python
def set_device_size(self, w: int, h: int):
    self._device_size = (w, h)
    # 根据新宽高比调整窗口
    new_h = int(current_w / device_aspect)
    self.resize(current_w, new_h)
```

## 触控坐标处理

### 关键问题

设备旋转时，必须使用 `_device_size` 而非 `_frame.shape` 进行坐标转换：

```python
def _get_device_coords(self, x, y):
    # 正确：使用 _device_size
    w, h = self._device_size

    # 错误：使用 _frame.shape（旋转过渡期会出错）
    # h, w = self._frame.shape[:2]
```

### 原因

旋转过渡期：
1. 新帧尺寸已变化（2400x1080）
2. 但 `_frame` 可能还是旧帧（1080x2400）
3. 使用 `_frame.shape` 会导致坐标计算错误

## MCP 服务器旋转处理

文件: `scrcpy_http_mcp_server.py`

### 问题背景

`inject_touch_event` 使用 `state.device_size` 归一化坐标，旋转后必须更新：

```python
def _handle_preview_control_event(self, event):
    width, height = self._client.state.device_size  # 必须是当前值！
    self._client.inject_touch_event(action, pointer_id, x, y, width, height, pressure)
```

### 解决方案

在帧发送循环中检测尺寸变化：

```python
if (frame_w, frame_h) != self._client.state.device_size:
    logger.info(f"Device rotation detected: {old} -> {new}")
    self._client.state.device_size = (frame_w, frame_h)
```

## 坐标系统

### 竖屏 (Portrait)

```
width < height
例: 1080x2400

(0,0) ───────────── (1079,0)
  │                    │
  │                    │
  │                    │
(0,2399) ───────── (1079,2399)
```

### 横屏 (Landscape)

```
width > height
例: 2400x1080

(0,0) ─────────────────────── (2399,0)
  │                               │
(0,1079) ─────────────────── (2399,1079)
```

### 重要说明

- 原点始终在**左上角**，无论横竖屏
- 旋转后 width 和 height 值会**互换**
- 调用 `get_state()` 获取当前 width、height 和 orientation

## 常见问题

### Q: 横屏时触控失效？

A: 检查以下点：
1. `state.device_size` 是否已更新
2. 坐标转换是否使用 `_device_size` 而非 `_frame.shape`
3. 窗口是否已调整到正确的宽高比

### Q: 旋转后窗口尺寸不正确？

A: 确保：
1. `set_device_size()` 被正确调用
2. widget 的 `_device_size` 已同步更新

### Q: 拖动边框后视频变形？

A: 检查 `resizeEvent` 逻辑，确保：
1. 正确判断拖动方向
2. 使用正确的设备宽高比计算

## 相关代码

- `preview_process.py` - 预览窗口实现
- `scrcpy_http_mcp_server.py` - MCP 服务器旋转检测
- `client.py` - 触控事件注入
