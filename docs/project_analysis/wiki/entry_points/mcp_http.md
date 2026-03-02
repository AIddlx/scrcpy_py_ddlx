# scrcpy_http_mcp_server.py

> **文件**: `scrcpy_http_mcp_server.py`
> **功能**: HTTP MCP 服务器入口

---

## 概述

`scrcpy_http_mcp_server.py` 提供 HTTP JSON-RPC MCP 服务，支持 URL 配置。

---

## 特性

- 无状态设计
- 支持多客户端并发
- UTF-8 完整支持
- 自动文件保存

---

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/mcp` | POST | JSON-RPC 请求 |
| `/health` | GET | 健康检查 |
| `/tools` | GET | 工具列表 |

---

## 配置

### Claude Code

```json
{
  "mcpServers": {
    "scrcpy-http": {
      "url": "http://localhost:3359/mcp"
    }
  }
}
```

---

## 文件保存位置 (v1.5 规范)

| 类型 | 路径 | 示例 |
|------|------|------|
| 截图 | `screenshots/` | `screenshot_20260301_232016.jpg` |
| 录音/视频 | `recordings/` | `recording_20260301_231830.opus` |
| 下载文件 | `files/<原路径>` | `files/DCIM/Camera/IMG.jpg` |

**说明**：所有路径相对于当前工作目录，v1.5 版本已规范化路径结构。

### pull_file 自动路径 (v1.5)

```json
// 不指定 local_path → 自动按原路径保存到 files/ 目录
{"tool": "pull_file", "arguments": {"device_path": "/sdcard/DCIM/Camera/IMG.jpg"}}
// → files/DCIM/Camera/IMG.jpg

// 指定 local_path → 保存到指定位置
{"tool": "pull_file", "arguments": {"device_path": "/sdcard/DCIM/Camera/IMG.jpg", "local_path": "D:/photos/IMG.jpg"}}
```

---

## 核心 API

```python
# Starlette 路由
routes = [
    Route("/mcp", handle_mcp, methods=["POST"]),
    Route("/health", handle_health, methods=["GET"]),
    Route("/tools", handle_tools, methods=["GET"]),
]

# 启动服务器
uvicorn.run(app, host="0.0.0.0", port=3359)
```

---

## 依赖

```
starlette
uvicorn
```

---

## 运行方式

```bash
python scrcpy_http_mcp_server.py
```

---

## 相关文档

- [mcp_server.md](../client/mcp_server.md) - MCP 核心实现
