# 连接管理工具

> 设备连接和会话管理

---

## connect

连接到 Android 设备。

### 参数

```json
{
  "connection_mode": "adb_tunnel | network",
  "device_id": "string (optional)",
  "control_port": 27184,
  "video_port": 27185,
  "audio_port": 27186,
  "file_port": 27187,
  "video": true,
  "audio": false,
  "codec": "auto | h264 | h265 | av1",
  "bitrate": 8000000,
  "max_fps": 60,
  "bitrate_mode": "vbr | cbr",
  "i_frame_interval": 10.0,
  "fec_enabled": false,
  "fec_k": 4,
  "fec_m": 1,
  "stay_alive": false,
  "wake_server": true,
  "auth_enabled": true
}
```

### 返回

```json
{
  "success": true,
  "device_name": "Samsung SM-S908B",
  "resolution": [1080, 2340],
  "orientation": "portrait"
}
```

### 示例

```json
// ADB 隧道模式 (推荐)
{
  "name": "connect",
  "arguments": {
    "connection_mode": "adb_tunnel"
  }
}

// 网络模式
{
  "name": "connect",
  "arguments": {
    "connection_mode": "network",
    "device_id": "192.168.1.100",
    "fec_enabled": true
  }
}
```

---

## disconnect

断开设备连接。

### 参数

```json
{
  "save_log": true
}
```

### 返回

```json
{
  "success": true,
  "message": "Disconnected"
}
```

---

## push_server

推送服务端 APK 到设备。

### 参数

```json
{
  "stay_alive": false,
  "force": false
}
```

### 返回

```json
{
  "success": true,
  "message": "Server pushed successfully"
}
```

---

## list_devices

列出可用设备。

### 参数

无

### 返回

```json
{
  "devices": [
    {
      "serial": "RF8M70QVZ6N",
      "status": "device",
      "model": "SM-S908B",
      "ip": "192.168.1.100"
    }
  ]
}
```

---

## get_connection_info

获取当前连接信息。

### 参数

无

### 返回

```json
{
  "connected": true,
  "mode": "network",
  "host": "192.168.1.100",
  "ports": {
    "control": 27184,
    "video": 27185,
    "audio": 27186,
    "file": 27187
  },
  "uptime": 3600
}
```

---

## 最佳实践

### 首次连接

```
1. push_server(stay_alive=True)  // USB 推送服务端
2. connect(mode="network")       // 切换到网络模式
3. 可以拔掉 USB 线
```

**注意**：网络模式始终使用 `setsid` 创建独立会话，USB 拔插不会导致服务端终止。`stay_alive` 参数控制服务端是否支持多客户端连接。

### 快速重连

```
1. connect(mode="network", wake_server=True)
   // 自动唤醒 stay-alive 服务端
```

### 安全考虑

- ADB 隧道模式更安全
- 网络模式仅在可信网络使用
- 启用认证 (--auth)
