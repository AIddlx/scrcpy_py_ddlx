# MCP 工具列表

> 完整的 MCP 工具清单

---

## 连接管理

| 工具 | 说明 | 参数 |
|------|------|------|
| `connect` | 建立设备连接 | `device`, `network_mode` |
| `disconnect` | 断开连接 | - |

---

## 媒体操作

| 工具 | 说明 | 参数 |
|------|------|------|
| `screenshot` | 截图 | - |
| `record_audio` | 录制音频 | `duration`, `output_path` |
| `start_video_recording` | 开始视频录制 | `output_path` |
| `stop_video_recording` | 停止视频录制 | - |

---

## 文件操作

| 工具 | 说明 | 参数 |
|------|------|------|
| `list_dir` | 列出目录 | `path` |
| `push_file` | 上传文件 | `local`, `remote` |
| `pull_file` | 下载文件 | `device_path`, `local_path` (可选) |
| `delete_file` | 删除文件 | `path` |

---

## 控制操作

| 工具 | 说明 | 参数 |
|------|------|------|
| `tap` | 点击 | `x`, `y` |
| `swipe` | 滑动 | `start_x`, `start_y`, `end_x`, `end_y`, `duration` |
| `input_text` | 输入文字 | `text` |
| `press_key` | 按键 | `keycode` |
| `get_clipboard` | 获取剪贴板 | - |
| `set_clipboard` | 设置剪贴板 | `text` |

---

## 使用示例

### 连接并截图

```json
// 1. 连接设备
{
  "tool": "connect",
  "arguments": {
    "device": "192.168.1.100:5555",
    "network_mode": true
  }
}

// 2. 截图
{
  "tool": "screenshot",
  "arguments": {}
}
```

### 点击并输入

```json
// 1. 点击输入框
{
  "tool": "tap",
  "arguments": {
    "x": 540,
    "y": 500
  }
}

// 2. 输入文字
{
  "tool": "input_text",
  "arguments": {
    "text": "Hello World"
  }
}
```

### 文件传输

```json
// 1. 列出目录
{
  "tool": "list_dir",
  "arguments": {
    "path": "/sdcard/Download"
  }
}

// 2. 下载文件 (自动路径)
{
  "tool": "pull_file",
  "arguments": {
    "device_path": "/sdcard/Download/test.txt"
  }
}
// 保存到: files/Download/test.txt

// 3. 下载文件 (指定路径)
{
  "tool": "pull_file",
  "arguments": {
    "device_path": "/sdcard/Download/test.txt",
    "local_path": "C:\\Downloads\\test.txt"
  }
}
```

---

## 返回格式

### 成功

```json
{
  "success": true,
  "data": { ... }
}
```

### 失败

```json
{
  "success": false,
  "error": "错误信息"
}
```

---

## 错误码

| 错误 | 说明 |
|------|------|
| DEVICE_NOT_CONNECTED | 设备未连接 |
| FILE_NOT_FOUND | 文件不存在 |
| PERMISSION_DENIED | 权限不足 |
| TIMEOUT | 操作超时 |
