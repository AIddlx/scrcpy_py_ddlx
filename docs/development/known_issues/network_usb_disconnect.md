# 网络模式 USB 拔插导致连接断开

## 问题描述

在网络模式 (`--net`) 下，拔插 USB 线可能导致连接断开，即使视频/音频是通过 WiFi 直连传输的。

## 症状

```
22:39:25.461 | Device socket closed (buffer had 0 bytes)
22:39:28.499 | Controller loop error: [WinError 10053] 你的主机中的软件中止了一个已建立的连接。
22:39:30.506 | Heartbeat timeout: no PONG for 6.0s (threshold: 5.0s)
22:39:35.490 | No video data for 10s - static screen (VBR mode) or network issue
```

## 根本原因

**推送方式问题！**

旧代码：
```python
# 只有 stay_alive=True 才用 setsid
if stay_alive:
    shell_cmd = f"nohup setsid sh -c '{server_cmd}' ..."
else:
    shell_cmd = f"nohup sh -c '{server_cmd}' ..."  # 没有 setsid！
```

日志显示用的是 `stay_alive=false`：
```
adb shell nohup sh -c '... stay_alive=false ...'
```

**没有 `setsid` 创建新会话，当 ADB 会话结束时，子进程被一起终止！**

### 技术解释

- `nohup` 只能忽略 SIGHUP 信号
- 但当 ADB 连接断开时，整个 shell 会话可能被终止
- **没有 `setsid`，子进程会随父进程一起终止**

## 修复方案

对于网络模式，**始终使用 `setsid`**：

```python
# Always use setsid for network mode to survive ADB disconnect
shell_cmd = f"nohup setsid sh -c '{server_cmd}' ..."
```

## 状态

- **状态**: ✅ 已修复
- **修复提交**: scrcpy_http_mcp_server.py
- **日期**: 2026-03-01

## 相关文件

- `scrcpy_http_mcp_server.py` - 推送命令生成
