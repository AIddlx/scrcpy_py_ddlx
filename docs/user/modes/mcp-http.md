# HTTP MCP 模式

使用 HTTP 方式提供 MCP 服务，支持远程访问。

---

## 什么是 HTTP MCP？

HTTP MCP 服务器提供：
- 无状态 HTTP 端点（JSON-RPC）
- 基于 URL 的 Claude Code 配置
- 远程访问支持
- 无需 SSE

---

## 启动

```bash
python scrcpy_http_mcp_server.py
```

服务器启动在 `http://localhost:3359/mcp`

---

## Claude Code 配置

添加到 Claude Code 设置：

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

## 可用工具

| 工具 | 说明 |
|------|------|
| `tap` | 点击屏幕坐标 |
| `swipe` | 从一点滑动到另一点 |
| `home` | 按主页键 |
| `back` | 按返回键 |
| `text` | 输入文字 |
| `screenshot` | 截图 |
| `get_clipboard` | 获取剪贴板内容 |
| `set_clipboard` | 设置剪贴板内容 |
| `list_apps` | 获取已安装应用 |

### 使用示例

在 Claude Code 中：

```
给我的手机截个图
```

```
打开 YouTube 应用
```

```
在搜索框输入"Hello World"
```

---

## 高级选项

```bash
# 自定义端口
python scrcpy_http_mcp_server.py --port 8080

# 自定义主机
python scrcpy_http_mcp_server.py --host 0.0.0.0

# 详细日志
python scrcpy_http_mcp_server.py --verbose
```

---

## 系统要求

- starlette
- uvicorn

```bash
pip install starlette uvicorn
```

---

## 故障排除

### 端口已被占用

更改端口：

```bash
python scrcpy_http_mcp_server.py --port 3360
```

### 连接被拒绝

确保服务器正在运行，并检查防火墙设置。

---

## 相关文档

- [MCP GUI 模式](mcp-gui.md) - GUI 方式 MCP 服务器
- [Python API 模式](python-api.md) - 直接 Python 使用
