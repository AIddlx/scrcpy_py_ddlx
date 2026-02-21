# ADB 隧道模式 - 完整功能文档

> 本文档记录 ADB 隧道模式的功能特性、配置选项和使用方法。
>
> **最后更新**: 2026-02-18

---

## 概述

ADB 隧道模式是 scrcpy-py-ddlx 的**默认连接模式**，通过 USB 数据线连接 Android 设备，使用 ADB 协议进行通信。

### 特点

| 特性 | 说明 |
|------|------|
| **安全性** | ✅ 高（USB 连接，无网络暴露） |
| **稳定性** | ✅ 高（有线连接，不受网络波动影响） |
| **延迟** | ✅ 低（USB 2.0 理论 480Mbps） |
| **兼容性** | ✅ 广（所有支持 ADB 的设备） |

---

## 连接流程

```
客户端启动
    ↓
检测 USB 设备 (adb devices)
    ↓
推送 scrcpy-server 到设备
    ↓
查询设备能力 (首次连接)
    ↓
选择最佳编码器 (AV1 → H.265 → H.264)
    ↓
启动服务端 (指定编码器)
    ↓
建立 ADB 隧道 (forward)
    ↓
连接视频/控制 Socket
    ↓
开始传输
```

---

## 编码器自动选择

### 选择优先级

```
AV1 硬编(设备) + 硬解(PC) → 最佳
H.265 硬编(设备) + 硬解(PC)
H.264 硬编(设备) + 硬解(PC)
AV1 硬编(设备) + 软解(PC)
H.265 硬编(设备) + 软解(PC)
H.264 硬编(设备) + 软解(PC)
H.264 软编 → 兜底
```

### 硬件编码器识别

支持的芯片厂商：

| 厂商 | OMX 前缀 | C2 前缀 |
|------|----------|---------|
| 高通 Snapdragon | `OMX.qcom.` | `c2.qti.` |
| 联发科 MediaTek | `OMX.MTK.` | `c2.mtk.` |
| 三星 Exynos | `OMX.Exynos.`, `OMX.sec.` | `c2.exynos.` |
| 华为 HiSilicon | `OMX.hisi.` | `c2.hisi.` |
| NVIDIA Tegra | `OMX.NVIDIA.` | - |
| 晶晨 Amlogic | `OMX.amlogic.` | `c2.amlogic.` |
| 瑞芯微 Rockchip | `OMX.rk.` | `c2.rk.` |

### 能力缓存

- **位置**: `~/.cache/scrcpy-py-ddlx/capability_cache.json`
- **策略**: 永久缓存（硬件能力不变）
- **首次查询**: ~400ms（ADB 查询设备编码器）
- **后续使用**: 0ms（直接读取缓存）

---

## MCP 工具列表

### 连接管理

| 工具 | 说明 |
|------|------|
| `connect` | 连接设备 |
| `disconnect` | 断开连接 |
| `set_video` | 设置视频开关（需重连） |
| `set_audio` | 设置音频开关（需重连） |
| `get_state` | 获取设备状态（尺寸、方向等） |
| `list_devices` | 列出已连接设备 |

### 触控操作

| 工具 | 说明 |
|------|------|
| `tap` | 点击 |
| `swipe` | 滑动 |
| `long_press` | 长按 |
| `scroll` | 滚动 |
| `pinch` | 双指缩放 |

### 按键操作

| 工具 | 说明 |
|------|------|
| `press_back` | 返回键 |
| `press_home` | 主页键 |
| `press_menu` | 菜单键 |
| `press_power` | 电源键 |
| `volume_up` | 音量+ |
| `volume_down` | 音量- |
| `wake_up` | 唤醒屏幕 |
| `inject_keycode` | 注入任意按键 |

### 文字输入

| 工具 | 说明 |
|------|------|
| `type_text` | 输入文字（支持中文，需 YADB） |

### 屏幕

| 工具 | 说明 |
|------|------|
| `screenshot` | 截图（支持 video=true/false 两种模式） |
| `start_preview` | 启动预览窗口（分离进程） |
| `stop_preview` | 停止预览窗口 |

### 剪贴板

| 工具 | 说明 |
|------|------|
| `get_clipboard` | 获取剪贴板 |
| `set_clipboard` | 设置剪贴板 |

### 应用管理

| 工具 | 说明 |
|------|------|
| `list_apps` | 列出应用 |
| `start_app` | 启动应用 |
| `stop_app` | 停止应用 |

### 录音

| 工具 | 说明 |
|------|------|
| `record_audio` | 录音（WAV/OPUS/MP3） |
| `stop_audio_recording` | 停止录音 |
| `is_recording_audio` | 检查录音状态 |

---

## 截图模式对比

| 模式 | 方法 | 耗时 | 适用场景 |
|------|------|------|---------|
| video=true | 视频流截图 | ~16ms | 高频截图、实时预览 |
| video=false | ADB screencap | ~300ms | 偶尔截图、节省资源 |

---

## 预览窗口

### 功能

- 分离进程运行，不阻塞主程序
- 实时触摸跟踪（touch_down, touch_move, touch_up）
- 支持设备旋转自动适应
- 窗口调整保持视频宽高比

### 操作

| 鼠标操作 | 设备响应 |
|----------|----------|
| 左键单击 | 点击 |
| 左键拖动 | 滑动 |
| 滚轮 | 滚动 |
| Esc | 返回键 |
| Home | 主页键 |

---

## 坐标系统

```
原点 (0, 0): 屏幕左上角
X 轴: 水平向右 (0 → width-1)
Y 轴: 垂直向下 (0 → height-1)

竖屏 (width < height):
  1080x2400 屏幕
  - 左上: (0, 0)
  - 右下: (1079, 2399)
  - 中心: (540, 1200)

横屏 (width > height):
  2400x1080 屏幕
  - 左上: (0, 0)
  - 右下: (2399, 1079)
  - 中心: (1200, 540)

注意: 旋转时 width/height 会变化！
      调用 get_state() 获取当前尺寸。
```

---

## 连接参数

```json
{
  "connection_mode": "adb_tunnel",
  "video": true,
  "audio": false,
  "codec": "auto",
  "bitrate": 8000000,
  "max_fps": 60,
  "bitrate_mode": "vbr",
  "i_frame_interval": 10.0,
  "stay_awake": true
}
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `video` | true | 启用视频流 |
| `audio` | false | 启用音频流 |
| `codec` | auto | 编码器（auto/h264/h265/av1） |
| `bitrate` | 8000000 | 视频码率（bps） |
| `max_fps` | 60 | 最大帧率 |
| `bitrate_mode` | vbr | 码率模式（vbr/cbr） |
| `i_frame_interval` | 10.0 | 关键帧间隔（秒） |
| `stay_awake` | true | 保持屏幕常亮 |

---

## 已知限制

1. **视频录制** - 功能不完整，已禁用（帧同步问题）
2. **Windows 反向隧道** - 不支持，自动使用 forward 模式

---

## 故障排除

### 连接失败

```bash
# 检查设备连接
adb devices

# 重启 ADB 服务
adb kill-server && adb start-server

# 检查 USB 调试是否开启
# 设置 → 开发者选项 → USB 调试
```

### 编码器问题

```bash
# 清除能力缓存，重新检测
rm ~/.cache/scrcpy-py-ddlx/capability_cache.json

# 重新连接，会自动查询设备能力
```

### 预览窗口无法关闭

- 按 Ctrl+C 两次强制退出
- 或关闭预览窗口后等待服务端清理

---

## 相关文档

- [协议规范](./PROTOCOL_SPEC.md)
- [Multiprocessing 最佳实践](./development/MULTIPROCESSING_BEST_PRACTICES.md)
