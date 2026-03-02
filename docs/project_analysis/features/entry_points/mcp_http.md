# MCP HTTP 入口 (scrcpy_http_mcp_server.py)

> HTTP JSON-RPC MCP 服务器，支持 URL 配置

---

## 文件位置

```
scrcpy_http_mcp_server.py
```

---

## 运行方式

```bash
python scrcpy_http_mcp_server.py
```

默认监听 `http://localhost:3359`

---

## Claude Code 配置

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

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/mcp` | POST | JSON-RPC 请求 |
| `/health` | GET | 健康检查 |
| `/tools` | GET | 工具列表 |

---

## 特性

### 无状态设计

- 每个请求独立处理
- 支持多客户端并发
- 自动管理连接生命周期

### 文件保存

- 截图: `~/Documents/scrcpy-py-ddlx/screenshots/`
- 录音/视频: `~/Documents/scrcpy-py-ddlx/recordings/`
- 下载文件: `~/Documents/scrcpy-py-ddlx/files/<原路径>` (如 `files/DCIM/Camera/IMG.jpg`)

### UTF-8 支持

- 完整中文支持
- 正确处理非 ASCII 字符

---

## 依赖

- Starlette
- Uvicorn

```bash
pip install starlette uvicorn
```

---

## 相关文档

- [MCP 工具列表](../mcp/tools.md)
- [MCP STDIO 入口](mcp_stdio.md)
