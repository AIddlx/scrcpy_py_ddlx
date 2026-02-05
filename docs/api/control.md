# scrcpy-py-ddlx 控制功能完整列表

## 所有控制方法（按类别）

### 1. 按键控制 (Keycode Control)

| 方法 | 说明 |
|------|------|
| `home()` | 按HOME键（返回主页） |
| `back()` | 按BACK键（返回上一级） |
| `volume_up()` | 按音量+键 |
| `volume_down()` | 按音量-键 |
| `menu()` | 按MENU键 |
| `enter()` | 按回车键 |
| `tab()` | 按Tab键 |
| `escape()` | 按ESC键（退出输入状态） |
| `dpad_up()` | 方向键上 |
| `dpad_down()` | 方向键下 |
| `dpad_left()` | 方向键左 |
| `dpad_right()` | 方向键右 |
| `dpad_center()` | 方向键中心 |
| `app_switch()` | 应用切换键（显示最近任务） |
| `inject_keycode(keycode, action, repeat, metastate)` | 注入任意按键事件 |

### 2. 文本输入 (Text Input)

| 方法 | 说明 |
|------|------|
| `inject_text(text)` | 输入文本（支持特殊字符、中文） |

### 3. 触摸控制 (Touch Control)

| 方法 | 说明 |
|------|------|
| `tap(x, y)` | 点击屏幕坐标(x, y) |
| `swipe(x1, y1, x2, y2, duration_ms)` | 滑动（从点1到点2，持续duration_ms毫秒） |
| `inject_touch_event(action, pointer_id, x, y, w, h, pressure)` | 注入触摸事件 |

### 4. 滚动控制 (Scroll Control)

| 方法 | 说明 |
|------|------|
| `inject_scroll_event(x, y, w, h, hscroll, vscroll)` | 滚动事件 |

### 5. 面板控制 (Panel Control)

| 方法 | 说明 |
|------|------|
| `expand_notification_panel()` | 展开通知面板（状态栏） |
| `expand_settings_panel()` | 展开设置面板（快捷设置） |
| `collapse_panels()` | 折叠所有面板 |

### 6. 显示控制 (Display Control)

| 方法 | 说明 |
|------|------|
| `set_display_power(on)` | 设置显示电源（True=开，False=关） |
| `turn_screen_on()` | 打开屏幕 |
| `turn_screen_off()` | 关闭屏幕 |

### 7. 设备控制 (Device Control)

| 方法 | 说明 |
|------|------|
| `rotate_device()` | 旋转设备（横屏/竖屏切换） |
| `start_app(app_name)` | 启动应用（按应用名称或包名） |
| `open_hard_keyboard_settings()` | 打开物理键盘设置 |
| `reset_video()` | 重置视频流（视频卡顿时使用） |

### 8. 剪贴板 (Clipboard)

| 方法 | 说明 |
|------|------|
| `set_clipboard(text, paste)` | 设置剪贴板内容（paste=True会自动粘贴） |
| `get_clipboard(copy_key)` | 获取剪贴板内容（copy_key=COPY或CUT） |

### 9. 特殊控制 (Special Control)

| 方法 | 说明 |
|------|------|
| `back_or_screen_on(action)` | 返回键或点亮屏幕（如果屏幕关闭） |

## 使用示例

### 基本按键
```python
from scrcpy_py_ddlx.client_v2 import ScrcpyClient, ClientConfig

config = ClientConfig(server_jar="scrcpy-server")
client = ScrcpyClient(config)
client.connect()

# 按键控制
client.home()              # 返回主页
client.back()              # 返回
client.volume_up()         # 音量+
client.volume_down()       # 音量-

# 文本输入
client.inject_text("Hello World!")

# 触摸控制
client.tap(500, 1000)      # 点击屏幕
client.swipe(500, 1000, 500, 500, 300)  # 向上滑动
```

### 面板控制
```python
# 打开通知栏
client.expand_notification_panel()
time.sleep(2)
client.collapse_panels()

# 打开快捷设置
client.expand_settings_panel()
```

### 显示控制
```python
# 关闭屏幕
client.turn_screen_off()
time.sleep(2)
# 打开屏幕
client.turn_screen_on()
```

### 剪贴板
```python
# 设置剪贴板
client.set_clipboard("Copied text")

# 获取剪贴板
client.get_clipboard()
```

### 设备控制
```python
# 旋转屏幕
client.rotate_device()

# 启动应用
client.start_app("com.android.settings")

# 重置视频
client.reset_video()
```

## 控制消息类型对照表

| 类型ID | 名称 | 状态 | 便捷方法 |
|--------|------|------|----------|
| 0 | INJECT_KEYCODE | ✅ | home(), back(), volume_up(), 等 |
| 1 | INJECT_TEXT | ✅ | inject_text() |
| 2 | INJECT_TOUCH_EVENT | ✅ | tap(), swipe() |
| 3 | INJECT_SCROLL_EVENT | ✅ | inject_scroll_event() |
| 4 | BACK_OR_SCREEN_ON | ✅ | back_or_screen_on() |
| 5 | EXPAND_NOTIFICATION_PANEL | ✅ | expand_notification_panel() |
| 6 | EXPAND_SETTINGS_PANEL | ✅ | expand_settings_panel() |
| 7 | COLLAPSE_PANELS | ✅ | collapse_panels() |
| 8 | GET_CLIPBOARD | ✅ | get_clipboard() |
| 9 | SET_CLIPBOARD | ✅ | set_clipboard() |
| 10 | SET_DISPLAY_POWER | ✅ | set_display_power() |
| 11 | ROTATE_DEVICE | ✅ | rotate_device() |
| 12 | UHID_CREATE | ✅ | (底层已实现，高级USB HID功能) |
| 13 | UHID_INPUT | ✅ | (底层已实现，高级USB HID功能) |
| 14 | UHID_DESTROY | ✅ | (底层已实现，高级USB HID功能) |
| 15 | OPEN_HARD_KEYBOARD_SETTINGS | ✅ | open_hard_keyboard_settings() |
| 16 | START_APP | ✅ | start_app() |
| 17 | RESET_VIDEO | ✅ | reset_video() |

## 总结

**scrcpy-py-ddlx 现已实现官方 scrcpy 的所有 18 种控制消息类型！**

- ✅ 核心控制功能：按键、文本、触摸、滚动
- ✅ 面板控制：通知栏、设置栏
- ✅ 显示控制：开关屏幕
- ✅ 设备控制：旋转、启动应用
- ✅ 剪贴板：双向同步
- ✅ 底层协议：18/18 完全实现
