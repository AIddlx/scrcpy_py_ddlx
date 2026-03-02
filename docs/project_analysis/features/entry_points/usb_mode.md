# USB 模式入口 (test_direct.py)

> ADB Tunnel 模式，适合日常使用和调试

---

## 文件位置

```
tests_gui/test_direct.py
```

---

## 运行方式

```bash
cd C:\Project\IDEA\2\new\scrcpy-py-ddlx
python -X utf8 tests_gui/test_direct.py
```

---

## 功能特性

### 自动设备发现

1. **检测 USB 设备**: 自动列出已连接设备
2. **自动启用无线**: 检测到 USB 设备后自动执行 `adb tcpip 5555`
3. **局域网扫描**: 无 USB 时自动扫描局域网寻找 ADB 设备

### 音频录制

```python
# 在文件顶部修改配置
ENABLE_AUDIO_RECORDING = True   # 启用录制
AUDIO_FORMAT = 'opus'           # 格式: opus/mp3/wav
RECORDING_DURATION = 10         # 时长(秒)，None=无限
```

### 文件传输

- 拖放 APK 文件 → 自动安装
- 拖放其他文件 → 推送到设备

### 文件保存路径 (v1.5 规范)

- 截图: `~/Documents/scrcpy-py-ddlx/screenshots/`
- 录音/视频: `~/Documents/scrcpy-py-ddlx/recordings/`
- 下载文件: `~/Documents/scrcpy-py-ddlx/files/<原路径>`

---

## 命令行参数

无 (所有配置在文件内修改)

---

## 配置项

```python
# tests_gui/test_direct.py

# 音频录制配置
ENABLE_AUDIO_RECORDING = False
AUDIO_FORMAT = 'opus'
RECORDING_DURATION = 10

# 客户端配置
config = ClientConfig(
    device_serial=device_id,
    host="localhost",
    port=27183,
    show_window=True,
    audio=True,
    audio_dup=False,
    clipboard_autosync=True,
    bitrate=2500000,
    max_fps=30,
)
```

---

## 依赖检查

脚本启动时自动检查：
- numpy
- PySide6
- PyAV

---

## 工作流程

```
1. 检查依赖
2. 列出/发现设备
3. 创建 ClientConfig
4. 连接设备
5. 初始化文件推送器
6. (可选) 启动音频录制
7. 显示视频窗口
8. 运行 Qt 事件循环
9. 关闭时清理资源
```

---

## 日志

日志保存到当前目录：
```
scrcpy_test_YYYYMMDD_HHMMSS.log
```

---

## 相关文档

- [网络模式入口](network_mode.md)
- [客户端配置](../connection/README.md)
