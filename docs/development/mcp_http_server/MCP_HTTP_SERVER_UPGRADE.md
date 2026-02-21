# MCP HTTP 服务器升级计划

> 日期：2026-02-16
> 目标：升级 scrcpy_http_mcp_server.py 以支持网络模式和更多参数
> 状态：**阶段 1-4 已完成**

## 已完成

### 阶段 1：更新工具定义 ✅
- [x] 扩展 connect 工具的 inputSchema（13 个参数）
- [x] 添加 set_video 工具
- [x] 添加 set_audio 工具
- [x] 更新版本号为 2.0.0

### 阶段 2：更新连接逻辑 ✅
- [x] 修改 `_ensure_connected` 支持网络模式和完整参数
- [x] 添加 `_config_matches` 检查配置变化
- [x] 支持 connection_mode 参数
- [x] 支持所有网络模式端口参数

### 阶段 3：设备发现改进 ✅
- [x] 更新 discover_devices 使用 UDP 广播发现
- [x] 同时返回 ADB 设备和 UDP 服务端

### 阶段 4：截图和录音改进 ✅
- [x] 截图时自动使用 screenshot_standalone（如果 video=False）
- [x] 录音时检查 audio 配置，给出明确提示（如果 audio=False）
- [x] 新增 `screenshot_network_standalone()` 方法支持网络模式临时截图

**截图方法选择逻辑**：

| 连接模式 | video | 截图方法 |
|----------|-------|----------|
| ADB 隧道 | True | `screenshot()` |
| ADB 隧道 | False | `screenshot_standalone()` |
| 网络 | True | `screenshot()` |
| 网络 | False | `screenshot_network_standalone()` |

## 待完成

### 阶段 5：更多参数支持
- [ ] audio_codec 参数
- [ ] bitrate_mode 参数
- [ ] i_frame_interval 参数
- [ ] FEC 参数

---

## 当前 connect 参数（13 个）

```json
{
  "connection_mode": "adb_tunnel",  // "adb_tunnel" 或 "network"
  "device_id": "192.168.5.4",       // 设备 ID 或 IP
  "control_port": 27184,            // 网络模式 TCP 端口
  "video_port": 27185,              // 网络模式 UDP 视频端口
  "audio_port": 27186,              // 网络模式 UDP 音频端口
  "stay_alive": false,              // stay-alive 模式
  "video": true,                    // 启用视频
  "audio": false,                   // 启用音频
  "codec": "auto",                  // 视频编码器
  "bitrate": 4000000,               // 视频码率
  "max_fps": 60,                    // 最大帧率
  "tcpip": false,                   // 兼容：TCP/IP 模式
  "stay_awake": true                // 保持唤醒
}
```

---

*此文档记录 MCP HTTP 服务器升级进度。*
