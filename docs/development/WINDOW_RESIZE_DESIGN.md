# 窗口缩放设计规范

本文档描述客户端和服务端如何配合处理窗口缩放和设备旋转。

---

## 1. 系统架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Android 服务端                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│  DisplaySizeMonitor ──► ScreenCapture ──► CaptureReset ──► SurfaceEncoder   │
│        │                    │                │                │             │
│        │ 检测尺寸变化        │ 调用 invalidate │ 设置 reset 标志  │ 重启编码器  │
│        ▼                    ▼                ▼                ▼             │
│  onDisplaySizeChanged  invalidate()    onInvalidated()  signalEndOfInputStream│
│                                                                │             │
│                                                                ▼             │
│                                                          Streamer           │
│                                                          发送新配置包        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ UDP/TCP
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Python 客户端                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  VideoDecoder ──► OpenGLVideoWidget ──► VideoWindow                         │
│       │                  │                     │                            │
│       │ 检测配置变化       │ 检测帧尺寸变化      │ 调整窗口大小               │
│       ▼                  ▼                     ▼                            │
│  Config changed    Frame size changed   _do_frame_size_changed             │
│                                        │                                    │
│                                        ▼                                    │
│                                   self.resize()                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 设计目标

1. **用户可自由调整窗口大小**：用户可以任意缩放窗口，不会被"锁定"
2. **视频保持正确宽高比**：视频在窗口中居中显示，不变形
3. **设备旋转时窗口自动调整**：横竖屏切换时，窗口尺寸和比例自动适应
4. **保持用户缩放偏好**：设备旋转后，保持用户之前设定的缩放比例

## 3. 职责划分

### 3.1 服务端（Android）

#### 3.1.1 DisplaySizeMonitor
- **位置**：`scrcpy/server/src/main/java/com/genymobile/scrcpy/video/DisplaySizeMonitor.java`
- **职责**：监听显示器尺寸变化（包括旋转）

```
┌─────────────────────────────────────────────────────────────┐
│                     DisplaySizeMonitor                       │
├─────────────────────────────────────────────────────────────┤
│ 机制：                                                       │
│   - Android 14 以下：使用 DisplayManager.registerDisplayListener │
│   - Android 14+：使用 WindowManager.registerDisplayWindowListener │
│                                                             │
│ 流程：                                                       │
│   1. onDisplayChanged() / onDisplayConfigurationChanged()   │
│   2. checkDisplaySizeChanged()                              │
│   3. 如果尺寸变化：listener.onDisplaySizeChanged()            │
│                                                             │
│ 日志：                                                       │
│   "DisplaySizeMonitor: requestReset(): 1264x2800 -> 2800x1264"│
└─────────────────────────────────────────────────────────────┘
```

#### 3.1.2 ScreenCapture
- **位置**：`scrcpy/server/src/main/java/com/genymobile/scrcpy/video/ScreenCapture.java`
- **职责**：屏幕捕获，响应尺寸变化

```
┌─────────────────────────────────────────────────────────────┐
│                       ScreenCapture                          │
├─────────────────────────────────────────────────────────────┤
│ 关键代码：                                                    │
│   displaySizeMonitor.start(displayId, this::invalidate);    │
│                                                             │
│ 流程：                                                       │
│   1. onDisplaySizeChanged() 被调用                           │
│   2. 调用 invalidate()                                       │
│   3. 触发 CaptureReset.onInvalidated()                      │
└─────────────────────────────────────────────────────────────┘
```

#### 3.1.3 CaptureReset
- **位置**：`scrcpy/server/src/main/java/com/genymobile/scrcpy/video/CaptureReset.java`
- **职责**：管理捕获重置状态

```
┌─────────────────────────────────────────────────────────────┐
│                       CaptureReset                           │
├─────────────────────────────────────────────────────────────┤
│ 关键方法：                                                    │
│   onInvalidated() { reset(); }                              │
│   reset() {                                                  │
│       reset.set(true);                                       │
│       runningMediaCodec.signalEndOfInputStream();           │
│   }                                                          │
│                                                             │
│ 作用：                                                       │
│   - 设置 reset 标志                                          │
│   - 向正在运行的 MediaCodec 发送 EOS 信号中断编码循环         │
└─────────────────────────────────────────────────────────────┘
```

#### 3.1.4 SurfaceEncoder
- **位置**：`scrcpy/server/src/main/java/com/genymobile/scrcpy/video/SurfaceEncoder.java`
- **职责**：视频编码，处理重置

```
┌─────────────────────────────────────────────────────────────┐
│                      SurfaceEncoder                          │
├─────────────────────────────────────────────────────────────┤
│ 关键流程（streamCapture 循环）：                              │
│   do {                                                       │
│       reset.consumeReset();  // 消费 reset 标志              │
│       capture.prepare();     // 获取新的视频尺寸              │
│       Size size = capture.getSize();                         │
│                                                             │
│       if (restartCount > 0) {                                │
│           Ln.i("Capture restarted: new size=" + size);      │
│       }                                                      │
│                                                             │
│       streamer.writeVideoHeader(size);  // 发送新配置        │
│       // ... 配置编码器并开始编码 ...                         │
│   } while (alive);                                           │
│                                                             │
│ 日志：                                                       │
│   "Capture restarted: new size=2800x1264 (restart #1)"      │
└─────────────────────────────────────────────────────────────┘
```

#### 3.1.5 Streamer
- **位置**：`scrcpy/server/src/main/java/com/genymobile/scrcpy/device/Streamer.java`
- **职责**：发送视频配置和帧数据

```
┌─────────────────────────────────────────────────────────────┐
│                        Streamer                              │
├─────────────────────────────────────────────────────────────┤
│ writeVideoHeader(Size videoSize):                           │
│   payload.putInt(codec.getId());     // 4 bytes             │
│   payload.putInt(videoSize.getWidth());   // 4 bytes        │
│   payload.putInt(videoSize.getHeight());  // 4 bytes        │
│                                                             │
│ 配置包格式：                                                  │
│   - pts_flags: 0x8000000000000000 (CONFIG 标志)              │
│   - size: 12 bytes                                          │
│   - payload: codec_id + width + height                      │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 客户端（Python）

#### 3.2.1 VideoWindow / OpenGLVideoWindow
- **负责**：窗口级别的尺寸管理
- **不负责**：强制窗口宽高比（这由 OpenGL 渲染处理）

```
┌─────────────────────────────────────────────────────────────┐
│                    VideoWindow / OpenGLVideoWindow          │
├─────────────────────────────────────────────────────────────┤
│ 职责：                                                       │
│   1. 设备旋转时调整窗口尺寸（_do_frame_size_changed）         │
│   2. 记录用户的缩放偏好（_user_scale）                        │
│   3. 计算初始窗口大小（set_device_info）                      │
│                                                             │
│ 不负责：                                                     │
│   1. 强制窗口宽高比（不要在 resizeEvent 中调用 resize()）     │
│   2. 视频渲染（由 OpenGLVideoWidget 负责）                   │
└─────────────────────────────────────────────────────────────┘
```

#### 3.2.2 OpenGLVideoWidget
- **负责**：视频渲染时的宽高比保持
- **位置**：`scrcpy_py_ddlx/core/player/video/opengl_widget.py`

```
┌─────────────────────────────────────────────────────────────┐
│                      OpenGLVideoWidget                       │
├─────────────────────────────────────────────────────────────┤
│ 职责：                                                       │
│   1. 在 paintGL() 中计算渲染区域，保持视频宽高比              │
│   2. 检测帧尺寸变化，通知 VideoWindow                         │
│   3. 视频在窗口中居中显示，上下/左右可能有黑边                │
│                                                             │
│ 关键代码（paintGL 中）：                                      │
│   scale_x = widget_size.width() / frame_width                │
│   scale_y = widget_size.height() / frame_height              │
│   scale = min(scale_x, scale_y)  # 使用较小的缩放比例         │
│   render_w = frame_width * scale                             │
│   render_h = frame_height * scale                            │
│   x = (widget_width - render_w) // 2  # 水平居中              │
│   y = (widget_height - render_h) // 2  # 垂直居中             │
└─────────────────────────────────────────────────────────────┘
```

## 4. 关键流程

### 4.1 初始化流程

```
1. Client 连接到设备
2. 收到设备能力信息（屏幕尺寸）
3. 调用 video_window.set_device_info(name, width, height)
   └─ 计算初始窗口大小（适应屏幕，不放大）
   └─ 设置 _device_size = (width, height)
4. 显示窗口
```

### 4.2 用户调整窗口大小

```
1. 用户拖动窗口边框
2. Qt 触发 resizeEvent(event)
3. resizeEvent 处理：
   ├─ 如果 _skip_resize_count > 0：跳过（程序调整）
   └─ 否则：更新 _user_scale = min(w/frame_w, h/frame_h)
4. paintGL 被调用，重新计算渲染区域
   └─ 视频在窗口中居中显示，保持宽高比
```

**重要**：resizeEvent 中**不应该**调用 `self.resize()` 来强制纠正窗口大小！

### 4.3 设备旋转流程（完整流程）

```
========== 服务端 (Android) ==========

1. 用户旋转设备
2. DisplaySizeMonitor 检测到尺寸变化
   └─ onDisplayChanged() / onDisplayConfigurationChanged()
   └─ checkDisplaySizeChanged()
   └─ 日志: "DisplaySizeMonitor: requestReset(): 1264x2800 -> 2800x1264"

3. ScreenCapture 收到通知
   └─ listener.onDisplaySizeChanged()
   └─ 调用 invalidate()

4. CaptureReset 处理重置
   └─ onInvalidated() { reset(); }
   └─ reset.set(true)
   └─ mediaCodec.signalEndOfInputStream()  // 中断编码循环

5. SurfaceEncoder 检测到 EOS
   └─ 退出 encode() 循环
   └─ 重新进入 streamCapture() 循环
   └─ capture.prepare() 获取新尺寸
   └─ 日志: "Capture restarted: new size=2800x1264 (restart #1)"

6. Streamer 发送新配置
   └─ writeVideoHeader(newSize)
   └─ 发送 CONFIG 包 (pts_flags=0x8000..., size=12)
   └─ 包含: codec_id + width + height

7. MediaCodec 重新配置
   └─ format.setInteger(KEY_WIDTH, newWidth)
   └─ format.setInteger(KEY_HEIGHT, newHeight)
   └─ mediaCodec.configure(format, ...)
   └─ mediaCodec.start()

8. 开始编码新尺寸的帧

========== 客户端 (Python) ==========

9. VideoDecoder 收到 CONFIG 包
   └─ 检测到 codec config 标志
   └─ 日志: "Config changed, reinitializing decoder (screen rotation?)"
   └─ 重新初始化解码器

10. VideoDecoder 收到新尺寸的帧
    └─ 解码成功
    └─ 日志: "[DECODER] Frame size changed: 1264x2800 -> 2800x1264"

11. OpenGLVideoWidget.paintGL() 检测到帧尺寸变化
    └─ 比较当前帧尺寸与 _frame_width/_frame_height
    └─ 日志: "[OPENGL] Frame size changed: 1264x2800 -> 2800x1264"
    └─ 调用 _frame_size_changed_callback(width, height)

12. VideoWindow._on_frame_size_changed(width, height)
    └─ 日志: "[ROTATION] _on_frame_size_changed called: 2800x1264"
    └─ 去重检查（避免重复处理）
    └─ 保存当前缩放比例：_user_scale = min(w/old_w, h/old_h)
    └─ 调用 _do_frame_size_changed(width, height)

13. VideoWindow._do_frame_size_changed(width, height)
    └─ 日志: "[ROTATION] Saved scale before rotation: 0.417"
    └─ 更新 _device_size = (width, height)
    └─ 计算新窗口大小：window_w = width * _user_scale
    └─ 日志: "[ROTATION] Window size: 1168x527 for frame 2800x1264 (scale=0.417)"
    └─ 设置 _skip_resize_count = 5
    └─ 调用 self.resize(window_w, window_h)
```

## 5. 关键变量

| 变量 | 位置 | 说明 |
|------|------|------|
| `_device_size` | VideoWindow | 当前视频帧尺寸 (width, height) |
| `_user_scale` | VideoWindow | 用户的缩放偏好 (0.0-1.0)，基于较小的缩放比例 |
| `_skip_resize_count` | VideoWindow | 跳过接下来的 N 个 resizeEvent（程序调整时使用） |
| `_last_resize_size` | VideoWindow | 上次处理的帧尺寸，用于去重 |

## 6. 代码规范

### 6.1 客户端：resizeEvent 中禁止的操作

```python
# ❌ 禁止：在 resizeEvent 中调用 resize()
def resizeEvent(self, event):
    # 错误！这会导致窗口被锁定或无限循环
    self.resize(corrected_width, corrected_height)

# ❌ 禁止：使用 QTimer.singleShot 来"异步"纠正大小
def resizeEvent(self, event):
    QTimer.singleShot(0, lambda: self.resize(...))  # 错误！
```

### 6.2 客户端：resizeEvent 中允许的操作

```python
# ✅ 正确：只更新状态，不改变窗口大小
def resizeEvent(self, event):
    super().resizeEvent(event)

    if self._skip_resize_count > 0:
        self._skip_resize_count -= 1
        return

    # 更新用户的缩放偏好
    if device_width > 0 and device_height > 0:
        scale_x = new_width / device_width
        scale_y = new_height / device_height
        self._user_scale = min(scale_x, scale_y)
```

### 6.3 客户端：设备旋转时的窗口调整

```python
# ✅ 正确：在 _do_frame_size_changed 中调整窗口
def _do_frame_size_changed(self, width, height):
    # 1. 更新设备尺寸
    self._device_size = (width, height)

    # 2. 计算新窗口大小（保持用户的缩放偏好）
    window_w = int(width * self._user_scale)
    window_h = int(height * self._user_scale)

    # 3. 设置跳过计数，避免 resizeEvent 误更新 _user_scale
    self._skip_resize_count = 5

    # 4. 调整窗口大小
    self.resize(window_w, window_h)
```

## 7. 测试用例

### 7.1 基本功能测试

| 测试项 | 操作 | 预期结果 |
|--------|------|----------|
| 初始窗口大小 | 启动预览 | 窗口大小适应屏幕，视频完整显示 |
| 放大窗口 | 拖动窗口边框放大 | 窗口可以放大，视频保持比例居中 |
| 缩小窗口 | 拖动窗口边框缩小 | 窗口可以缩小，视频保持比例居中 |
| 非比例调整 | 只拖动宽度/高度 | 窗口可以非比例调整，视频保持比例（有黑边） |

### 7.2 旋转测试

| 测试项 | 操作 | 预期结果 |
|--------|------|----------|
| 竖屏→横屏 | 设备旋转到横屏 | 窗口调整为横屏比例，保持缩放偏好 |
| 横屏→竖屏 | 设备旋转到竖屏 | 窗口调整为竖屏比例，保持缩放偏好 |
| 连续旋转 | 快速多次旋转 | 窗口正确跟随，无卡死或异常 |

### 7.3 边界测试

| 测试项 | 操作 | 预期结果 |
|--------|------|----------|
| 最小窗口 | 缩小到极限 | 窗口有最小尺寸限制（200x200） |
| 最大窗口 | 放大到极限 | 窗口不超过屏幕可用区域 |
| 超大屏幕 | 4K 屏幕测试 | 窗口正确计算，不超出屏幕 |

## 8. 常见问题与解决方案

### Q1: 窗口无法调整大小 / 被"锁定"
**原因**：resizeEvent 中调用了 `self.resize()` 导致无限循环或覆盖用户操作
**解决**：移除 resizeEvent 中的 `resize()` 调用，让 OpenGL 渲染处理宽高比

### Q2: 视频变形
**原因**：渲染时没有正确计算缩放比例
**解决**：在 paintGL 中使用 `scale = min(scale_x, scale_y)` 保持宽高比

### Q3: 设备旋转后窗口大小不对
**原因**：`_do_frame_size_changed` 没有被正确调用
**解决**：检查回调链是否完整，确保 `_frame_size_changed_callback` 被正确设置

### Q4: 旋转后缩放偏好丢失
**原因**：`_user_scale` 在旋转前没有保存
**解决**：在 `_do_frame_size_changed` 开始时保存当前缩放比例

### Q5: 窗口大小调整混乱/闪烁
**原因**：帧尺寸变化回调被设置了两次（decoder 和 widget 都设置）
**解决**：只保留一个回调设置点（在 video_widget 中），移除 components.py 中的重复设置

## 关键修复记录

### 2026-02-21: 移除重复回调
**问题**：`components.py` 和 `video_window.py` 都设置了帧尺寸变化回调，导致同一个旋转被处理两次。

**修复**：
- 移除 `components.py` 中对 `video_decoder.set_frame_size_changed_callback()` 的调用
- 只保留 `video_window.py` 中 `video_widget.set_frame_size_changed_callback()` 的设置

**原因**：
1. 窗口调整必须在 GUI 线程上进行
2. `paintGL` 已经在 GUI 线程上运行
3. 避免多线程竞争导致的问题

## 9. 修改检查清单

修改窗口相关代码时，请检查：

- [ ] resizeEvent 中是否调用了 `self.resize()`？（应该没有）
- [ ] resizeEvent 中是否使用了 `QTimer.singleShot` 来调整大小？（应该没有）
- [ ] `_skip_resize_count` 是否在程序调整窗口前设置？
- [ ] `_user_scale` 是否在旋转前保存、旋转后使用？
- [ ] paintGL 中的宽高比计算是否使用 `min(scale_x, scale_y)`？
- [ ] 是否测试了放大、缩小、旋转三种场景？

## 10. 相关文件

### 10.1 客户端文件

| 文件 | 职责 |
|------|------|
| `scrcpy_py_ddlx/core/player/video/video_window.py` | 窗口管理，尺寸调整 |
| `scrcpy_py_ddlx/core/player/video/opengl_widget.py` | OpenGL 渲染，宽高比保持 |
| `scrcpy_py_ddlx/core/decoder/video.py` | 解码器，检测配置变化 |

### 10.2 服务端文件

| 文件 | 职责 |
|------|------|
| `video/DisplaySizeMonitor.java` | 监听显示器尺寸变化 |
| `video/ScreenCapture.java` | 屏幕捕获，响应尺寸变化 |
| `video/CaptureReset.java` | 管理捕获重置状态 |
| `video/SurfaceEncoder.java` | 视频编码，处理重置 |
| `device/Streamer.java` | 发送视频配置和帧数据 |

---

**文档版本**: 1.1
**创建日期**: 2026-02-21
**更新日期**: 2026-02-21
**维护者**: 修改窗口或旋转相关代码后，必须更新此文档

## 更新历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.1 | 2026-02-21 | 添加服务端设计规范，完善设备旋转流程 |
| 1.0 | 2026-02-21 | 初始版本，客户端设计规范 |
