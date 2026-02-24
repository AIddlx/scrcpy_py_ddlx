# MCP 预览窗口横屏触摸失效修复

## 问题描述

**现象**：MCP 预览窗口在横屏模式下触摸控制不生效，竖屏模式正常。

**影响范围**：
- `scrcpy_http_mcp_server.py --preview` 模式
- 设备旋转后触摸事件无法正确发送

**不影响**：
- `tests_gui/test_network_direct.py`（使用内置 Qt 窗口）
- 竖屏模式

---

## 根因分析

### 数据流对比

**test_network_direct.py（正常工作）**：
```
video_window._device_size
    ↓ (回调同步)
input_handler._device_size
    ↓
触摸事件使用正确的屏幕尺寸
```

**MCP 服务器（不工作）**：
```
preview_process._device_size (正确检测到旋转)
    ↓ (IPC 断裂！没有同步)
client.state.device_size (仍然是旧值)
    ↓
触摸事件使用错误的屏幕尺寸
```

### 问题本质

1. 预览进程使用 `_device_size=2400x1080` 转换坐标，得到设备坐标 `(1459, 622)`
2. MCP 服务器使用 `client.state.device_size=1080x2400` 作为 `screen_width/height`
3. 设备收到 `pos=(1459, 622), screen=(1080, 2400)`
4. **坐标 1459 超出了 screen_width=1080，设备认为坐标无效**

---

## 修复方案

### 核心思路

**在信息源头直接同步**：预览进程检测到旋转时，主动发送消息通知 MCP 服务器。

### 修改文件

#### 1. preview_process.py

在 `GLWindowContainer.set_device_size()` 中添加 IPC 通知：

```python
def set_device_size(self, w: int, h: int):
    """Update device size (called when device rotates)."""
    old_w, old_h = self._device_size
    if (w, h) == (old_w, old_h):
        return  # No change

    logger.info(f"Device size changed: {old_w}x{old_h} -> {w}x{h}")
    self._device_size = (w, h)

    # Notify MCP server about device size change for touch events
    try:
        control_queue.put(('device_size_changed', w, h), timeout=0.1)
        logger.info(f"[ROTATION] Sent device_size_changed to MCP server: {w}x{h}")
    except Exception as e:
        logger.warning(f"[ROTATION] Failed to send device_size_changed: {e}")

    # ... 窗口调整代码
```

#### 2. scrcpy_http_mcp_server.py

在 `_handle_preview_control_event()` 中处理新消息：

```python
# Device size change notification from preview process
elif event_type == 'device_size_changed':
    w, h = event[1], event[2]
    logger.info(f"[MCP] Received device_size_changed from preview: {w}x{h}")
    # Update client.state.device_size for touch events
    old_w, old_h = self._client.state.device_size
    if (w, h) != (old_w, old_h):
        self._client.state.device_size = (w, h)
        logger.info(f"[MCP] Updated client.state.device_size: {old_w}x{old_h} -> {w}x{h}")
    return True
```

#### 3. delay_buffer.py（辅助修复）

确保 `consume()` 和 `wait_for_frame()` 返回 `width` 和 `height`：

```python
def consume(self) -> Optional[FrameWithMetadata]:
    # ...
    width = self._pending_frame.width
    height = self._pending_frame.height

    return FrameWithMetadata(
        frame=raw_frame, packet_id=packet_id, pts=pts,
        capture_time=capture_time, udp_recv_time=udp_recv_time,
        width=width, height=height  # 添加这两个字段
    )
```

---

## 验证方法

### 日志验证

旋转设备后，应该看到以下日志：

```
# 预览进程
Device size changed: 1080x2400 -> 2400x1080
[ROTATION] Sent device_size_changed to MCP server: 2400x1080

# MCP 服务器
[MCP] Received device_size_changed from preview: 2400x1080
[MCP] Updated client.state.device_size: 1080x2400 -> 2400x1080
```

### 功能验证

1. 启动 MCP 服务器：`python scrcpy_http_mcp_server.py --connect --preview`
2. 旋转设备到横屏
3. 在预览窗口点击/滑动
4. 确认设备响应触摸操作

---

## 经验教训

### 1. 在信息源头处理

**错误思路**：在 MCP 服务器的帧发送循环中检测旋转（间接，不可靠）
**正确思路**：预览进程已经正确检测旋转，直接发送消息通知

### 2. 对比工作的参照物

`test_network_direct.py` 可以工作，对比两者的数据流差异，快速定位问题。

### 3. 跨进程状态需要显式同步

不要假设状态会自动同步，使用 IPC 消息显式传递状态变化。

---

## 相关文件

- `scrcpy_py_ddlx/preview_process.py`
- `scrcpy_http_mcp_server.py`
- `scrcpy_py_ddlx/core/decoder/delay_buffer.py`

---

## 日期

2026-02-25
