# ServerCore (服务端)

> **目录**: 根目录
> **文件**: 7 个 Java 文件
> **功能**: 服务端核心入口和配置

---

## 文件清单

| 文件 | 职责 |
|------|------|
| `Server.java` | 服务端入口 |
| `Options.java` | 命令行参数解析 |
| `AsyncProcessor.java` | 异步处理器接口 |
| `CleanUp.java` | 清理管理 |
| `Workarounds.java` | 设备兼容性修复 |
| `FakeContext.java` | 伪上下文 |
| `AndroidVersions.java` | Android API 版本常量 |

---

## Server.java

### 运行模式

| 模式 | 说明 |
|------|------|
| 传统模式 | 单次连接，ADB 启动 |
| Stay-Alive 模式 | 持久运行，UDP 唤醒 |

---

## 进程控制: setsid vs stay_alive

> **v1.5 更新**: 网络模式下 `setsid` 和 `stay_alive` 是两个独立的功能

### setsid - 进程会话独立化

**作用**: 让服务端进程脱离 ADB 会话

| 特性 | 说明 |
|------|------|
| **设置位置** | 客户端启动命令 (shell) |
| **网络模式** | **始终使用** |
| **目的** | USB 断开时进程不受影响 |

```bash
# 客户端启动命令
adb shell CLASSPATH=/data/local/tmp/scrcpy-server.jar \
    setsid app_process / com.genymobile.scrcpy.Server ...
    # ^^^^^^ 创建新会话
```

### stay_alive - 多客户端支持

**作用**: 控制服务端是否支持多连接

| 特性 | 说明 |
|------|------|
| **设置位置** | 服务端启动参数 |
| **默认值** | false (单连接) |
| **目的** | 热连接，无需重启 |

```bash
# 服务端参数
stay_alive=true         # 启用多连接
max_connections=5       # 最大连接数 (-1=无限)
```

### 关系总结

```
┌─────────────────────────────────────────────────────────────┐
│                      进程会话控制                             │
│                                                             │
│    setsid (客户端启动命令) ──→ 进程脱离 ADB 会话             │
│                                         ↓                   │
│                                   USB 拔插不终止              │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                    连接生命周期控制                           │
│                                                             │
│    stay_alive (服务端参数) ──→ 客户端断开后行为              │
│                                         ↓                   │
│                                   true: 等待新连接           │
│                                   false: 退出服务           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**注意**: 两者是正交的，setsid 控制进程会话，stay_alive 控制连接模式。

---

### 启动流程

```
main() → internalMain() → Options.parse()
                            ↓
                    ┌───────┴───────┐
                    ↓               ↓
            scrcpy()         runStayAliveMode()
            (传统)              (Stay-Alive)
                    ↓               ↓
            scrcpySession()  scrcpySession() [循环]
```

### 核心方法

```java
public final class Server {
    // 单次会话
    private static void scrcpy(Options options)

    // Stay-Alive 模式循环
    private static void runStayAliveMode(Options options)

    // 会话处理
    private static void scrcpySession(Options options,
                                      DesktopConnection connection,
                                      CleanUp cleanUp)

    // 创建连接
    private static DesktopConnection createConnection(Options options)
}
```

### 组件初始化顺序

```java
// 1. 设备元数据
connection.sendDeviceMeta(Device.getDeviceName());

// 2. 能力协商 (网络模式)
connection.sendCapabilities(width, height);
CapabilityNegotiation.ClientConfig config = connection.receiveClientConfig();
options.applyClientConfig(config);

// 3. 控制器
if (control) {
    controller = new Controller(controlChannel, cleanUp, options);
}

// 4. 音频
if (audio) {
    AudioCapture capture = ...;
    Streamer streamer = networkMode ? new Streamer(udpSender) : new Streamer(fd);
    AudioEncoder encoder = new AudioEncoder(capture, streamer, options);
}

// 5. 视频
if (video) {
    SurfaceCapture capture = new ScreenCapture(...);
    Streamer streamer = networkMode ? new Streamer(udpSender) : new Streamer(fd);
    SurfaceEncoder encoder = new SurfaceEncoder(capture, streamer, options);
}

// 6. 截图支持 (video=false)
else if (networkMode && control) {
    ScreenshotCapture screenshotCapture = new ScreenshotCapture(options);
    controller.setScreenshotCapture(screenshotCapture);
}
```

---

## Options.java

### 核心参数分类

#### 基础参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `video` | boolean | true | 启用视频 |
| `audio` | boolean | true | 启用音频 |
| `control` | boolean | true | 启用控制 |
| `max_size` | int | 0 | 最大尺寸 (0=自动) |

#### 编解码器

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `video_codec` | VideoCodec | H264 | h264/h265/av1 |
| `audio_codec` | AudioCodec | OPUS | opus/aac/flac/raw |
| `video_bit_rate` | int | 8000000 | 视频码率 (bps) |
| `audio_bit_rate` | int | 128000 | 音频码率 (bps) |
| `bitrate_mode` | String | "vbr" | cbr/vbr |
| `i_frame_interval` | float | 10 | 关键帧间隔 (秒) |

#### 网络模式

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `control_port` | int | 0 | TCP 控制端口 (0=ADB) |
| `video_port` | int | 27185 | UDP 视频端口 |
| `audio_port` | int | 27186 | UDP 音频端口 |
| `file_port` | int | 27187 | TCP 文件端口 |
| `discovery_port` | int | 27183 | UDP 发现端口 |

#### FEC 配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `fec_enabled` | boolean | false | 启用 FEC |
| `video_fec_enabled` | boolean | false | 视频流 FEC |
| `audio_fec_enabled` | boolean | false | 音频流 FEC |
| `fec_group_size` | int | 4 | K: 数据包数 |
| `fec_parity_count` | int | 1 | M: 校验包数 |
| `fec_mode` | String | "frame" | frame/fragment |

#### Stay-Alive 模式

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `stay_alive` | boolean | false | 持久运行 |
| `max_connections` | int | -1 | 最大连接数 (-1=无限) |

#### 低延迟优化

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `low_latency` | boolean | false | MediaCodec 低延迟 |
| `encoder_priority` | int | 1 | 线程优先级 |
| `encoder_buffer` | int | 0 | 缓冲模式 |
| `skip_frames` | boolean | true | 跳帧模式 |

#### 认证 (v1.4)

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `auth_key_file` | String | null | 认证密钥文件路径 |

### 参数解析

```java
public static Options parse(String... args) {
    // args[0] = 客户端版本
    // args[1..n] = key=value 参数

    for (int i = 1; i < args.length; ++i) {
        String arg = args[i];
        int equalIndex = arg.indexOf('=');
        String key = arg.substring(0, equalIndex);
        String value = arg.substring(equalIndex + 1);

        switch (key) {
            case "video_bit_rate":
                options.videoBitRate = Integer.parseInt(value);
                break;
            case "control_port":
                options.controlPort = Integer.parseInt(value);
                break;
            // ...
        }
    }
}
```

### 能力协商应用

```java
public void applyClientConfig(CapabilityNegotiation.ClientConfig config) {
    this.videoCodec = config.getVideoCodec();
    this.audioCodec = config.getAudioCodec();
    this.videoBitRate = config.videoBitrate;
    this.audioBitRate = config.audioBitrate;
    this.maxFps = config.maxFps;
    this.iFrameInterval = config.iFrameInterval;
    this.video = config.isVideoEnabled();
    this.audio = config.isAudioEnabled();
    this.videoFecEnabled = config.isVideoFecEnabled();
    this.audioFecEnabled = config.isAudioFecEnabled();
}
```

---

## AsyncProcessor

异步处理器接口。

```java
public interface AsyncProcessor {
    void start(CompletionCallback callback);
    void stop();
    void join() throws InterruptedException;
}
```

---

## CleanUp

会话清理管理。

```java
public class CleanUp extends Thread {
    // 启动清理线程
    public static CleanUp start(Options options)

    // 恢复设备设置
    @Override
    public void run()

    // 关闭电源
    public void setPowerOff(boolean powerOff)
}
```

---

## Workarounds

设备兼容性修复。

```java
public final class Workarounds {
    // 应用所有修复
    public static void apply()

    // 填充 AudioRecord 缓冲区
    public static void fillAudioRecordBuffer()

    // 设置连接保持
    public static void setKeepAlive(Socket socket)
}
```

---

## AndroidVersions

Android API 版本常量。

```java
public final class AndroidVersions {
    public static final int API_29_ANDROID_10 = 29;
    public static final int API_30_ANDROID_11 = 30;
    public static final int API_31_ANDROID_12 = 31;
    // ...
}
```

---

## 相关文档

- [DesktopConnection.md](DesktopConnection.md) - 连接管理
- [Streamer.md](Streamer.md) - 流分发
- [UdpDiscoveryReceiver.md](UdpDiscoveryReceiver.md) - UDP 发现
- [AuthHandler.md](AuthHandler.md) - 认证处理
