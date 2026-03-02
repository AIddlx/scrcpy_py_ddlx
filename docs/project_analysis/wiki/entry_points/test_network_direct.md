# test_network_direct.py

> **文件**: `tests_gui/test_network_direct.py`
> **功能**: 网络模式入口脚本

---

## 概述

`test_network_direct.py` 是纯网络模式 (TCP 控制 + UDP 媒体) 的主要入口，支持 FEC 和认证。

---

## 命令行参数

### 网络设置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--ip` | 自动检测 | 设备 IP |
| `--control-port` | 27184 | TCP 控制端口 |
| `--video-port` | 27185 | UDP 视频端口 |
| `--audio-port` | 27186 | UDP 音频端口 |

### FEC 设置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--fec` | - | FEC 模式: frame/fragment |
| `--fec-k` | 4 | 每组数据帧数 |
| `--fec-m` | 1 | 每组校验帧数 |

### 视频设置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--codec` | auto | 编解码器 |
| `--bitrate` | 2500000 | 码率 |
| `--max-fps` | 60 | 最大帧率 |
| `--cbr/--vbr` | vbr | 码率模式 |

### 低延迟优化

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--low-latency` | False | 低延迟模式 |
| `--multiprocess` | False | 多进程解码 |

### 服务端生命周期

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--reuse` | False | 复用服务器 |
| `--push/--no-push` | push | 推送 APK |
| `--stay-alive` | False | Stay-Alive 模式（客户端断开后服务端保持运行） |
| `--hot-connect` | False | 自动发现并连接设备（无需指定 IP） |
| `--discover-timeout` | 5 | 发现超时（秒） |

> **注意**: 所有网络模式启动均使用 `setsid` 创建独立会话，服务端进程独立于 ADB 会话，USB 拔插不影响运行。 |

### 认证

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--auth/--no-auth` | no-auth | 启用/禁用认证（同时控制客户端和服务端） |

### 调试选项

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-v/--verbose` | False | 详细日志（DEBUG 级别） |
| `-q/--quiet` | False | 安静模式（WARNING 级别） |
| `--no-tracker` | False | 禁用 latency_tracker（节省 CPU） |

---

## 服务端启动逻辑

```python
def start_server(args, device_serial):
    """
    启动服务端流程:

    1. 检查服务器是否运行
    2. (可选) 推送 APK
    3. (可选) 推送认证密钥
    4. 启动服务器 (nohup setsid - 独立于 ADB 会话)
    """
```

---

## 工作流程

```
1. 解析参数
2. 自动检测/验证 IP
3. 查询设备编码器
4. 选择最佳编解码器
5. 检查/启动服务端
   ├── 推送认证密钥
   ├── 推送服务器 APK
   └── 启动 (nohup)
6. 创建 ClientConfig
7. 连接设备
8. 运行 Qt 事件循环
9. 保存服务端日志
```

---

## 运行示例

```bash
# 基本使用
python tests_gui/test_network_direct.py --ip 192.168.1.100

# 启用 FEC
python tests_gui/test_network_direct.py --fec frame --fec-k 8

# 完整配置
python tests_gui/test_network_direct.py \
    --ip 192.168.1.100 \
    --codec h265 \
    --bitrate 4000000 \
    --fec frame \
    --audio
```

---

## 日志

日志存放在用户缓存目录：

```
~/.cache/scrcpy-py-ddlx/logs/test_gui_logs/scrcpy_network_test_YYYYMMDD_HHMMSS.log
~/.cache/scrcpy-py-ddlx/logs/test_gui_logs/scrcpy_network_test_YYYYMMDD_HHMMSS_server.log
```

### 日志级别

| 选项 | 文件级别 | 控制台级别 |
|------|----------|------------|
| 默认 | DEBUG | INFO |
| `-v/--verbose` | DEBUG | DEBUG |
| `-q/--quiet` | WARNING | WARNING |

---

## 相关文档

- [fec_decoder.md](../client/fec_decoder.md) - FEC 解码
- [auth.md](../client/auth.md) - 认证模块
