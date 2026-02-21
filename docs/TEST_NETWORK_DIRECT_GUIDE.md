# test_network_direct.py 使用指南

> 网络模式测试脚本完整参数说明

---

## 快速开始

```bash
# 基本使用（自动检测设备IP，重启服务器）
python -X utf8 tests_gui/test_network_direct.py --no-reuse

# 查看帮助
python -X utf8 tests_gui/test_network_direct.py --help
```

---

## 完整参数列表

### 网络设置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--ip DEVICE_IP` | 自动检测 | 设备 IP 地址 |
| `--control-port PORT` | 27184 | TCP 控制端口 |
| `--video-port PORT` | 27185 | UDP 视频端口 |
| `--audio-port PORT` | 27186 | UDP 音频端口 |

### 服务器生命周期

| 参数 | 说明 |
|------|------|
| `--no-reuse` | 重启服务器（默认） |
| `--reuse` | 复用现有服务器 |
| `--push` | 推送服务器 APK（默认） |
| `--no-push` | 跳过 APK 推送 |
| `--wake` | 使用 UDP 唤醒连接（默认） |
| `--no-wake` | 禁用 UDP 唤醒 |
| `--stay-alive` | 启动服务器为 stay-alive 模式（热连接） |
| `--max-connections N` | stay-alive 模式最大连接数（-1=无限） |

### 视频设置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--codec {auto,h264,h265,av1}` | auto | 视频编码格式 |
| `--list-encoders` | - | 列出设备支持的编码器并退出 |
| `--bitrate BPS` | 4000000 | 视频码率（bps） |
| `--max-fps FPS` | 60 | 最大帧率 |
| `--cbr` | - | 使用 CBR（恒定码率）模式 |
| `--vbr` | - | 使用 VBR（可变码率）模式（默认） |
| `--i-frame-interval SEC` | 10 | I帧间隔（秒），支持小数如 0.5 |

### FEC 抗丢包

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--fec {frame,fragment}` | - | 启用 FEC，指定模式 |
| `--video-fec {frame,fragment}` | - | 仅视频启用 FEC |
| `--audio-fec {frame,fragment}` | - | 仅音频启用 FEC |
| `--fec-k N` | 4 | FEC 数据包数（K） |
| `--fec-m N` | 1 | FEC 校验包数（M） |

### 低延迟优化

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--low-latency` | 关 | 启用 MediaCodec 低延迟模式（Android 11+，部分设备不兼容） |
| `--no-low-latency` | 开 | 禁用低延迟模式（默认） |
| `--encoder-priority {0,1,2}` | 1 | 编码线程优先级：0=普通, 1=紧急, 2=实时 |
| `--encoder-buffer {0,1}` | 0 | 编码缓冲：0=自动, 1=禁用B帧 |
| `--skip-frames` | 开 | 跳过缓冲帧减少延迟（默认启用） |
| `--no-skip-frames` | - | 禁用跳帧（发送所有帧） |

### 调试设置

| 参数 | 说明 |
|------|------|
| `-v, --verbose` | 详细输出 |
| `-q, --quiet` | 安静模式（仅警告） |
| `--show-details` | 连接后显示详细编码器信息 |

### 音频设置

| 参数 | 说明 |
|------|------|
| `--audio` | 启用音频（默认禁用） |
| `--no-audio` | 禁用音频 |

---

## 命令组合

### 服务器模式组合

| 组合 | 用途 | 说明 |
|------|------|------|
| `--no-reuse --push` | 完整重启 | 杀掉旧服务器，推送新 APK，启动（**默认**） |
| `--reuse --no-push` | 快速重连 | 服务器已运行，直接连接 |
| `--no-reuse --no-push` | 热连接 | 服务器已运行，不杀掉，直接连接 |
| `--reuse --push` | 更新重启 | 推送新 APK 到现有服务器 |

### 视频质量组合

| 组合 | 用途 |
|------|------|
| `--codec h264 --bitrate 2000000` | 低带宽/快速编码 |
| `--codec h265 --bitrate 8000000` | 高质量/节省带宽 |
| `--codec h264 --cbr --bitrate 4000000` | 严格带宽控制 |
| `--max-fps 30 --bitrate 2000000` | 低性能设备 |
| `--i-frame-interval 2` | 快速质量恢复（更多 I 帧） |

### FEC 抗丢包组合

| 组合 | 用途 |
|------|------|
| `--fec frame --fec-k 4 --fec-m 1` | 基础 FEC（25% 冗余） |
| `--fec frame --fec-k 4 --fec-m 2` | 中等 FEC（50% 冗余） |
| `--fec frame --fec-k 8 --fec-m 2` | 大分组 FEC（25% 冗余） |
| `--video-fec frame --fec-k 4 --fec-m 1` | 仅视频 FEC |

### 低延迟组合

| 组合 | 用途 |
|------|------|
| `--codec h264 --skip-frames` | 最低延迟（H264 编码更快） |
| `--low-latency --encoder-priority 2` | 激进优化（可能不稳定） |
| `--encoder-buffer 1 --skip-frames` | 禁用 B 帧 + 跳帧 |

### 调试组合

| 组合 | 用途 |
|------|------|
| `-v --show-details` | 详细信息 |
| `--list-encoders` | 查看编码器 |
| `--no-reuse --stay-alive -v` | 调试热连接模式 |

---

## 常用场景

### 场景 1: 首次连接 / 代码更新后

```bash
python -X utf8 tests_gui/test_network_direct.py --no-reuse --push
```

### 场景 2: 快速测试（服务器已运行）

```bash
python -X utf8 tests_gui/test_network_direct.py --reuse --no-push
```

### 场景 3: WiFi 信号差

```bash
python -X utf8 tests_gui/test_network_direct.py --no-reuse --fec frame --fec-k 4 --fec-m 2
```

### 场景 4: 最低延迟

```bash
python -X utf8 tests_gui/test_network_direct.py --no-reuse --codec h264 --bitrate 2000000 --max-fps 30
```

### 场景 5: 高质量录制

```bash
python -X utf8 tests_gui/test_network_direct.py --no-reuse --codec h265 --bitrate 8000000 --cbr
```

### 场景 6: 启用音频

```bash
python -X utf8 tests_gui/test_network_direct.py --no-reuse --audio
```

### 场景 7: 热连接模式（断开后服务器继续运行）

```bash
# 首次启动
python -X utf8 tests_gui/test_network_direct.py --no-reuse --stay-alive

# 后续连接
python -X utf8 tests_gui/test_network_direct.py --reuse --no-push
```

### 场景 8: 查看设备编码器

```bash
python -X utf8 tests_gui/test_network_direct.py --list-encoders
```

---

## 故障排除

### 连接失败

1. 确保设备和电脑在同一 WiFi 网络
2. 检查防火墙是否阻止端口 27183-27186
3. 尝试 `--no-reuse --push` 重启服务器

### 黑屏

1. 尝试 `--codec h264`（兼容性更好）
2. 检查服务器日志：`adb shell cat /data/local/tmp/scrcpy_server.log`
3. 使用 `--list-encoders` 检查编码器支持

### 画面卡顿

1. 启用 FEC：`--fec frame --fec-k 4 --fec-m 2`
2. 降低码率：`--bitrate 2000000`
3. 降低帧率：`--max-fps 30`

### 延迟高

1. 使用 H264：`--codec h264`
2. 降低分辨率（通过 ADB 设置）
3. 确保 FEC 未启用（增加处理开销）

---

## 相关文档

- [网络管线](development/NETWORK_PIPELINE.md)
- [FEC 协议规范](FEC_PLI_PROTOCOL_SPEC.md)
- [E2E 延迟分析](development/E2E_LATENCY_ANALYSIS.md)
