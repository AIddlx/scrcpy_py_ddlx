# Options

> **文件**: `Options.java`
> **功能**: 服务端配置解析

---

## 概述

`Options` 类解析命令行参数，配置服务端行为。相比原始 scrcpy 有大量扩展。

---

## 原始 scrcpy 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `log_level` | String | 日志级别 |
| `max_size` | int | 最大分辨率 |
| `bit_rate` | int | 视频码率 |
| `max_fps` | int | 最大帧率 |
| `lock_video_orientation` | int | 锁定方向 |
| `tunnel_host` | String | Tunnel 主机 |
| `tunnel_port` | int | Tunnel 端口 |
| `send_device_meta` | boolean | 发送设备信息 |
| `send_dummy_byte` | boolean | 发送哑字节 |
| `cleanup` | boolean | 清理模式 |

---

## 新增参数 (本项目)

### 网络模式

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `control_port` | int | 27184 | TCP 控制端口 |
| `video_port` | int | 27185 | UDP 视频端口 |
| `audio_port` | int | 27186 | UDP 音频端口 |
| `discovery_port` | int | 27183 | UDP 发现端口 |

### FEC

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `fec_enabled` | boolean | false | 启用 FEC |
| `video_fec_enabled` | boolean | false | 视频 FEC |
| `audio_fec_enabled` | boolean | false | 音频 FEC |
| `fec_group_size` | int | 4 | FEC 组大小 K |
| `fec_parity_count` | int | 1 | FEC 校验数 M |
| `fec_mode` | String | frame | FEC 模式 |

### 认证

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `auth_key_file` | String | null | 认证密钥文件 |

### Stay-Alive 模式

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `stay_alive` | boolean | false | 客户端断开后服务端继续运行 |
| `max_connections` | int | -1 | 最大连接数 (-1=无限) |

> **注意**: `stay_alive` 控制服务端是否支持多客户端连接，与 `setsid` 无关。
> `setsid` 是客户端启动命令中的 shell 命令，用于让进程脱离 ADB 会话。

### 进程控制对比

| 功能 | setsid | stay_alive |
|------|--------|------------|
| **类型** | Shell 命令 | 服务端参数 |
| **设置位置** | 客户端启动脚本 | 服务端命令行 |
| **作用域** | 进程会话 (操作系统) | 连接生命周期 (应用层) |
| **网络模式** | **始终使用** | 可选 |
| **目的** | USB 拔插时进程存活 | 支持多客户端热连接 |

---

### 低延迟优化

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `low_latency` | boolean | false | 低延迟模式 |
| `encoder_priority` | int | 1 | 编码器优先级 |
| `encoder_buffer` | int | 0 | 编码器缓冲 |
| `skip_frames` | boolean | true | 跳帧模式 |
| `bitrate_mode` | String | vbr | 码率模式 |
| `i_frame_interval` | float | 10.0 | 关键帧间隔 |

---

## 解析方式

```java
public static Options parse(Arguments args) {
    Options options = new Options();

    // 原始参数
    options.setLogLevel(args.getValue("log_level", "info"));
    options.setMaxSize(args.getInt("max_size", 0));
    options.setBitRate(args.getInt("bit_rate", 8000000));

    // 新增参数
    options.setControlPort(args.getInt("control_port", 27184));
    options.setVideoPort(args.getInt("video_port", 27185));
    options.setFecEnabled(args.getBoolean("fec_enabled", false));
    options.setAuthKeyFile(args.getValue("auth_key_file", null));
    // ...
}
```

---

## 命令行示例

### 服务端参数 (Java)

```bash
app_process / com.genymobile.scrcpy.Server 3.3.4 \
    control_port=27184 \
    video_port=27185 \
    audio_port=27186 \
    video_codec=h265 \
    video_bit_rate=4000000 \
    max_fps=60 \
    fec_enabled=true \
    fec_group_size=4 \
    fec_parity_count=1 \
    auth_key_file=/data/local/tmp/scrcpy-auth.key \
    stay_alive=true
```

### 客户端启动命令 (Shell)

```bash
# 网络模式: 使用 setsid 让进程脱离 ADB 会话
adb shell CLASSPATH=/data/local/tmp/scrcpy-server.jar \
    setsid app_process / com.genymobile.scrcpy.Server 3.3.4 \
        control_port=27184 video_port=27185 audio_port=27186

# ADB 模式: 不使用 setsid (默认行为)
adb shell CLASSPATH=/data/local/tmp/scrcpy-server.jar \
    app_process / com.genymobile.scrcpy.Server 3.3.4
```

> **说明**: `setsid` 是 shell 命令前缀，不是 Java 参数。它由客户端在启动服务端时添加。

---

## 相关文档

- [Server.md](Server.md) - 服务端主类
