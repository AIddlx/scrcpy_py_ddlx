# scrcpy-py-ddlx 改进设计

> 日期：2026-02-16

## 1. scrcpy-companion（手机端伴侣应用）

### 设计目标
在手机上提供一个简单的 UI，用于：
- 显示当前连接状态
- 快速启动/停止服务端
- 显示连接的客户端列表
- 配置常用参数

### 现有资源
- 代码位置：`scrcpy/companion/`
- Android 应用，使用 Android 原生 UI

### 编译方法

**前置条件：**
- Android SDK 已安装
- `ANDROID_HOME` 环境变量指向 SDK 目录（默认 `%LOCALAPPDATA%\Android\Sdk`）
- build-tools 34.0.0 已安装
- Java JDK 11+ 已安装

**编译命令：**

Windows:
```cmd
cd scrcpy\companion
build.cmd
```

Linux/Mac:
```bash
cd scrcpy/companion
chmod +x build.sh
./build.sh
```

编译成功后输出：`scrcpy-companion.apk`

**安装到设备：**
```cmd
adb install scrcpy-companion.apk
```

如果遇到权限问题：
1. 在手机上开启"允许安装未知来源应用"
2. 安装时在手机屏幕上点击"允许"

### 编译故障排除

#### 问题 1: javac 不支持 `*.java` 通配符

**错误信息：**
```
java.nio.file.InvalidPathException: Illegal char <*> at index 36
```

**原因：** 较新版本的 javac 不支持命令行通配符

**解决方案：** 在 build.cmd 中明确列出所有 Java 文件：
```cmd
javac ... MainActivity.java ScrcpyTileService.java UdpClient.java R.java
```

#### 问题 2: 内部类未打包导致 NoClassDefFoundError

**错误信息：**
```
java.lang.NoClassDefFoundError: Failed resolution of: Lcom/genymobile/scrcpy/companion/MainActivity$1;
Caused by: java.lang.ClassNotFoundException: com.genymobile.scrcpy.companion.MainActivity$1
```

**原因：** d8 命令未包含内部类（如 `MainActivity$1.class`）

**解决方案：** 先用 jar 打包所有 class 文件，再用 d8 处理 jar：
```cmd
REM Create jar from all class files (including inner classes)
cd classes
jar cf classes.jar com
cd ..

REM Use jar as input to d8
d8 --output dex --lib android.jar classes.jar
```

#### 问题 3: APK 安装失败 "Package code is missing"

**错误信息：**
```
INSTALL_FAILED_INVALID_APK: Package code is missing
```

**原因：** APK 中缺少 classes.dex 文件

**解决方案：** 确保编译和 d8 步骤都成功，检查 build 目录中是否有 classes.dex

### 使用方法

1. 安装后在手机上打开 **"Scrcpy Companion"** 应用
2. 应用会自动检测服务端状态
3. 功能：
   - **刷新**：重新检测服务端状态
   - **终止服务器**：通过 UDP 发送终止命令
   - **日志查看**：显示最近的服务端日志
   - **快捷设置磁贴**：下拉通知栏可添加 Scrcpy 磁贴快速启动/停止

### 功能列表
- [x] 显示服务端状态（运行中/已停止）
- [x] 显示连接的客户端数量
- [ ] 快速设置（码率、帧率、编码器）
- [ ] 一键启动/停止
- [ ] 通知栏常驻图标
- [ ] 开机自启动选项

### 与电脑端交互
```
┌──────────────┐                    ┌──────────────┐
│   手机端      │  ←── UDP/TCP ──→  │   电脑端      │
│  Companion   │                    │   Client     │
├──────────────┤                    ├──────────────┤
│ - 状态显示    │                    │ - 视频显示    │
│ - 配置管理    │                    │ - 控制输入    │
│ - 服务控制    │                    │ - MCP 服务   │
└──────────────┘                    └──────────────┘
```

---

## 2. 热链接（Stay-Alive 模式改进）

### 当前问题
- 服务端启动后需要客户端主动连接
- 断开后服务端可能继续运行（资源浪费）
- 无法快速重连

### 设计方案

#### 2.1 服务端状态机
```
┌─────────┐  启动   ┌─────────┐  客户端连接  ┌─────────┐
│  IDLE   │ ──────→ │ WAITING │ ──────────→ │ACTIVE   │
└─────────┘         └─────────┘             └─────────┘
     ↑                  │                        │
     │                  │ 超时                   │ 断开
     │                  ↓                        ↓
     │             ┌─────────┐             ┌─────────┐
     └──────────── │ TIMEOUT │ ←────────── │DISCONN  │
                   └─────────┘             └─────────┘
```

#### 2.2 UDP 唤醒机制
```python
# 客户端发送唤醒包
WAKE_REQUEST = b"WAKE_UP"
WAKE_RESPONSE = b"WAKE_ACK"

# 流程：
# 1. 客户端发送 WAKE_UP 到设备 UDP 端口
# 2. 服务端收到后唤醒，回复 WAKE_ACK
# 3. 客户端开始 TCP/UDP 连接
```

#### 2.3 心跳检测
```
客户端 ──→ PING ──→ 服务端
客户端 ←── PONG ←── 服务端

间隔：5 秒
超时：15 秒无响应认为断开
```

### 已实现
- [x] UDP 唤醒 (`udp_wake.py`)
- [x] 服务端 stay-alive 模式
- [ ] 心跳检测
- [ ] 自动重连

---

## 3. 灵活的服务器停止

### 当前问题
- 停止服务器需要 ADB 命令
- 无法从客户端远程停止
- 无法优雅关闭（可能丢数据）

### 设计方案

#### 3.1 远程停止命令
```python
# 通过控制通道发送停止命令
CONTROL_MSG_SERVER_STOP = 0xFF

# 服务端收到后：
# 1. 停止接受新连接
# 2. 等待当前帧发送完成
# 3. 关闭所有 socket
# 4. 退出
```

#### 3.2 UDP 停止命令
```python
# 用于紧急停止（无连接时）
STOP_REQUEST = b"SCRCPY_STOP"
STOP_RESPONSE = b"SCRCPY_STOP_ACK"

# 流程：
# 1. 客户端发送 SCRCPY_STOP 到 UDP 端口
# 2. 服务端验证来源（可选）
# 3. 服务端停止并回复
```

#### 3.3 超时自动停止
```java
// 服务端配置
stay_alive=true
idle_timeout=300000  // 5 分钟无连接自动停止
max_connections=-1   // 无限制
```

### 实现状态
- [x] ADB 命令停止 (`adb shell pkill -f app_process`)
- [ ] 控制通道停止命令
- [ ] UDP 停止命令
- [ ] 超时自动停止

---

## 4. 优先级排序

| 优先级 | 功能 | 复杂度 | 价值 |
|--------|------|--------|------|
| P0 | UDP 停止命令 | 低 | 高 |
| P0 | 超时自动停止 | 低 | 高 |
| P1 | 心跳检测 | 中 | 高 |
| P1 | 自动重连 | 中 | 高 |
| P2 | Companion 完善 | 中 | 中 |
| P3 | 控制通道停止 | 中 | 中 |

---

## 5. 下一步计划

### 短期（本周）
1. 实现 UDP 停止命令
2. 添加服务端 idle_timeout 配置
3. 实现心跳检测

### 中期
1. 完善自动重连逻辑
2. 改进 Companion 应用
3. 添加更多配置选项

### 长期
1. 重新设计 GUI（或改用 Web UI）
2. 完善文档
3. 添加更多测试

---

*此文档记录当前改进方向和待办事项。*
