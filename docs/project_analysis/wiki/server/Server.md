# Server.java - 服务入口

> **路径**: `scrcpy/server/src/main/java/com/genymobile/scrcpy/Server.java`
> **职责**: 服务端主入口，管理会话生命周期

---

## 类定义

### Server (final class)

**职责**: 主服务类，不可实例化

**类型**: final class，所有方法为 static

### Completion (inner class)

**职责**: 异步处理器完成状态跟踪

---

## 主要方法

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `main` | args... | void | 程序入口 |
| `internalMain` | args... | void | 内部主逻辑 |
| `scrcpy` | Options | void | 运行单次会话 |
| `runStayAliveMode` | Options | void | 热连接模式 |
| `scrcpySession` | Options, Connection, CleanUp | void | 执行单次会话 |
| `createConnection` | Options | DesktopConnection | 创建连接 |
| `prepareMainLooper` | - | void | 准备 Looper |
| `resetMainLooper` | - | void | 重置 Looper |

---

## 会话流程

### 传统模式 (scrcpy)

```
main()
  └── internalMain()
        └── scrcpy(options)
              ├── createConnection()
              ├── scrcpySession()
              │     ├── 发送设备名称
              │     ├── 发送能力信息
              │     ├── 接收客户端配置
              │     ├── 启动 Controller
              │     ├── 启动 SurfaceEncoder
              │     ├── 启动 AudioEncoder
              │     └── 等待结束
              └── close()
```

### 热连接模式 (stay-alive)

```
main()
  └── internalMain()
        └── runStayAliveMode(options)
              ├── 启动 UdpDiscoveryReceiver
              ├── 循环等待唤醒
              └── 每次唤醒执行 scrcpySession()
```

---

## 进程控制: setsid vs stay_alive

> **v1.5 重要更新**: 网络模式下 `setsid` 和 `stay_alive` 是两个独立的功能

### setsid - 进程会话控制

**作用**: 让服务端进程独立于 ADB 会话运行

| 特性 | 说明 |
|------|------|
| **启用方式** | 客户端启动命令中添加 `setsid` 前缀 |
| **使用场景** | **网络模式始终启用** |
| **目的** | USB 拔插不会导致服务终止 |
| **原理** | 创建新的会话 ID，进程脱离父进程 (adb) |

**客户端启动示例**:
```bash
# 网络模式: setsid 是必需的
adb shell CLASSPATH=/data/local/tmp/scrcpy-server.jar \
    setsid app_process / com.genymobile.scrcpy.Server ...

# ADB 模式: 不使用 setsid (默认行为)
adb shell CLASSPATH=/data/local/tmp/scrcpy-server.jar \
    app_process / com.genymobile.scrcpy.Server ...
```

### stay_alive - 多客户端连接控制

**作用**: 服务端是否在客户端断开后继续运行

| 特性 | 说明 |
|------|------|
| **启用方式** | 服务端参数 `stay_alive=true` |
| **使用场景** | 需要多客户端连接 (hot-connect) |
| **目的** | 支持热连接，无需重启服务端 |
| **机制** | 客户端断开后不退出，等待新连接 |

**服务端参数示例**:
```bash
# 单客户端模式 (默认)
app_process / com.genymobile.scrcpy.Server 3.3.4 ...

# 多客户端模式
app_process / com.genymobile.scrcpy.Server 3.3.4 stay_alive=true max_connections=5 ...
```

### 关系对比

| 特性 | setsid | stay_alive |
|------|--------|------------|
| **控制层级** | 进程会话 (操作系统) | 连接生命周期 (应用层) |
| **设置位置** | 客户端启动命令 | 服务端启动参数 |
| **网络模式** | **始终启用** | 可选 |
| **USB 拔插** | 进程存活 | 依赖 setsid |
| **多连接** | 无关 | **必须启用** |

### 配置矩阵

| 模式 | setsid | stay_alive | 说明 |
|------|--------|------------|------|
| ADB 单客户端 | 否 | 否 | 默认 ADB 模式 |
| 网络单客户端 | **是** | 否 | 网络模式默认 |
| 网络多客户端 | **是** | **是** | Hot-Connect 支持 |

---

---

## 依赖关系

```
Server
    │
    ├──→ Options (配置解析)
    │
    ├──→ DesktopConnection (连接管理)
    │       ├── ControlChannel
    │       ├── UdpMediaSender
    │       └── CapabilityNegotiation
    │
    ├──→ Controller (控制处理)
    │
    ├──→ SurfaceEncoder (视频编码)
    │
    ├──→ AudioEncoder (音频编码)
    │
    └──→ UdpDiscoveryReceiver (唤醒接收)
```

---

## 常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `SERVER_PATH` | - | 服务端 JAR 路径 |

---

*此文档基于服务端代码分析生成*
