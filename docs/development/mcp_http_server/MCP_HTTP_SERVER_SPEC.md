# scrcpy_http_mcp_server.py 开发规范

> **版本**: 1.0
> **最后更新**: 2026-02-17
> **适用范围**: scrcpy_http_mcp_server.py 及相关 MCP 组件

---

## 目录

1. [概述](#1-概述)
2. [文件结构](#2-文件结构)
3. [编码规范](#3-编码规范)
4. [工具定义规范](#4-工具定义规范)
5. [错误处理规范](#5-错误处理规范)
6. [日志规范](#6-日志规范)
7. [状态管理规范](#7-状态管理规范)
8. [连接模式规范](#8-连接模式规范)
9. [测试规范](#9-测试规范)
10. [常见陷阱](#10-常见陷阱)

---

## 1. 概述

### 1.1 文件定位

`scrcpy_http_mcp_server.py` 是一个**无状态的 HTTP MCP 服务器**，为 Claude Code 等 MCP 客户端提供 Android 设备控制能力。

### 1.2 核心职责

```
┌─────────────────────────────────────────────────┐
│              scrcpy_http_mcp_server.py          │
├─────────────────────────────────────────────────┤
│  1. HTTP 服务 (Starlette + Uvicorn)             │
│  2. MCP 协议处理 (JSON-RPC 2.0)                 │
│  3. 设备连接管理 (ADB/Network)                  │
│  4. 工具调用分发                                │
│  5. 线程安全保护                                │
└─────────────────────────────────────────────────┘
```

### 1.3 依赖关系

```
scrcpy_http_mcp_server.py
    │
    ├── starlette (HTTP 框架)
    ├── uvicorn (ASGI 服务器)
    │
    └── scrcpy_py_ddlx (核心库)
            ├── ScrcpyClient
            ├── ClientConfig
            ├── MCPServer
            └── ADBManager
```

---

## 2. 文件结构

### 2.1 代码区域划分

```python
# ============== 区域 1: 导入和全局常量 ==============
# 第 1-100 行
# - 标准库导入
# - 第三方库导入
# - 全局常量 (TOOLS, RESOURCES, PROMPTS)

# ============== 区域 2: 辅助函数 ==============
# 第 100-600 行
# - check_and_install_yadb()
# - 其他独立工具函数

# ============== 区域 3: 核心类 ==============
# 第 600-1400 行
# - class ScrcpyMCPHandler

# ============== 区域 4: HTTP 处理 ==============
# 第 1400-1500 行
# - handle_mcp_request()
# - health_check()

# ============== 区域 5: 应用配置 ==============
# 第 1500-1600 行
# - routes, app
# - main()

# ============== 区域 6: 入口 ==============
# 第 1600+ 行
# - if __name__ == "__main__"
```

### 2.2 添加新代码的位置

| 新增内容 | 放置位置 |
|---------|---------|
| 新工具定义 | `TOOLS` 列表（按类别分组） |
| 新辅助函数 | 区域 2，`check_and_install_yadb` 之后 |
| 新工具处理逻辑 | `ScrcpyMCPHandler.call_tool()` 内 |
| 新 HTTP 端点 | `routes` 列表 |

---

## 3. 编码规范

### 3.1 Python 语法

#### ✅ 正确

```python
# 布尔值使用首字母大写
"enabled": True
"disabled": False

# 字符串使用双引号（JSON 兼容）
description = "Connect to device"

# 类型注解
def call_tool(self, tool_name: str, arguments: Dict) -> Dict:

# 异常处理
try:
    result = some_operation()
except Exception as e:
    logger.exception(f"Operation failed: {e}")
    return {"success": False, "error": str(e)}
```

#### ❌ 错误

```python
# 布尔值使用小写（JavaScript 风格）
"enabled": true   # NameError!
"disabled": false # NameError!

# 缺少类型注解
def call_tool(self, tool_name, arguments):

# 裸异常捕获
except:
    pass
```

### 3.2 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 函数 | snake_case | `discover_devices()` |
| 类 | PascalCase | `ScrcpyMCPHandler` |
| 常量 | UPPER_SNAKE | `DISCOVERY_PORT` |
| 私有方法 | _前缀 | `_ensure_connected()` |
| MCP 工具 | snake_case | `get_device_ip` |
| JSON 字段 | snake_case | `"device_id"` |

### 3.3 导入顺序

```python
# 1. 标准库
import sys
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

# 2. 第三方库
from starlette.applications import Starlette

# 3. 本地模块
from scrcpy_py_ddlx import create_mcp_server
```

---

## 4. 工具定义规范

### 4.1 工具定义模板

```python
{
    "name": "tool_name",           # snake_case，必须唯一
    "description": "...",          # 简洁描述 + 使用说明
    "inputSchema": {
        "type": "object",
        "properties": {
            "param1": {
                "type": "string",
                "description": "参数说明",
                # 可选: enum, default, minimum, maximum
            },
            "param2": {
                "type": "integer",
                "default": 100,
                "description": "可选参数，有默认值"
            }
        },
        "required": ["param1"]     # 必填参数列表
    }
}
```

### 4.2 工具分类

工具按功能分组定义，顺序如下：

```python
TOOLS = [
    # === 1. 连接管理 ===
    {"name": "connect", ...},
    {"name": "disconnect", ...},
    {"name": "discover_devices", ...},
    {"name": "list_devices", ...},

    # === 2. 设备信息 ===
    {"name": "get_state", ...},
    {"name": "get_device_ip", ...},

    # === 3. 屏幕 ===
    {"name": "screenshot", ...},

    # === 4. 触控 ===
    {"name": "tap", ...},
    {"name": "swipe", ...},

    # === 5. 按键 ===
    {"name": "press_key", ...},
    {"name": "back", ...},
    {"name": "home", ...},

    # === 6. 文字输入 ===
    {"name": "input_text", ...},
    {"name": "get_clipboard", ...},

    # === 7. 应用管理 ===
    {"name": "list_apps", ...},
    {"name": "open_app", ...},

    # === 8. 媒体 ===
    {"name": "record_audio", ...},
]
```

### 4.3 描述规范

#### ✅ 好的描述

```python
"description": "Connect to an Android device. Supports ADB tunnel mode (default) and network mode (TCP control + UDP media). For network mode, set connection_mode='network' and provide device IP."
```

- 说明功能
- 说明参数用法
- 给出使用示例

#### ❌ 差的描述

```python
"description": "Connect to device"  # 太简略
```

### 4.4 参数规范

```python
# 必填参数
"device_id": {
    "type": "string",
    "description": "Device serial number or IP:port"
}

# 可选参数（有默认值）
"timeout": {
    "type": "integer",
    "default": 30,
    "description": "Timeout in seconds (default: 30)"
}

# 枚举参数
"codec": {
    "type": "string",
    "enum": ["auto", "h264", "h265", "av1"],
    "default": "auto",
    "description": "Video codec"
}
```

---

## 5. 错误处理规范

### 5.1 返回格式

所有工具返回**统一格式**的 JSON：

```python
# 成功
{
    "success": True,
    "data": {...},
    "message": "Operation completed"  # 可选
}

# 失败
{
    "success": False,
    "error": "Error description",
    "hint": "Suggested action"  # 可选，帮助用户解决
}
```

### 5.2 异常处理模板

```python
def call_tool(self, tool_name: str, arguments: Dict) -> Dict:
    with self._lock:
        try:
            # 工具处理逻辑
            result = self._do_something(arguments)
            return {"content": [{"type": "text", "text": json.dumps(result)}]}

        except Exception as e:
            logger.exception(f"Tool {tool_name} failed")
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": str(e),
                        "hint": "What to do next"  # 可选
                    }, ensure_ascii=False)
                }]
            }
```

### 5.3 常见错误提示

| 场景 | 错误信息 | 提示 |
|------|---------|------|
| 未连接设备 | "Not connected to device" | "Call discover_devices() first" |
| 设备未找到 | "Device not found: {id}" | "Check if device is on the same network" |
| 超时 | "Timeout after {n}s" | "Device may be sleeping, try wake_device()" |
| 权限不足 | "Permission denied" | "Enable USB debugging and grant permission" |

---

## 6. 日志规范

### 6.1 日志级别

| 级别 | 使用场景 | 示例 |
|------|---------|------|
| DEBUG | 详细调试信息 | `"Sent packet: <hex>"` |
| INFO | 正常操作流程 | `"Connected to device"` |
| WARNING | 可恢复的问题 | `"YADB installation failed, using fallback"` |
| ERROR | 操作失败 | `"Connection failed: timeout"` |
| EXCEPTION | 异常堆栈 | `logger.exception("Unexpected error")` |

### 6.2 日志格式

```python
# 配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# 使用
logger.info(f"[{tag}] Operation started")
logger.info(f"[ADB] Found: {model} ({serial})")
logger.warning(f"[UDP] Discovery failed: {error}")
logger.error(f"[YADB] Installation failed: {error}")
```

### 6.3 敏感信息

```python
# ❌ 不要记录敏感信息
logger.info(f"Password: {password}")

# ✅ 脱敏处理
logger.info(f"Authenticating with token: {token[:8]}...")
```

---

## 7. 状态管理规范

### 7.1 全局状态

```python
class ScrcpyMCPHandler:
    def __init__(self):
        self._client = None          # ScrcpyClient 实例
        self._server = None          # MCPServer 实例
        self._lock = threading.Lock()  # 线程锁（必须）
        self._current_config = None  # 当前 ClientConfig
```

### 7.2 线程安全

**所有状态访问必须在锁内进行**：

```python
def call_tool(self, tool_name: str, arguments: Dict) -> Dict:
    with self._lock:  # 获取锁
        # 所有状态操作都在这里
        if self._client is None:
            return {"error": "Not connected"}
        return self._server.call_tool(tool_name, arguments)
```

### 7.3 配置变更检测

```python
def _config_matches(self, **kwargs) -> bool:
    """检查新参数是否与当前配置匹配"""
    if not self._is_client_connected() or self._current_config is None:
        return False

    # 检查关键参数
    checks = [
        ('audio', kwargs.get('audio', False)),
        ('video', kwargs.get('video', True)),
        ('connection_mode', kwargs.get('connection_mode', 'adb_tunnel')),
    ]

    for key, expected in checks:
        if getattr(self._current_config, key, None) != expected:
            return False

    return True
```

---

## 8. 连接模式规范

### 8.1 模式对比

| 特性 | adb_tunnel | network |
|------|-----------|---------|
| 需要 ADB | ✅ 是 | ❌ 否 |
| 需要服务端 | ✅ 自动启动 | ✅ 手动启动 |
| 端口 | 单端口 (27183) | 多端口 (27184-27186) |
| 延迟 | 较高 | 较低 |
| 支持 stay-alive | ❌ | ✅ |

### 8.2 参数验证

```python
def _ensure_connected(self, **kwargs):
    connection_mode = kwargs.get('connection_mode', 'adb_tunnel')
    device_id = kwargs.get('device_id')

    # 网络模式必须有 IP
    if connection_mode == 'network' and not device_id:
        return None  # 或抛出异常
```

### 8.3 服务端部署流程

```
网络模式前置步骤:

1. ADB 连接设备 (USB 或 WiFi ADB)
2. 调用 push_server(push=True, start=True, stay_alive=True)
3. 等待服务端启动
4. 获取设备 IP
5. 调用 connect(connection_mode='network', device_id=IP)
```

---

## 9. 测试规范

### 9.1 测试用例结构

```python
# tests/test_mcp_server.py

import pytest
from scrcpy_http_mcp_server import ScrcpyMCPHandler

class TestScrcpyMCPHandler:

    def test_discover_devices_returns_list(self):
        """discover_devices 应返回设备列表"""
        handler = ScrcpyMCPHandler()
        result = handler.call_tool("discover_devices", {})
        assert "content" in result

    def test_connect_without_device_returns_error(self):
        """无设备时连接应返回错误"""
        handler = ScrcpyMCPHandler()
        result = handler.call_tool("connect", {"device_id": "invalid"})
        assert "error" in result or "success" in result
```

### 9.2 curl 测试命令

```bash
# 健康检查
curl http://127.0.0.1:3359/health

# 工具列表
curl -X POST http://127.0.0.1:3359/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# 扫描设备
curl -X POST http://127.0.0.1:3359/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"discover_devices"}}'
```

---

## 10. 常见陷阱

### 10.1 Python 布尔值

```python
# ❌ 错误：使用 JavaScript 风格
"default": true   # NameError: name 'true' is not defined
"default": false  # NameError: name 'false' is not defined

# ✅ 正确：使用 Python 布尔值
"default": True
"default": False
```

### 10.2 JSON 序列化

```python
# ❌ 错误：ensure_ascii=True 会破坏中文
json.dumps(result)

# ✅ 正确：保留中文字符
json.dumps(result, ensure_ascii=False)
```

### 10.3 异步/同步混用

```python
# ❌ 错误：在异步函数中直接返回同步结果
async def handle_request(request):
    return {"result": sync_operation()}

# ✅ 正确：使用 JSONResponse
async def handle_request(request):
    result = sync_operation()
    return JSONResponse({"result": result})
```

### 10.4 状态泄漏

```python
# ❌ 错误：使用全局变量
_client = None

# ✅ 正确：使用类封装
class ScrcpyMCPHandler:
    def __init__(self):
        self._client = None
```

### 10.5 资源未释放

```python
# ❌ 错误：socket 未关闭
sock = socket.socket(...)
sock.sendto(...)

# ✅ 正确：使用 try-finally
sock = socket.socket(...)
try:
    sock.sendto(...)
finally:
    sock.close()
```

### 10.6 图像格式混淆

```python
# ❌ 错误：假设帧是 BGR 格式
rgb = frame[:, :, ::-1]  # 会导致颜色错乱（蓝色变紫色）

# ✅ 正确：帧已经是 RGB 格式
q_img = QImage(frame.data, w, h, w * 3, QImage.Format_RGB888)
```

**重要**: 视频解码器输出的帧格式是 **RGB24**（不是 BGR）。
详见 `docs/development/DATA_FORMAT_CONVENTIONS.md`。

### 10.7 懒解码模式处理

```python
# ❌ 错误：直接从暂停的解码器获取帧
frame = decoder.get_frame()  # 永远返回 None

# ✅ 正确：先恢复解码器，再请求关键帧
client.enable_video()
client.reset_video()  # 请求关键帧
```

---

## 附录 Z: 必读文档

| 文档 | 说明 | 路径 |
|------|------|------|
| 数据格式约定 | RGB/BGR 格式、坐标系统等 | `docs/development/DATA_FORMAT_CONVENTIONS.md` |
| 协议规范 | 通信协议详细说明 | `docs/PROTOCOL_SPEC.md` |
| 预览窗口问题 | 分离进程 GUI、懒解码模式 | `docs/development/mcp_http_server/GUI_WINDOW_ISSUES.md` |

---

## 附录 A: 工具清单

| 分类 | 工具 | 需要连接 |
|------|------|---------|
| 连接 | connect, disconnect, push_server | ❌ |
| 发现 | discover_devices, list_devices | ❌ |
| ADB | get_device_ip, enable_wireless, connect_wireless | ❌ |
| 配置 | set_video, set_audio | ❌ |
| 状态 | get_state | ✅ |
| 屏幕 | screenshot, screenshot_device | ✅ |
| 触控 | tap, long_press, swipe | ✅ |
| 按键 | press_key, back, home, recent_apps, menu, enter, tab, escape | ✅ |
| D-Pad | dpad_up, dpad_down, dpad_left, dpad_right, dpad_center | ✅ |
| 面板 | expand_notification_panel, expand_settings_panel, collapse_panels | ✅ |
| 显示 | turn_screen_on, turn_screen_off, rotate_device, reset_video | ✅ |
| 音量 | volume_up, volume_down | ✅ |
| 电源 | wake_up | ✅ |
| 剪贴板 | get_clipboard, set_clipboard | ✅ |
| 文字 | input_text | ✅ |
| 应用 | list_apps, open_app | ✅ |
| 录音 | record_audio, stop_audio_recording, is_recording_audio, get_recording_duration | ✅ |

---

## 附录 B: 修改检查清单

在修改 `scrcpy_http_mcp_server.py` 前，请确认：

- [ ] 已阅读本文档相关章节
- [ ] 新工具定义在 `TOOLS` 列表中
- [ ] 布尔值使用 `True`/`False`（非 `true`/`false`）
- [ ] 错误返回包含 `success` 和 `error` 字段
- [ ] 日志使用正确的级别和格式
- [ ] 状态操作在 `self._lock` 内
- [ ] JSON 序列化使用 `ensure_ascii=False`
- [ ] 添加了必要的类型注解
- [ ] 测试通过

---

## 更新历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0 | 2026-02-17 | 初始版本 |
