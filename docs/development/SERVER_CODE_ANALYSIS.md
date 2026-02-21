# scrcpy 服务端代码结构分析

本文档详细分析 scrcpy 服务端的代码结构，为热连接功能实现提供设计基础。

---

## 1. Server.java 主流程

### 1.1 入口流程

```
main(String... args)
├── internalMain(args)
│   ├── prepareMainLooper() - 创建主 Looper
│   ├── Options.parse(args) - 解析命令行参数
│   ├── Ln.initLogLevel() - 初始化日志
│   ├── Workarounds.apply() - 应用设备兼容性修复
│   └── scrcpy(options) - 主逻辑
└── System.exit(status)  // 总是调用
```

### 1.2 scrcpy() 完整流程

```
scrcpy(Options options)
│
├── [1] 前置检查
│   ├── Camera mirroring 支持
│   └── New virtual display 支持
│
├── [2] CleanUp.start(options) - 清理进程
│
├── [3] DesktopConnection 创建
│   └── networkMode ? openNetwork() : open()
│
├── [4] 能力协商
│   ├── sendDeviceMeta()
│   ├── sendCapabilities()
│   └── receiveClientConfig()
│
├── [5] 创建 AsyncProcessor 组件
│   ├── Controller
│   ├── AudioEncoder/AudioRawRecorder
│   └── SurfaceEncoder
│
├── [6] 启动所有 AsyncProcessor
│   └── processor.start(listener)
│
├── [7] Looper.loop() - 阻塞
│   └── 等待 Completion.quitSafely()
│
└── [8] 清理 (finally)
    ├── cleanUp.interrupt()
    ├── processor.stop() × N
    ├── OpenGLRunner.quit()
    ├── connection.shutdown()
    ├── cleanUp.join()
    ├── processor.join() × N
    ├── OpenGLRunner.join()
    └── connection.close()
```

### 1.3 Completion 中断机制

```java
class Completion {
    int running;
    boolean fatalError;

    void addCompleted(boolean fatalError) {
        --running;
        if (fatalError) this.fatalError = true;
        if (running == 0 || this.fatalError) {
            Looper.getMainLooper().quitSafely();
        }
    }
}
```

**关键**：任一组件致命错误 → 退出整个服务端

---

## 2. DesktopConnection 连接管理

### 2.1 连接模式

| 模式 | 控制通道 | 视频通道 | 音频通道 |
|------|---------|---------|---------|
| ADB 隧道 | LocalSocket | LocalSocket | LocalSocket |
| 网络 TCP | TCP Socket | UDP | UDP |

### 2.2 openNetwork() 流程

```java
1. ServerSocket(controlPort)     // 创建 TCP 服务端
2. accept()                       // 等待连接
3. sendDummyByte()               // 发送 0x00
4. DatagramSocket(videoPort)     // 创建 UDP
5. DatagramSocket(audioPort)
6. UdpMediaSender 实例
```

### 2.3 shutdown() vs close()

| 方法 | 作用 | 调用时机 |
|------|------|---------|
| `shutdown()` | 停止数据传输 | finally 块开头 |
| `close()` | 释放资源 | 所有线程 join 后 |

---

## 3. AsyncProcessor 组件

### 3.1 接口定义

```java
interface AsyncProcessor {
    void start(TerminationListener listener);
    void stop();
    void join();
}
```

### 3.2 实现类

| 类 | 用途 | 线程数 | 停止方式 |
|----|------|--------|---------|
| Controller | 控制消息 | 2 | interrupt() |
| AudioEncoder | 音频编码 | 3 | end() + interrupt() |
| SurfaceEncoder | 视频编码 | 1 | stopped.set(true) |

---

## 4. CleanUp 机制

### 4.1 工作原理

```
主进程 ──stdin──> CleanUp 子进程
  │                    │
  │ (正常退出)          │
  │ interrupt() ─────> 退出
  │                    │
  │ (崩溃)             │
  │ pipe break ──────> 执行清理
  │                    - 恢复显示电源
  │                    - 关闭 show_touches
  │                    - 恢复 stay_awake
```

### 4.2 关键点

- CleanUp 是**独立子进程**
- 整个生命周期只需要**一个实例**
- 热连接循环中**不要重复创建**

---

## 5. UdpDiscoveryReceiver

### 5.1 监听机制

```java
while (running && !wakeRequested) {
    socket.receive(packet);  // 1秒超时
    // 处理 DISCOVER 或 WAKE
}
```

- **带超时阻塞**：每秒检查 running 标志
- **可中断**：设置 `running=false` 退出

### 5.2 与主循环配合

应在**独立线程**中运行，不影响主 Looper

---

## 6. 资源生命周期

### 6.1 创建顺序

```
Workarounds → CleanUp → Connection → 能力协商 → AsyncProcessors → Looper
```

### 6.2 销毁顺序

```
Looper.quit → CleanUp.interrupt → Processors.stop → Connection.shutdown
           → CleanUp.join → Processors.join → Connection.close
```

### 6.3 热连接循环中

| 资源 | 复用策略 |
|------|---------|
| Workarounds | 只应用一次 |
| CleanUp | 整个生命周期一个实例 |
| Options | 配置不变 |
| Connection | **每次重建** |
| AsyncProcessors | **每次重建** |
| Looper | **每次重新 prepare** |

---

## 7. 线程模型

| 线程 | 创建者 | 守护 |
|------|--------|------|
| main | 系统 | 否 |
| cleanup | CleanUp | 否 |
| control-recv | Controller | 否 |
| video | SurfaceEncoder | 否 |
| audio-* | AudioEncoder | 否 |

---

## 8. 错误传播

```
AsyncProcessor 异常
    ↓
listener.onTerminated(fatalError=true)
    ↓
Completion.addCompleted(fatalError=true)
    ↓
Looper.quitSafely()
    ↓
finally 清理
    ↓
System.exit(1)
```

---

## 9. 热连接实现要点

### 9.1 需要修改的文件

| 文件 | 修改内容 |
|------|---------|
| `Options.java` | 添加 `stayAlive` 参数 |
| `Server.java` | 添加主循环逻辑 |
| `UdpDiscoveryReceiver.java` | 添加 `reset()` 方法 |

### 9.2 循环结构建议

```java
public static void main(String... args) {
    Options options = Options.parse(args);
    Workarounds.apply();        // 只一次
    CleanUp cleanUp = CleanUp.start(options);  // 只一次

    while (options.isStayAlive()) {
        // 1. UDP 等待唤醒
        UdpDiscoveryReceiver discovery = ...;
        waitForWake(discovery);

        // 2. 每次重建
        prepareMainLooper();    // 重新准备
        DesktopConnection conn = openNetwork(...);

        try {
            scrcpyInner(options, conn, cleanUp);
        } finally {
            conn.close();
        }
    }
}
```

### 9.3 注意事项

1. **CleanUp 不要重复创建**
2. **Looper 每次重新 prepare**
3. **AsyncProcessor 每次重建**
4. **Workarounds 只应用一次**

---

*文档创建: 2026-02-16*
*用途: 热连接功能设计基础*
