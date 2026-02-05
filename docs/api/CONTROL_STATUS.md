# scrcpy-py-ddlx 控制功能状态 (scrcpy-server v3.3.4)

## 测试日期
2026-01-19

## 测试设备
- 设备: realme RMX1931
- Android版本: 11
- scrcpy-server版本: 3.3.4

## 控制消息支持状态

### ✅ 完全支持的功能

| 功能 | 方法 | 状态 | 说明 |
|------|------|------|------|
| 按键控制 | `home()`, `back()`, `volume_up()` 等 | ✅ 有效 | 所有按键都工作 |
| 文本输入 | `inject_text()` | ✅ 有效 | 支持中文、特殊字符 |
| 触摸控制 | `tap()`, `swipe()` | ✅ 有效 | 点击、滑动 |
| 滚动控制 | `inject_scroll_event()` | ✅ 有效 | 滚动 |
| 返回或亮屏 | `back_or_screen_on()` | ✅ 有效 | |
| 展开通知面板 | `expand_notification_panel()` | ✅ 有效 | |
| 展开设置面板 | `expand_settings_panel()` | ✅ 有效 | |
| 折叠面板 | `collapse_panels()` | ✅ 有效 | |
| 设置剪贴板 | `set_clipboard()` | ✅ 有效 | |
| 获取剪贴板 | `get_clipboard()` | ✅ 有效 | |
| 显示电源控制 | `turn_screen_on()`, `turn_screen_off()` | ✅ **已验证** | ADB日志确认有效 |
| 重置视频 | `reset_video()` | ✅ 有效 | |

### ⚠️ 部分支持/服务器限制的功能

| 功能 | 方法 | 状态 | 说明 |
|------|------|------|------|
| 启动应用 | `start_app()` | ⚠️ **服务器不支持** | scrcpy 3.3.4 不支持 START_APP 消息，需要更新到 v2.4+ 实际上是添加于较新版本 |
| 设备旋转 | `rotate_device()` | ⚠️ **服务器不支持** | scrcpy 3.3.4 不支持 ROTATE_DEVICE 消息 |
| 键盘设置 | `open_hard_keyboard_settings()` | ⚠️ **服务器不支持** | |
| UHID功能 | UHID_CREATE/INPUT/DESTROY | ⚠️ 高级功能 | USB HID相关，需要特定场景 |

## 替代方案

### 启动应用的替代方法

由于scrcpy 3.3.4 不支持 START_APP 控制消息，可以使用以下方法：

```python
import subprocess

# 方法1: 使用 ADB monkey 命令（推荐，只使用包名）
subprocess.run(["adb", "shell", "monkey", "-p", "com.heytap.browser",
                "-c", "android.intent.category.LAUNCHER", "1"])

# 方法2: 使用 ADB am start 命令（需要完整Activity）
subprocess.run(["adb", "shell", "am", "start", "-n",
                "com.android.settings/.Settings"])
```

### 常用应用包名

| 应用 | 包名 |
|------|------|
| 设置 | com.android.settings |
| realme浏览器 | com.heytap.browser |
| 夸克浏览器 | com.quark.browser |
| Chrome | com.android.chrome |

## 测试验证

### 已验证有效

1. **显示电源控制** - ADB日志确认：
   ```
   D/SurfaceFlinger: Setting power mode 0 on display
   I/scrcpy: Device display turned off
   ```

2. **按键控制** - 所有按键测试通过

3. **触摸/滚动** - 正常工作

### 需要更新服务器的功能

- `start_app()` - 需要更新 scrcpy-server
- `rotate_device()` - 需要更新 scrcpy-server

## 建议

1. **当前可用的功能已经非常完整**，包括：
   - 完整的按键控制
   - 文本输入
   - 触摸和滚动
   - 面板控制
   - 剪贴板
   - 显示电源控制

2. **如需启动应用**，使用ADB命令作为替代

3. **scrcpy-server版本**：当前测试使用 v3.3.4，较新的版本可能支持更多控制消息
