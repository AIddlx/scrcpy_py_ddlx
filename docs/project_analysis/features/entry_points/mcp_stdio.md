# MCP STDIO 入口 (mcp_stdio.py)

> 标准 MCP 服务器，用于 Claude Desktop

---

## 文件位置

```
mcp_stdio.py
```

---

## 运行方式

```bash
python mcp_stdio.py
```

通过标准输入/输出通信。

---

## Claude Desktop 配置

### Windows

```json
{
  "mcpServers": {
    "scrcpy": {
      "command": "python",
      "args": ["C:\\Project\\IDEA\\2\\new\\scrcpy-py-ddlx\\mcp_stdio.py"]
    }
  }
}
```

### macOS/Linux

```json
{
  "mcpServers": {
    "scrcpy": {
      "command": "python3",
      "args": ["/path/to/scrcpy-py-ddlx/mcp_stdio.py"]
    }
  }
}
```

---

## 特性

- 标准 MCP 协议
- STDIO 通信
- 支持 Claude Desktop 直接调用
- 自动连接管理

### 文件保存路径 (v1.5 规范)

- 截图: `~/Documents/scrcpy-py-ddlx/screenshots/`
- 录音/视频: `~/Documents/scrcpy-py-ddlx/recordings/`
- 下载文件: `~/Documents/scrcpy-py-ddlx/files/<原路径>`

---

## 核心实现

```
mcp_stdio.py
    │
    └── scrcpy_py_ddlx/mcp_server.py
            │
            ├── 工具定义
            ├── 连接管理
            └── 请求处理
```

---

## 相关文档

- [MCP 工具列表](../mcp/tools.md)
- [MCP HTTP 入口](mcp_http.md)
