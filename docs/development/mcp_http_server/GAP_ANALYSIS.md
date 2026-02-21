# HTTP MCP Server 差距分析

> 本文档记录 `scrcpy_http_mcp_server.py` 当前实现与理想设计的差距，
> 作为后续改进的指导。
>
> **最后更新**: 2026-02-17

---

## 修复状态总览

| 类别 | 状态 | 说明 |
|------|------|------|
| 安全警告 | ✅ 已修复 | 工具描述已添加安全警告 |
| 网络模式参数 | ✅ 已修复 | push_server 和 connect 已补充参数 |
| 流程引导 | ✅ 已修复 | 工具描述已更新 |

---

## 1. 安全性差距 ✅ 已修复

### 1.1 已修复的问题

| 问题 | 修复内容 |
|------|---------|
| `enable_wireless` 无安全警告 | ✅ 添加 "⚠️ NOT RECOMMENDED" 警告 |
| `connect_wireless` 无安全警告 | ✅ 添加安全警告和网络模式替代方案 |
| `tcpip` 参数 | ✅ 标记为 "[DEPRECATED - Insecure]" |
| `discover_devices` 提到 5555 | ✅ 更新描述，不再强调 5555 |

### 1.2 理想设计

```
┌─────────────────────────────────────────────────────────────────┐
│                      安全设计原则                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. ADB USB 是首选，工具描述应明确说明                          │
│                                                                  │
│  2. ADB WiFi (5555) 相关工具应:                                 │
│     • 添加安全警告                                              │
│     • 标记为 "不推荐" 或 "仅限可信网络"                         │
│     • 考虑移除或隐藏                                            │
│                                                                  │
│  3. 网络模式应说明:                                             │
│     • 仅限可信局域网                                            │
│     • 服务端部署应通过 USB (安全)                               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 建议修改

#### enable_wireless 工具

```python
{
    "name": "enable_wireless",
    "description": "⚠️ NOT RECOMMENDED - Enable ADB wireless debugging (insecure, plaintext). Use USB or network mode instead. Only use in trusted private networks.",
    # ... 或考虑移除此工具
}
```

#### connect_wireless 工具

```python
{
    "name": "connect_wireless",
    "description": "⚠️ NOT RECOMMENDED - Connect via ADB WiFi (insecure). For wireless, use network mode: push_server(stay_alive=True) first, then connect(connection_mode='network').",
    # ... 或考虑移除此工具
}
```

#### connect 工具

```python
# 移除 tcpip 参数，或添加警告
"tcpip": {
    "type": "boolean",
    "default": False,
    "description": "⚠️ DEPRECATED - Use network mode instead. ADB WiFi is insecure."
}
```

---

## 2. 网络模式参数差距 ✅ 已修复

### 2.1 push_server 参数 ✅ 已修复

| 参数 | test_network_direct.py | 状态 |
|------|------------------------|------|
| `video_codec` | ✅ | ✅ 已添加 |
| `video_bitrate` | ✅ | ✅ 已添加 |
| `max_fps` | ✅ | ✅ 已添加 |
| `bitrate_mode` | ✅ (cbr/vbr) | ✅ 已添加 |
| `i_frame_interval` | ✅ | ✅ 已添加 |
| `audio` | ✅ | ✅ 已添加 |
| `fec_enabled` | ✅ | ✅ 已添加 |
| `video_fec_enabled` | ✅ | ✅ 已添加 |
| `audio_fec_enabled` | ✅ | ✅ 已添加 |
| `fec_group_size` | ✅ (K) | ✅ 已添加 |
| `fec_parity_count` | ✅ (M) | ✅ 已添加 |

### 2.2 connect 参数 ✅ 已修复

| 参数 | test_network_direct.py | 状态 |
|------|------------------------|------|
| `bitrate_mode` | ✅ | ✅ 已添加 |
| `i_frame_interval` | ✅ | ✅ 已添加 |
| `fec_enabled` | ✅ | ✅ 已添加 |
| `video_fec_enabled` | ✅ | ✅ 已添加 |
| `audio_fec_enabled` | ✅ | ✅ 已添加 |
| `fec_group_size` | ✅ | ✅ 已添加 |
| `fec_parity_count` | ✅ | ✅ 已添加 |
| `wake_server` | ✅ | ✅ 已添加 |

### 2.3 建议修改

#### push_server 工具完整参数

```python
{
    "name": "push_server",
    "description": "Deploy scrcpy server for network mode. REQUIRES USB connection (secure). After deployment, USB can be unplugged.",
    "inputSchema": {
        "type": "object",
        "properties": {
            # 基础参数
            "device_id": {"type": "string", "description": "Device serial (USB device)"},
            "server_path": {"type": "string", "default": "./scrcpy-server"},
            "push": {"type": "boolean", "default": True},
            "start": {"type": "boolean", "default": True},
            "reuse": {"type": "boolean", "default": False},
            "stay_alive": {"type": "boolean", "default": True},

            # 端口配置
            "control_port": {"type": "integer", "default": 27184},
            "video_port": {"type": "integer", "default": 27185},
            "audio_port": {"type": "integer", "default": 27186},

            # 视频参数 (新增)
            "video_codec": {
                "type": "string",
                "enum": ["auto", "h264", "h265", "av1"],
                "default": "auto"
            },
            "video_bitrate": {"type": "integer", "default": 4000000},
            "max_fps": {"type": "integer", "default": 60},
            "bitrate_mode": {
                "type": "string",
                "enum": ["cbr", "vbr"],
                "default": "vbr"
            },
            "i_frame_interval": {"type": "number", "default": 10.0},

            # 音频参数 (新增)
            "audio": {"type": "boolean", "default": False},

            # FEC 参数 (新增)
            "fec_enabled": {"type": "boolean", "default": False},
            "video_fec_enabled": {"type": "boolean", "default": False},
            "audio_fec_enabled": {"type": "boolean", "default": False},
            "fec_group_size": {"type": "integer", "default": 4},
            "fec_parity_count": {"type": "integer", "default": 1}
        }
    }
}
```

#### connect 工具新增参数

```python
{
    "name": "connect",
    # ... 现有参数 ...

    # 新增参数
    "bitrate_mode": {
        "type": "string",
        "enum": ["cbr", "vbr"],
        "default": "vbr"
    },
    "i_frame_interval": {"type": "number", "default": 10.0},

    # FEC 参数
    "fec_enabled": {"type": "boolean", "default": False},
    "video_fec_enabled": {"type": "boolean", "default": False},
    "audio_fec_enabled": {"type": "boolean", "default": False},
    "fec_group_size": {"type": "integer", "default": 4},
    "fec_parity_count": {"type": "integer", "default": 1},

    # UDP wake
    "wake_server": {"type": "boolean", "default": True}
}
```

---

## 3. 流程引导差距

### 3.1 当前问题

- 用户不清楚何时用 ADB 隧道、何时用网络模式
- 网络模式部署流程不明确
- 没有引导用户先用 USB 部署服务端

### 3.2 理想引导流程

```
┌─────────────────────────────────────────────────────────────────┐
│                      推荐使用流程                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  场景 A: 有 USB 数据线                                          │
│  ──────────────────────                                         │
│  1. USB 连接手机                                                │
│  2. connect() → 自动使用 ADB 隧道模式                           │
│                                                                  │
│  场景 B: 需要无线连接 (推荐方式)                                │
│  ──────────────────────────────                                 │
│  1. USB 连接手机 (仅部署阶段)                                   │
│  2. push_server(stay_alive=True) → 部署网络模式服务端           │
│  3. 拔掉 USB                                                    │
│  4. connect(connection_mode='network', device_id='手机IP')      │
│                                                                  │
│  场景 C: 服务端已运行 (热连接)                                  │
│  ────────────────────────────                                   │
│  1. discover_devices() → 发现运行中的服务端                     │
│  2. connect(connection_mode='network', device_id='IP')          │
│                                                                  │
│  ⚠️ 不推荐: ADB WiFi (adb tcpip 5555)                          │
│     - 不安全，明文传输                                          │
│     - 任何人都可连接                                            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 建议修改

#### 工具描述更新

```python
{
    "name": "connect",
    "description": """
Connect to Android device.

RECOMMENDED:
• USB connection → use default (adb_tunnel mode)
• Wireless → first push_server(stay_alive=True) via USB, then use network mode

SECURITY WARNING:
• ADB WiFi (5555) is insecure - do NOT use in public networks
• Network mode should only be used in trusted private networks

Modes:
• adb_tunnel: Uses ADB USB connection (secure, recommended)
• network: Direct TCP+UDP connection (requires server running on device)
    """
}
```

---

## 4. 代码实现差距 ✅ 已修复

### 4.1 push_server 实现 ✅ 已修复

已添加以下参数的处理:
- `video_codec`, `video_bitrate`, `max_fps`, `bitrate_mode`, `i_frame_interval`
- `audio` (audio_enabled)
- `fec_enabled`, `video_fec_enabled`, `audio_fec_enabled`, `fec_group_size`, `fec_parity_count`

### 4.2 _ensure_connected 实现 ✅ 已修复

已添加以下参数的处理:
- `bitrate_mode`, `i_frame_interval`
- `fec_enabled`, `video_fec_enabled`, `audio_fec_enabled`, `fec_group_size`, `fec_parity_count`
- `wake_server`

---

## 5. 优先级排序

| 优先级 | 任务 | 状态 |
|--------|------|------|
| P0 | 添加安全警告 | ✅ 完成 |
| P0 | 标记 ADB WiFi 工具为不推荐 | ✅ 完成 |
| P1 | 补充 push_server 参数 | ✅ 完成 |
| P1 | 补充 connect 参数 | ✅ 完成 |
| P2 | 更新工具描述 | ✅ 完成 |
| P2 | 更新实现代码 | ✅ 完成 |

---

## 6. 修复记录

### 2026-02-17 修复内容

1. **安全警告**
   - `enable_wireless`: 添加 "⚠️ NOT RECOMMENDED" 警告
   - `connect_wireless`: 添加安全警告和网络模式替代方案
   - `tcpip` 参数: 标记为 "[DEPRECATED - Insecure]"
   - `discover_devices`: 更新描述，不再强调 5555 端口

2. **connect 工具新增参数**
   - `wake_server`: UDP wake 唤醒
   - `bitrate_mode`: CBR/VBR 模式
   - `i_frame_interval`: 关键帧间隔
   - `fec_enabled`, `video_fec_enabled`, `audio_fec_enabled`: FEC 开关
   - `fec_group_size`, `fec_parity_count`: FEC 参数

3. **push_server 工具新增参数**
   - `video_codec`, `video_bitrate`, `max_fps`: 视频参数
   - `bitrate_mode`, `i_frame_interval`: 编码参数
   - `audio`: 音频开关
   - `fec_enabled`, `video_fec_enabled`, `audio_fec_enabled`: FEC 开关
   - `fec_group_size`, `fec_parity_count`: FEC 参数

4. **实现代码更新**
   - `_ensure_connected()`: 支持新参数
   - `push_server` 处理: 支持新参数构建服务端启动命令

---

## 附录: test_network_direct.py 参数对照

```python
# 网络设置
--ip              → device_ip
--control-port    → control_port (27184)
--video-port      → video_port (27185)
--audio-port      → audio_port (27186)

# 服务端生命周期
--reuse           → reuse_server
--push            → push_server
--wake            → wake_server
--stay-alive      → stay_alive

# 视频设置
--codec           → video_codec (auto/h264/h265/av1)
--bitrate         → video_bitrate (4000000)
--max-fps         → max_fps (60)
--cbr/--vbr       → bitrate_mode

# 音频设置
--audio           → audio_enabled

# FEC 设置
--fec             → fec_enabled
--video-fec       → video_fec_enabled
--audio-fec       → audio_fec_enabled
--fec-k           → fec_group_size (4)
--fec-m           → fec_parity_count (1)
```

---

## 3. ADB 隧道模式设计 ✅ 已明确

### 3.1 两种连接模式

| 模式 | 参数 | 解码器 | 预览 | 截图 | CPU |
|------|------|--------|------|------|-----|
| 解码器待命模式 | `video=True` | 持续运行 | 可自由开关 | ~16ms | 中等 |
| 纯控制模式 | `video=False` | 不运行 | ❌ | ~50-100ms | 最低 |

### 3.2 设计原则

```
┌─────────────────────────────────────────────────────────────────┐
│                   ADB 隧道模式设计原则                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. 解码器待命模式 (video=True)                                 │
│     • demuxer + decoder 持续运行                                │
│     • 帧缓冲始终有最新帧                                         │
│     • 截图：从帧缓冲直接获取，延迟 ~16ms                         │
│     • GUI 实时预览：可自由开关 (start_preview / stop_preview)   │
│     • lazy_decode=False                                         │
│                                                                  │
│  2. 纯控制模式 (video=False)                                    │
│     • 不建立视频/音频 socket                                     │
│     • 截图：通过控制消息 (SurfaceControl API)                    │
│     • 延迟 ~50-100ms，最低功耗                                   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 代码实现状态

| 组件 | 状态 | 说明 |
|------|------|------|
| connect 工具 | ✅ | `lazy_decode=False`，解码器持续运行 |
| start/stop_preview | ✅ | 分离进程 GUI，可自由开关 |
| 控制消息截图 | ✅ | 纯控制模式下的截图方式 |
| 文档 | ✅ | 两种模式清晰说明 |
