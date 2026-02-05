# 故障排除

常见问题解决方案。

---

## 连接问题

### 未检测到设备

**症状**: `Unable to detect device`

**解决方案**:
1. 检查 USB 线（使用数据线，而非仅充电线）
2. 在设备上启用 USB 调试：
   - 设置 → 关于手机 → 连续点击"版本号"7次
   - 设置 → 开发者选项 → USB 调试
3. 使用 ADB 验证：
   ```bash
   adb devices
   ```
4. 重启 ADB 服务器：
   ```bash
   adb kill-server
   adb start-server
   ```

### 无线连接失败

**症状**: `Wireless connection timeout`

**解决方案**:
1. 确保设备和电脑在同一网络
2. 首次设置：使用 USB 线
3. 检查防火墙设置
4. 手动无线连接：
   ```bash
   adb tcpip 5555
   adb connect <设备-ip>:5555
   ```

---

## 视频问题

### 黑屏 / 无视频

**症状**: 窗口显示但为黑色

**解决方案**:
1. 检查编解码器支持：
   ```bash
   adb shell dumpsys media.codec | grep h264
   ```
2. 尝试不同的编解码器：
   ```python
   config = ClientConfig(codec_name="h264")  # 或 "h265", "av1"
   ```
3. 降低码率：
   ```python
   config = ClientConfig(bitrate=4000000)  # 4 Mbps
   ```

### 视频冻结

**症状**: 几秒后视频冻结

**解决方案**:
1. 降低最大帧率：
   ```python
   config = ClientConfig(max_fps=30)
   ```
2. 禁用音频：
   ```python
   config = ClientConfig(audio=False)
   ```

---

## 音频问题

### 无声音

**症状**: 没有声音播放

**解决方案**:
1. 检查音频后端：
   ```bash
   pip install sounddevice
   ```
2. 在配置中启用音频：
   ```python
   config = ClientConfig(audio=True)
   ```
3. 检查设备音频捕获支持：
   ```bash
   adb shell cmd media_support
   ```

### 音频失真

**症状**: 音频声音有问题

**解决方案**:
1. 尝试不同的音频配置：
   ```python
   config = ClientConfig(audio_codec="opus")
   ```

---

## 剪贴板问题

### 剪贴板不同步

**症状**: 复制/粘贴在设备间不工作

**解决方案**:
1. 启用剪贴板自动同步：
   ```python
   config = ClientConfig(clipboard_autosync=True)
   ```
2. 在设备上授予通知权限
3. 检查设备兼容性（Android 7+）

---

## Server 问题

### 找不到 Server

**症状**: `scrcpy-server not found`

**解决方案**:
1. 编译 server：
   ```bash
   cd scrcpy
   ./gradlew.bat server:assembleRelease
   cp server/build/outputs/apk/release/server-release-unsigned.apk ../scrcpy-server
   ```
2. 检查文件是否存在：
   ```bash
   ls -lh scrcpy-server
   ```

### Server 在设备上崩溃

**症状**: Server 立即退出

**解决方案**:
1. 检查设备 logcat：
   ```bash
   adb logcat | grep scrcpy
   ```
2. 以 Release 模式重新编译 server
3. 检查设备 Android 版本（API 21+）

---

## Python 问题

### 导入错误

**症状**: `ModuleNotFoundError: No module named 'scrcpy_py_ddlx'`

**解决方案**:
1. 以开发模式安装：
   ```bash
   pip install -e .
   ```
2. 或添加到 PYTHONPATH：
   ```bash
   export PYTHONPATH=/path/to/scrcpy-py-ddlx:$PYTHONPATH
   ```

### PyAV 错误

**症状**: `ImportError: av` 未找到

**解决方案**:
```bash
pip install av>=13.0.0
```

---

## 获取帮助

如果问题仍未解决：

1. 检查 `logs/` 目录中的日志
2. 启用详细日志：
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   ```
3. 运行调试输出：
   ```bash
   python tests_gui/test_direct.py
   ```
4. 查看 [GitHub Issues](https://github.com/AIddlx/scrcpy_py_ddlx/issues)
