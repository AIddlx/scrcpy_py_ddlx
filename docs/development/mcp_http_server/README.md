# HTTP MCP Server 文档

本文件夹包含 `scrcpy_http_mcp_server.py` 的完整开发文档。

---

## 文档列表

| 文档 | 说明 | 优先级 |
|------|------|--------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | **架构与流程** - 技术栈、系统架构、数据流、设计决策 | ⭐⭐ 必读 |
| [GAP_ANALYSIS.md](GAP_ANALYSIS.md) | **差距分析** - 当前实现与理想设计的差距 | ⭐⭐ 必读 |
| [MCP_HTTP_SERVER_SPEC.md](MCP_HTTP_SERVER_SPEC.md) | **开发规范** - 编码规范、工具定义、错误处理 | ⭐⭐ 必读 |
| [GUI_WINDOW_ISSUES.md](GUI_WINDOW_ISSUES.md) | **预览窗口** - 分离进程 GUI 实现 | ⭐⭐ 必读 |
| [STARTUP_FLOW_REFACTOR.md](STARTUP_FLOW_REFACTOR.md) | **启动流程重构** - 网络模式命令行设计、参数验证 | ⭐⭐ 必读 |
| [SCREENSHOT_AUDIO_MODES.md](SCREENSHOT_AUDIO_MODES.md) | **截图录音** - 两种模式说明 | ⭐ 参考 |
| [MCP_HTTP_SERVER_LOGIC.md](MCP_HTTP_SERVER_LOGIC.md) | **工作逻辑** - 请求处理、连接管理、状态控制 | ⭐ 参考 |
| [MCP_HTTP_SERVER_UPGRADE.md](MCP_HTTP_SERVER_UPGRADE.md) | **升级计划** - 功能改进和优化计划 | 参考 |

---

## 跨项目通用文档

| 文档 | 说明 |
|------|------|
| [DATA_FORMAT_CONVENTIONS.md](../DATA_FORMAT_CONVENTIONS.md) | **数据格式约定** - RGB/BGR 格式、坐标系统、控制消息格式 |

---

## 文档用途

```
┌─────────────────────────────────────────────────────────────┐
│                    什么时候看哪个文档？                       │
├─────────────────────────────────────────────────────────────┤
│  理解整体框架、技术选型、数据流    →  ARCHITECTURE.md        │
│  了解当前问题、规划改进           →  GAP_ANALYSIS.md        │
│  修改代码、添加功能、修复bug      →  SPEC.md                │
│  理解预览窗口实现                →  GUI_WINDOW_ISSUES.md   │
│  理解截图录音模式                →  SCREENSHOT_AUDIO_MODES.md │
│  调试问题、理解执行流程          →  LOGIC.md                │
│  规划新功能、改进现有功能        →  UPGRADE.md              │
└─────────────────────────────────────────────────────────────┘
```

---

## 新功能: 分离进程预览窗口

### 使用方法

```cmd
# 1. 连接设备 (开启视频)
curl -X POST http://localhost:3359/mcp -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"connect\",\"arguments\":{\"video\":true}}}"

# 2. 启动预览窗口
curl -X POST http://localhost:3359/mcp -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"start_preview\"}}"

# 3. 停止预览
curl -X POST http://localhost:3359/mcp -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"stop_preview\"}}"
```

### 架构

```
HTTP MCP Server (主进程)
    │
    ├── Frame Sender Thread
    │       │
    │       ▼
    │   multiprocessing.Queue
    │       │
    │       ▼
    └── Preview Process (子进程)
            │
            └── Qt Window (独立事件循环)
```

---

## 快速开始

### 1. 新人入门

```bash
# 第一步：理解架构
docs/development/mcp_http_server/ARCHITECTURE.md

# 第二步：学习规范
docs/development/mcp_http_server/MCP_HTTP_SERVER_SPEC.md
```

### 2. 修改代码前

```bash
# 阅读编码规范和常见陷阱
docs/development/mcp_http_server/MCP_HTTP_SERVER_SPEC.md
```

### 3. 启动服务器

```bash
python scrcpy_http_mcp_server.py
```

---

## 文件位置

```
scrcpy-py-ddlx/
├── scrcpy_http_mcp_server.py          # 主文件
└── docs/
    └── development/
        └── mcp_http_server/           # 📁 本文件夹
            ├── README.md              # 本文件
            ├── ARCHITECTURE.md        # 架构与流程
            ├── MCP_HTTP_SERVER_SPEC.md    # 开发规范
            ├── MCP_HTTP_SERVER_LOGIC.md   # 工作逻辑
            └── MCP_HTTP_SERVER_UPGRADE.md # 升级计划
```

---

## 常见任务指引

### 添加新工具

1. 阅读 `ARCHITECTURE.md` → 理解工具执行路径
2. 阅读 `SPEC.md` → 第 4 章「工具定义规范」
3. 在 `TOOLS` 列表中添加定义
4. 在 `call_tool()` 中添加处理逻辑

### 修复错误

1. 阅读 `LOGIC.md` → 理解请求处理流程
2. 阅读 `SPEC.md` → 第 5 章「错误处理规范」
3. 确保返回格式包含 `success` 和 `error` 字段

### 添加日志

1. 阅读 `SPEC.md` → 第 6 章「日志规范」
2. 使用正确的日志级别和格式

### 理解连接模式

1. 阅读 `ARCHITECTURE.md` → 第 5 章「连接流程」
2. 阅读 `ARCHITECTURE.md` → 第 4.2 节「设备连接数据流」
