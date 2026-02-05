# 快速开始

5 分钟快速上手 scrcpy-py-ddlx。

---

## 前置要求

- Python 3.8+
- 已启用 USB 调试的 Android 设备
- 已安装 ADB

---

## 步骤 1: 编译 Server（首次使用）

### 为什么需要编译？

本项目使用了修改过的 scrcpy server，包含额外功能（应用列表获取等）。

### 前置要求

- JDK 17+
- Android SDK

### 编译步骤

```bash
# Windows
cd scrcpy
.\gradlew.bat server:assembleRelease

# Linux/macOS
cd scrcpy
./gradlew server:assembleRelease
```

### 复制到项目根目录

```bash
# Windows
copy server\build\outputs\apk\release\server-release-unsigned.apk ..\scrcpy-server

# Linux/macOS
cp server/build/outputs/apk/release/server-release-unsigned.apk ../scrcpy-server
```

### ⚠️ 重要

1. **必须使用 Release 版本** - Debug 版本可能不包含修改
2. **文件名不要带 .jar 扩展名** - `scrcpy-server` 而非 `scrcpy-server.jar`
3. **文件大小约 90KB** - 如果太小说明编译有问题

### 验证编译结果

```bash
# 检查文件大小
ls -lh scrcpy-server

# 验证包含修改（Windows Git Bash）
strings scrcpy-server | grep -iE "GET_APP_LIST|getAppList"
# 应该能看到相关字符串
```

### 详细说明

详见 [完整编译指南](../development/build.md)

---

## 步骤 2: 安装 Python 依赖

```bash
pip install -r requirements.txt
```

或手动安装：

```bash
pip install av>=13.0.0 numpy>=1.24.0 PySide6
```

---

## 步骤 3: 运行测试脚本

最简单的验证方式：

```bash
python tests_gui/test_direct.py
```

脚本会自动：
1. 检测设备（USB 或无线）
2. 自动推送 server 到设备（无需手动操作）
3. 显示视频窗口
4. 启用音频流和剪贴板同步

---

## 如何 Server 部署到设备？

### 自动部署（推荐）

Python 客户端会**自动推送** server 到设备：

```python
from scrcpy_py_ddlx import ScrcpyClient, ClientConfig

config = ClientConfig(server_jar="scrcpy-server")
client = ScrcpyClient(config)
client.connect()  # 自动推送 scrcpy-server 到设备
```

**推送位置**: `/data/local/tmp/scrcpy-server`（无 .jar 扩展名）

### 手动部署（一般不需要）

```bash
# 推送文件
adb push scrcpy-server /data/local/tmp/scrcpy-server

# 设置权限
adb shell chmod 755 /data/local/tmp/scrcpy-server
```

---

## 步骤 4: 作为 Python 库使用

```python
import sys
sys.path.insert(0, '/path/to/scrcpy-py-ddlx')

from scrcpy_py_ddlx import ScrcpyClient, ClientConfig

config = ClientConfig(
    server_jar="scrcpy-server",
    show_window=True,
    audio=True,
)

client = ScrcpyClient(config)
client.connect()

# 控制设备
client.tap(500, 1000)
client.home()
client.text("Hello World")

# 获取应用列表（修改版 server 特有功能）
apps = client.list_apps()
for app in apps:
    print(f"{app['name']}: {app['package']}")

client.disconnect()
```

---

## 故障排除

### 编译问题

**问题**: 编译后功能不正常

**解决**:
```bash
cd scrcpy
./gradlew.bat clean
./gradlew.bat server:assembleRelease
```

**验证**: 使用 `strings` 命令检查 APK 是否包含修改

### 连接问题

**问题**: 找不到设备

**解决**:
```bash
adb devices
adb kill-server
adb start-server
```

### Server 问题

详见 [故障排除](troubleshooting.md) 和 [完整编译指南](../development/build.md)

---

## 下一步

- [安装指南](installation.md) - 完整安装说明
- [使用模式](modes/) - 不同的使用方式
- [API 文档](../api/) - 完整 API 参考
