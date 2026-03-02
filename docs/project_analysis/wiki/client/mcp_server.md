# mcp_server.py

> **文件**: `scrcpy_py_ddlx/mcp_server.py`
> **功能**: MCP 服务核心实现

---

## 概述

`mcp_server.py` 实现 MCP (Model Context Protocol) 服务核心逻辑，供 `mcp_stdio.py` 和 `scrcpy_http_mcp_server.py` 调用。

---

## MCP 工具列表

### 连接管理

| 工具 | 说明 |
|------|------|
| `connect` | 建立设备连接 |
| `disconnect` | 断开连接 |

### 媒体操作

| 工具 | 说明 |
|------|------|
| `screenshot` | 截图 |
| `record_audio` | 录制音频 |
| `start_video_recording` | 开始视频录制 |
| `stop_video_recording` | 停止视频录制 |

### 文件操作

| 工具 | 说明 |
|------|------|
| `list_dir` | 列出目录 |
| `push_file` | 上传文件 |
| `pull_file` | 下载文件 (v1.5: `local_path` 可选，自动保存到 `files/<原路径>`) |
| `delete_file` | 删除文件 |

### 控制操作

| 工具 | 说明 |
|------|------|
| `tap` | 点击 |
| `swipe` | 滑动 |
| `input_text` | 输入文字 |
| `press_key` | 按键 |
| `get_clipboard` | 获取剪贴板 |
| `set_clipboard` | 设置剪贴板 |

---

## ScrcpyMcpServer 类

```python
class ScrcpyMcpServer:
    def __init__(self)

    # 工具注册
    def register_tools(self) -> None

    # 工具执行
    async def execute_tool(self, name: str, args: dict) -> dict

    # 连接管理
    def ensure_connected(self) -> bool
    def get_client(self) -> Optional[ScrcpyClient]

    # 资源清理
    def cleanup(self) -> None
```

---

## 工具实现示例

### screenshot

```python
async def screenshot(self, args: dict) -> dict:
    """截图工具"""
    client = self.ensure_connected()
    if not client:
        return {"success": False, "error": "Not connected"}

    # 执行截图
    image = client.screenshot()
    if image is None:
        return {"success": False, "error": "Screenshot failed"}

    # 保存文件
    save_path = get_save_dir("screenshots") / f"screenshot_{timestamp}.png"
    image.save(save_path)

    return {
        "success": True,
        "data": {
            "path": str(save_path),
            "width": image.width,
            "height": image.height
        }
    }
```

### tap

```python
async def tap(self, args: dict) -> dict:
    """点击工具"""
    client = self.ensure_connected()
    if not client:
        return {"success": False, "error": "Not connected"}

    x = args.get("x")
    y = args.get("y")

    if x is None or y is None:
        return {"success": False, "error": "Missing x or y"}

    client.control.tap(x, y)
    return {"success": True}
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

## 入口文件

| 文件 | 协议 | 说明 |
|------|------|------|
| `mcp_stdio.py` | STDIO | Claude Desktop |
| `scrcpy_http_mcp_server.py` | HTTP | HTTP JSON-RPC |

---

## 相关文档

- [tools.md](../../features/mcp/tools.md) - 工具完整列表
- [mcp_http.md](../../features/entry_points/mcp_http.md) - HTTP 入口
- [mcp_stdio.md](../../features/entry_points/mcp_stdio.md) - STDIO 入口
