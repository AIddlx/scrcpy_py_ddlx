# scrcpy_http_mcp_server.py 工作逻辑

## 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                    Claude Code (MCP Client)                       │
│                           HTTP POST                               │
└──────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Starlette HTTP Server                          │
│                       (localhost:3359)                            │
├──────────────────────────────────────────────────────────────────┤
│  Routes:                                                          │
│  - POST /mcp          → handle_mcp_request()                      │
│  - GET  /health       → health_check()                            │
└──────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                     ScrcpyMCPHandler                              │
│                     (单例，线程安全)                               │
├──────────────────────────────────────────────────────────────────┤
│  - _client: ScrcpyClient     (当前连接)                          │
│  - _server: MCPServer        (MCP 服务器包装)                     │
│  - _current_config: ClientConfig (当前配置)                      │
│  - _lock: threading.Lock     (线程锁)                            │
├──────────────────────────────────────────────────────────────────┤
│  Methods:                                                         │
│  - call_tool(tool_name, arguments)                               │
│  - _ensure_connected(**kwargs)                                   │
│  - _config_matches(**kwargs)                                     │
└──────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                     scrcpy_py_ddlx                                │
│  - ScrcpyClient      (客户端核心)                                │
│  - ClientConfig      (配置)                                      │
│  - create_mcp_server (工厂函数)                                  │
└──────────────────────────────────────────────────────────────────┘
```

## 请求处理流程

### 1. HTTP 请求入口
```
POST /mcp
Content-Type: application/json

{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "connect",
    "arguments": {"device_id": "192.168.5.4", "connection_mode": "network"}
  },
  "id": 1
}
```

### 2. handle_mcp_request() 处理
```python
async def handle_mcp_request(request):
    body = await request.json()
    method = body.get("method")

    if method == "initialize":
        return {"result": {"protocolVersion": "...", "capabilities": {...}}}

    elif method == "tools/list":
        return {"result": {"tools": TOOLS}}

    elif method == "tools/call":
        tool_name = body["params"]["name"]
        arguments = body["params"].get("arguments", {})
        result = handler.call_tool(tool_name, arguments)
        return {"result": result}
```

### 3. call_tool() 分发
```python
def call_tool(self, tool_name, arguments):
    with self._lock:  # 线程安全
        # 特殊工具（不需要连接）
        if tool_name == "connect":
            return self._handle_connect(arguments)
        if tool_name == "disconnect":
            return self._handle_disconnect()
        if tool_name == "discover_devices":
            return self._handle_discover()
        if tool_name == "list_devices":
            return self._handle_list_devices()

        # 其他工具（需要先连接）
        server = self._ensure_connected()
        if server is None:
            return {"error": "Not connected"}

        # 调用 MCPServer 的方法
        return server.call_tool(tool_name, arguments)
```

### 4. _ensure_connected() 连接管理
```python
def _ensure_connected(self, **kwargs):
    # 1. 检查配置是否匹配
    if self._config_matches(**kwargs):
        return self._server  # 复用现有连接

    # 2. 配置变化，断开旧连接
    if self._server:
        self._server.disconnect()

    # 3. 创建新配置
    config = ClientConfig(
        connection_mode=kwargs.get('connection_mode', 'adb_tunnel'),
        host=kwargs.get('device_id'),
        video=kwargs.get('video', True),
        audio=kwargs.get('audio', False),
        ...
    )

    # 4. 创建并连接
    self._server = create_mcp_server(config)
    result = self._server.connect()

    if result["success"]:
        self._client = self._server._client
        return self._server

    return None
```

## 工具分类

### 不需要连接的工具
| 工具 | 说明 |
|------|------|
| `connect` | 建立连接 |
| `disconnect` | 断开连接 |
| `discover_devices` | 发现设备（ADB + UDP） |
| `list_devices` | 列出 ADB 设备 |
| `get_device_ip` | 获取设备 IP |
| `enable_wireless` | 启用 ADB WiFi |
| `connect_wireless` | ADB WiFi 连接 |
| `disconnect_wireless` | ADB WiFi 断开 |
| `set_video` | 设置视频开关（需重连生效） |
| `set_audio` | 设置音频开关（需重连生效） |

### 需要连接的工具
| 工具 | 说明 |
|------|------|
| `get_state` | 获取设备状态 |
| `tap`, `swipe`, `type_text` | 输入控制 |
| `screenshot`, `start_recording` | 媒体捕获 |
| `press_key`, `back`, `home` | 按键控制 |
| `open_app`, `get_app_list` | 应用管理 |
| ... | (共 39 个) |

## 连接模式

### ADB 隧道模式（默认）
```
电脑 ←── ADB Forward/Reverse ──→ 手机
         (localhost:27183)
```
- 需要 ADB 连接（USB 或 WiFi ADB）
- 单端口传输
- 延迟较高

### 网络直连模式
```
电脑 ←── TCP:27184 ──→ 手机 (控制)
电脑 ←── UDP:27185 ──→ 手机 (视频)
电脑 ←── UDP:27186 ──→ 手机 (音频)
```
- 不需要 ADB
- 多端口分离
- 延迟较低
- 支持 stay-alive

## 配置热切换

当调用 `connect()` 时，如果参数与当前连接不同：

```python
# 第一次连接
connect(device_id="192.168.5.4", audio=False)
# → 创建新连接

# 相同参数再次连接
connect(device_id="192.168.5.4", audio=False)
# → 复用现有连接

# 参数变化
connect(device_id="192.168.5.4", audio=True)
# → 断开旧连接，创建新连接
```

## 关键状态

```python
class ScrcpyMCPHandler:
    _client = None           # ScrcpyClient 实例
    _server = None           # MCPServer 实例
    _lock = threading.Lock() # 线程锁
    _current_config = None   # 当前 ClientConfig
```

## 启动方式

```bash
# 直接运行
python scrcpy_http_mcp_server.py

# 或使用 uvicorn
uvicorn scrcpy_http_mcp_server:app --host 127.0.0.1 --port 3359
```

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
