# 网络模式入口 (test_network_direct.py)

> 纯网络模式 (TCP 控制 + UDP 媒体)，支持无线投屏

---

## 文件位置

```
tests_gui/test_network_direct.py
```

---

## 运行方式

```bash
# 自动检测设备 IP
python -X utf8 tests_gui/test_network_direct.py

# 指定设备 IP
python -X utf8 tests_gui/test_network_direct.py --ip 192.168.1.100

# 快速重连 (复用服务器)
python -X utf8 tests_gui/test_network_direct.py --reuse --no-push

# 启用 FEC
python -X utf8 tests_gui/test_network_direct.py --fec frame

# 完整配置
python -X utf8 tests_gui/test_network_direct.py \
    --ip 192.168.1.100 \
    --fec frame --fec-k 8 --fec-m 2 \
    --codec h265 \
    --bitrate 4000000 \
    --audio
```

---

## 命令行参数

### 网络设置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--ip` | 自动检测 | 设备 IP 地址 |
| `--control-port` | 27184 | TCP 控制端口 |
| `--video-port` | 27185 | UDP 视频端口 |
| `--audio-port` | 27186 | UDP 音频端口 |

### FEC 设置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--fec` | - | FEC 模式: frame/fragment |
| `--video-fec` | - | 仅视频 FEC |
| `--audio-fec` | - | 仅音频 FEC |
| `--fec-k` | 4 | 每组数据帧数 |
| `--fec-m` | 1 | 每组校验帧数 |

### 视频设置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--codec` | auto | 编解码器: auto/h264/h265/av1 |
| `--bitrate` | 2500000 | 码率 (bps) |
| `--max-fps` | 60 | 最大帧率 |
| `--cbr` | - | 恒定码率模式 |
| `--vbr` | 默认 | 可变码率模式 |
| `--i-frame-interval` | 10.0 | 关键帧间隔 (秒) |

### 低延迟优化

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--low-latency` | False | MediaCodec 低延迟模式 |
| `--encoder-priority` | 1 | 编码器优先级: 0/1/2 |
| `--encoder-buffer` | 0 | 编码器缓冲: 0=auto, 1=禁用B帧 |
| `--skip-frames` | True | 跳过缓冲帧 |
| `--multiprocess` | False | 多进程解码 (避免 GIL) |

### 服务端生命周期

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--reuse` | False | 复用现有服务器 |
| `--no-reuse` | - | 重启服务器 (默认) |
| `--push` | True | 推送服务器 APK |
| `--no-push` | - | 跳过 APK 推送 |
| `--wake` | True | 使用 UDP 唤醒连接 |
| `--stay-alive` | False | 热连接模式 |

### 认证设置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--auth` | True | 启用 HMAC-SHA256 认证 |
| `--no-auth` | - | 禁用认证 |
| `--auth-key` | 自动 | 认证密钥文件路径 |

### 音频设置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--audio` | False | 启用音频 |
| `--no-audio` | - | 禁用音频 |
| `--audio-dup` | False | 双端播放 |

### 调试设置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-v, --verbose` | False | 详细输出 |
| `-q, --quiet` | False | 静默模式 |
| `--show-details` | False | 显示编码器详情 |
| `--drop-rate` | 0.0 | 模拟丢包率 |
| `--queue-size` | 3 | 数据包队列大小 |

---

## 服务端生命周期模式

| 组合 | 说明 |
|------|------|
| `--reuse --no-push` | 快速重连 (复用服务器) |
| `--no-reuse --push` | 完全重启 (默认) |
| `--reuse --push` | 首次部署 (推送新 APK) |
| `--no-reuse --no-push` | 热连接 (服务器必须运行) |

---

## 工作流程

```
1. 解析命令行参数
2. 自动检测/验证设备 IP
3. 查询设备编码器
4. 选择最佳编解码器
5. 检查/启动服务端
   - 推送认证密钥 (如启用)
   - 推送服务器 APK
   - 启动服务器 (nohup setsid) - 独立于 ADB 会话
6. 创建 ClientConfig
7. 连接设备
8. 运行 Qt 事件循环
9. 保存服务端日志
```

---

## 关键特性

### 纯网络模式

- ADB 仅用于启动服务器
- 服务器启动后可拔掉 USB
- TCP 控制 + UDP 媒体分离
- **始终使用 `setsid`**，USB 拔插不会导致服务终止

### 文件保存路径 (v1.5 规范)

- 截图: `~/Documents/scrcpy-py-ddlx/screenshots/`
- 录音/视频: `~/Documents/scrcpy-py-ddlx/recordings/`
- 下载文件: `~/Documents/scrcpy-py-ddlx/files/<原路径>`

### 认证流程

```
1. 生成/加载 32 字节密钥
2. 通过 ADB 推送密钥到设备
3. 客户端保存密钥副本
4. 连接时进行 Challenge-Response
5. 设备端自动删除密钥
```

### FEC 支持

```
原始帧:  [F1] [F2] [F3] [F4]
FEC 帧:  [P1 = F1⊕F2⊕F3⊕F4]

丢失 F3 时: F3 = F1⊕F2⊕F4⊕P1
```

---

## 日志

日志保存到 `test_logs/` 目录：
```
test_logs/scrcpy_network_test_YYYYMMDD_HHMMSS.log
test_logs/scrcpy_network_test_YYYYMMDD_HHMMSS_server.log
```

---

## 相关文档

- [USB 模式入口](usb_mode.md)
- [网络认证](../connection/auth.md)
- [FEC 纠错](../media/fec.md)
- [协议规范](../../../PROTOCOL_SPEC.md)
