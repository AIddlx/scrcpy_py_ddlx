# 截图与录音能力

## ADB 隧道模式的两种连接方式

### 模式一：解码器待命模式 (推荐)

```python
connect(video=True, audio=True)
```

**特点：**
- 建立 video socket + audio socket + control socket
- demuxer + decoder 持续运行
- 帧缓冲始终有最新帧
- GUI 实时预览可自由开关

**截图：**
- 方法：从帧缓冲直接获取
- 延迟：~16ms
- 代码：`client.screenshot()`

**实时预览：**
```python
# 需要时启动预览窗口
start_preview()

# 不需要时关闭
stop_preview()
```

**录音：** ✅ 支持

**CPU 消耗：** 中等（解码持续运行，GPU 渲染按需）

---

### 模式二：纯控制模式

```python
connect(video=False, audio=False)
```

**特点：**
- 只建立 control socket
- 不建立视频/音频 socket
- 最低功耗

**截图：**
- 方法：通过控制消息（SurfaceControl API）
- 延迟：~50-100ms
- 代码：`client.request_screenshot_async(callback)`

**录音：** ❌ 不支持

**CPU 消耗：** 最低（无解码）

---

## 对比总结

| 特性 | 解码器待命模式 | 纯控制模式 |
|------|---------------|-----------|
| Video Socket | ✅ | ❌ |
| Audio Socket | ✅ | ❌ |
| Control Socket | ✅ | ✅ |
| 解码器状态 | 持续运行 | 不运行 |
| 实时预览 | ✅ 可自由开关 | ❌ |
| 截图延迟 | ~16ms | ~50-100ms |
| 录音 | ✅ | ❌ |
| CPU 消耗 | 中等 | 最低 |

## 使用场景

| 场景 | 推荐模式 |
|------|---------|
| 需要随时查看屏幕 | 解码器待命模式 |
| 需要录制屏幕/音频 | 解码器待命模式 |
| 偶尔需要预览 | 解码器待命模式 |
| 只需要控制操作 | 纯控制模式 |
| 追求最低功耗 | 纯控制模式 |
| 后台自动化（无需截图） | 纯控制模式 |

## 代码示例

### 解码器待命模式

```python
# 连接
connect(video=True, audio=True)

# 截图 (低延迟)
screenshot("screen.jpg")

# 需要时启动预览
start_preview()

# 不需要时关闭
stop_preview()

# 录音
start_recording("audio.wav")
# ...
stop_recording()
```

### 纯控制模式

```python
# 连接
connect(video=False, audio=False)

# 截图 (控制消息)
screenshot("screen.jpg")  # 自动使用控制消息方式

# 触控操作
tap(500, 500)
swipe(100, 100, 500, 500)

# 按键操作
key_event(4)  # BACK
```
