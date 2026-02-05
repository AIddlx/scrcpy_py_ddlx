# Direct Test 模式

使用 `test_direct.py` 快速测试和体验。

---

## 什么是 test_direct.py？

`test_direct.py` 是一个完整的测试脚本，提供：
- 自动设备检测（USB/无线）
- 视频窗口显示
- 音频流播放
- 剪贴板自动同步
- 无需安装包，直接运行

---

## 快速开始

```bash
python tests_gui/test_direct.py
```

脚本会自动：
1. 检测设备
2. 如果是 USB，自动启用无线模式
3. 连接并显示视频窗口
4. 启动音频和剪贴板同步

---

## 功能

### 自动设备发现

- USB 设备自动检测
- 自动启用无线 ADB
- 局域网设备扫描
- 一键无线连接

### 视频与音频

- 实时视频显示（60fps）
- 音频流播放
- 窗口可交互（点击、输入）

### 剪贴板同步

- 自动同步剪贴板（PC ↔ 设备）
- 复制文本自动传输

---

## 配置

编辑 `test_direct.py` 自定义：

```python
# 音频录制（默认：禁用）
ENABLE_AUDIO_RECORDING = False

# 录制格式: 'opus', 'mp3', 'wav'
AUDIO_FORMAT = 'opus'

# 录制时长（秒）
RECORDING_DURATION = 10
```

---

## 使用示例

### 基本测试

```bash
python tests_gui/test_direct.py
```

### 带音频录制

编辑脚本：
```python
ENABLE_AUDIO_RECORDING = True
RECORDING_DURATION = 10
```

然后运行：
```bash
python tests_gui/test_direct.py
```

---

## 故障排除

### 未检测到设备

1. 检查 USB 连接
2. 在设备上启用 USB 调试
3. 运行 `adb devices` 验证

### 无线连接失败

1. 确保设备和电脑在同一网络
2. 首次使用：使用 USB 线
3. 脚本会自动启用无线模式

### 音频不工作

检查音频后端安装：
```bash
pip install sounddevice
```

---

## 日志

日志保存到：
```
scrcpy_test_YYYYMMDD_HHMMSS.log
```

包含详细的调试信息。

---

## 相关文档

- [Python API 模式](python-api.md) - 编程方式使用
- [安装指南](../installation.md) - 完整安装指南
