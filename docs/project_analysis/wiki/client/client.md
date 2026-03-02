# client.py - 主客户端

> **路径**: `scrcpy_py_ddlx/client/client.py`
> **职责**: scrcpy 客户端主类，协调所有组件的初始化和生命周期

---

## 类清单

### ScrcpyClient

**职责**: 完整的 scrcpy 客户端实现，遵循官方 scrcpy 初始化顺序

**线程**: 主线程（初始化），控制线程（控制循环）

**依赖**:
- `ClientConfig` - 配置
- `ClientState` - 状态
- `ComponentFactory` - 组件工厂
- `ConnectionManager` - 连接管理
- `ControlMessageQueue` - 控制队列

---

## 属性清单

### 配置和状态

| 属性 | 类型 | 说明 |
|------|------|------|
| `config` | ClientConfig | 客户端配置 |
| `state` | ClientState | 客户端状态 |

### 组件引用

| 属性 | 类型 | 说明 |
|------|------|------|
| `_component_factory` | ComponentFactory | 组件工厂 |
| `_control_queue` | ControlMessageQueue | 控制消息队列 |
| `_video_demuxer` | UdpVideoDemuxer | 视频解复用器 |
| `_audio_demuxer` | UdpAudioDemuxer | 音频解复用器 |
| `_video_decoder` | VideoDecoder | 视频解码器 |
| `_audio_decoder` | AudioDecoder | 音频解码器 |
| `_video_window` | QOpenGLWindow | 视频窗口 |
| `_screen` | Screen | 屏幕渲染 |
| `_audio_player` | QtPushPlayer | 音频播放器 |
| `_control_thread` | Thread | 控制线程 |
| `_recorder` | Recorder | 录制器 |
| `_device_receiver` | DeviceReceiver | 设备消息接收 |
| `_heartbeat` | HeartbeatManager | 心跳管理 |

### 文件传输

| 属性 | 类型 | 说明 |
|------|------|------|
| `_file_channel` | FileChannel | 网络模式文件通道 |
| `_file_ops` | FileOps | ADB 模式文件操作 |
| `_file_conn` | NetworkConnection | 文件连接 |
| `_file_socket` | socket | 文件 socket |

### 剪贴板同步

| 属性 | 类型 | 说明 |
|------|------|------|
| `_clipboard_sequence` | int | 剪贴板序列号 |
| `_clipboard_monitor_running` | bool | 监控运行标志 |
| `_last_clipboard` | str | 上次剪贴板内容 |

### 截图控制

| 属性 | 类型 | 说明 |
|------|------|------|
| `_screenshot_queue` | Queue | 截图请求队列 |
| `_screenshot_last_time` | float | 上次截图时间 |
| `_screenshot_min_interval` | float | 最小间隔 (0.3s) |
| `_screenshot_callback` | Callable | 截图回调 |

---

## 方法清单

### 生命周期

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `__init__` | config | - | 初始化客户端 |
| `connect` | timeout | bool | 连接到服务端 |
| `disconnect` | - | - | 断开连接 |
| `connect_hot` | device_ip, ports, timeout | bool | 热连接（唤醒+连接） |

### 组件初始化 (内部)

| 方法 | 说明 |
|------|------|
| `_init_server` | 初始化服务器连接 |
| `_initialize_components_multiprocess` | 多进程模式初始化 |

### 视频控制

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `start_video_demuxer` | - | bool | 启动视频解复用 |
| `enable_video` | - | - | 启用视频解码 |
| `disable_video` | - | - | 禁用视频解码 |
| `request_screenshot` | callback | - | 请求截图 |

### 音频控制

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `start_audio_demuxer` | - | bool | 启动音频解复用 |
| `enable_audio` | - | - | 启用音频解码 |
| `disable_audio` | - | - | 禁用音频解码 |
| `start_audio_recording` | filename, duration | bool | 开始录音 |
| `stop_audio_recording` | - | str | 停止录音 |

### 触摸控制

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `touch_down` | x, y, pointer_id | - | 触摸按下 |
| `touch_up` | pointer_id | - | 触摸抬起 |
| `touch_move` | x, y, pointer_id | - | 触摸移动 |
| `tap` | x, y | - | 单击 |
| `swipe` | start, end, duration | - | 滑动 |
| `long_press` | x, y, duration | - | 长按 |

### 按键控制

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `key_press` | keycode | - | 按下按键 |
| `key_release` | keycode | - | 释放按键 |
| `key_click` | keycode | - | 点击按键 |
| `input_text` | text | - | 输入文本 |
| `back` | - | - | 返回键 |
| `home` | - | - | Home键 |
| `menu` | - | - | 菜单键 |
| `volume_up` | - | - | 音量+ |
| `volume_down` | - | - | 音量- |
| `power` | - | - | 电源键 |

### 文件传输

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `list_dir` | path | dict | 列出目录 |
| `file_stat` | path | dict | 获取文件信息 |
| `push_file` | local, remote | bool | 上传文件 |
| `pull_file` | remote, local? | bool | 下载文件 (v1.5: local 可选，自动保存到 files/) |
| `make_dir` | path | bool | 创建目录 |
| `delete_file` | path | bool | 删除文件 |

### 其他控制

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `set_clipboard` | text | - | 设置剪贴板 |
| `get_clipboard` | - | str | 获取剪贴板 |
| `rotate_screen` | - | - | 旋转屏幕 |
| `reset_video` | - | - | 重置视频（请求关键帧） |

---

## 初始化顺序

connect() 方法遵循官方 scrcpy 初始化顺序：

```
STEP 1:  服务器连接 (_init_server)
STEP 2:  VideoDemuxer
STEP 3:  AudioDemuxer
STEP 4:  VideoDecoder
STEP 5:  AudioDecoder
STEP 6:  Recorder (可选)
STEP 7:  Controller
STEP 8:  VideoWindow
STEP 8.5: Screen
STEP 9:  AudioPlayer (可选)
STEP 10: DeviceReceiver
STEP 11: 启动 Demuxers
STEP 12: Lazy Decode (可选)
```

---

## 多进程模式

当 `config.multiprocess=True` 且网络模式时，使用多进程架构：

```
[主进程]
    │
    ├── Controller
    ├── AudioDemuxer + AudioDecoder + AudioPlayer
    └── VideoWindow (从SHM读取)
         │
         ▼
[解码进程]
    │
    ├── VideoDemuxer
    ├── VideoDecoder
    └── 写入 SHM
```

初始化顺序不同：
1. 关闭主进程 video socket
2. 创建控制队列
3. 创建解码进程
4. 启动解码进程
5. 创建视频窗口（SHM源）
6. 创建控制器
7. 创建设备接收器
8. 创建音频组件

---

## 数据流

```
[UDP Socket] ──→ [Demuxer] ──→ [Decoder] ──→ [Window/Player]
                              │
[Control Queue] ──────────────┴──→ [Control Socket] ──→ [服务端]
```

---

## 线程安全

| 操作 | 线程 | 锁 |
|------|------|-----|
| 初始化 | 主线程 | 无 |
| 控制发送 | 控制线程 | _control_queue |
| 截图回调 | GUI线程 | 无 |
| 剪贴板同步 | 后台线程 | 无 |

---

## 依赖关系

```
ScrcpyClient
    │
    ├──→ ClientConfig (配置)
    ├──→ ClientState (状态)
    ├──→ ComponentFactory (组件创建)
    │       ├──→ VideoDemuxer
    │       ├──→ AudioDemuxer
    │       ├──→ VideoDecoder
    │       ├──→ AudioDecoder
    │       ├──→ VideoWindow
    │       └──→ ...
    │
    ├──→ ConnectionManager (连接)
    │       └──→ NetworkConnection
    │
    ├──→ ControlMessageQueue (控制)
    │
    └──→ FileChannel (文件传输)
```

---

## 常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `_screenshot_min_interval` | 0.3 | 截图最小间隔（秒）|

---

*此文档基于代码分析生成*
