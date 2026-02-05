# Python API 模式

将 scrcpy-py-ddlx 作为 Python 库使用。

---

## 基本用法

```python
from scrcpy_py_ddlx import ScrcpyClient, ClientConfig

# 创建配置
config = ClientConfig(
    server_jar="scrcpy-server",
    show_window=True,    # 显示视频窗口
    audio=True,          # 启用音频
)

# 连接
client = ScrcpyClient(config)
client.connect()

# 控制设备
client.tap(500, 1000)    # 点击屏幕
client.home()           # 按主页键
client.text("Hello")    # 输入文字

# 断开连接
client.disconnect()
```

---

## 配置选项

| 选项 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `server_jar` | str | `"scrcpy-server"` | Server 文件路径 |
| `host` | str | `"localhost"` | ADB 服务器地址 |
| `port` | int | `27183` | ADB 服务器端口 |
| `show_window` | bool | `False` | 显示视频窗口 |
| `audio` | bool | `False` | 启用音频流 |
| `bitrate` | int | `8000000` | 视频码率 (bps) |
| `max_fps` | int | `60` | 最大帧率 |
| `clipboard_autosync` | bool | `False` | 自动同步剪贴板 |

---

## 控制方法

### 触摸与鼠标

```python
client.tap(x, y)                    # 单击
client.swipe(x1, y1, x2, y2)        # 滑动
client.long_press(x, y, duration=500)  # 长按
```

### 按键

```python
client.home()                       # 主页键
client.back()                       # 返回键
client.recent_apps()                # 最近任务
client.text("Hello World")          # 输入文字
client.press_key("KEYCODE_ENTER")   # 按键
```

### 设备控制

```python
client.turn_screen_on()             # 亮屏
client.turn_screen_off()            # 熄屏
client.rotate_device()              # 旋转屏幕
```

### 剪贴板

```python
# 获取剪贴板
text = client.get_clipboard()

# 设置剪贴板
client.set_clipboard("Hello")
```

### 应用列表

```python
# 获取所有应用
apps = client.list_apps()

# 筛选用户应用
user_apps = [a for a in apps if not a["system"]]
```

---

## 事件回调

```python
def on_frame(frame):
    """收到视频帧时调用"""
    print(f"帧: {frame.shape}")

def on_audio_frame(data):
    """收到音频帧时调用"""
    print(f"音频: {len(data)} 字节")

config = ClientConfig(
    frame_callback=on_frame,
    audio_callback=on_audio_frame,
)
```

---

## 示例

### 无窗口截图

```python
from scrcpy_py_ddlx import ScrcpyClient, ClientConfig
import numpy as np

config = ClientConfig(show_window=False, audio=False)
client = ScrcpyClient(config)
client.connect()

# 获取单帧
frame = client.last_frame
if frame is not None:
    from PIL import Image
    img = Image.fromarray(frame)
    img.save("screenshot.png")

client.disconnect()
```

### 录制音频

```python
config = ClientConfig(show_window=False, audio=True)
client = ScrcpyClient(config)
client.connect()

# 开始录制
client.start_opus_recording("output.opus")

# 录制 10 秒
import time
time.sleep(10)

# 停止录制
client.stop_opus_recording()
client.disconnect()
```

---

## API 参考

详见 [API 文档](../../api/)。
