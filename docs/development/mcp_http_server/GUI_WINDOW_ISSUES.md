# GUI 实时预览窗口问题分析

## 当前实现 (已解决)

### 分离进程 GUI 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      分离进程架构                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  HTTP MCP Server (主进程)                                       │
│  ├── ScrcpyMCPHandler                                           │
│  ├── PreviewManager                                             │
│  └── Frame Sender Thread                                        │
│           │                                                      │
│           │ multiprocessing.Queue (帧数据)                      │
│           ▼                                                      │
│  Preview Process (子进程)                                        │
│  ├── QApplication (独立事件循环)                                │
│  └── PreviewWindow (Qt 窗口)                                    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 新增工具

| 工具 | 说明 |
|------|------|
| `start_preview` | 启动分离进程预览窗口 |
| `stop_preview` | 停止预览窗口 |
| `get_preview_status` | 获取预览状态 |

### 使用方法

```cmd
# 1. 连接设备 (开启视频)
curl -X POST http://localhost:3359/mcp -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"connect\",\"arguments\":{\"video\":true}}}"

# 2. 启动预览窗口
curl -X POST http://localhost:3359/mcp -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"start_preview\"}}"

# 3. 检查预览状态
curl -X POST http://localhost:3359/mcp -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"get_preview_status\"}}"

# 4. 停止预览
curl -X POST http://localhost:3359/mcp -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"stop_preview\"}}"
```

---

## 技术实现

### 文件结构

```
scrcpy_py_ddlx/
├── preview_process.py          # 分离进程预览实现
│   ├── preview_window_process() # 进程入口函数
│   └── PreviewManager          # 管理类
│
└── scrcpy_http_mcp_server.py   # MCP 服务器
    └── ScrcpyMCPHandler
        ├── _preview_manager    # PreviewManager 实例
        ├── _start_frame_sender() # 帧发送线程
        └── start_preview/stop_preview 处理
```

### 关键代码

```python
# PreviewManager 使用示例
from scrcpy_py_ddlx.preview_process import PreviewManager

manager = PreviewManager()

# 启动预览
manager.start("Device Name", 1080, 2400)

# 发送帧
manager.send_frame(frame)  # numpy array

# 停止预览
manager.stop()
```

---

## 解决的问题

| 问题 | 解决方案 |
|------|---------|
| Qt 与 HTTP 事件循环冲突 | 分离进程，各自运行独立事件循环 |
| 关闭窗口导致崩溃 | 子进程独立，崩溃不影响主服务 |
| 灵活开关 | start_preview / stop_preview 工具 |
| 帧数据传递 | multiprocessing.Queue |
| 懒解码模式预览黑屏 | start_preview 时自动恢复解码器 |
| 关键帧缺失 | start_preview 时自动请求关键帧 |
| 关闭预览后 CPU 占用高 | 自动恢复懒解码模式 |

---

## 懒解码模式 (Lazy Decode Mode)

### 概述

当 `show_window=False` 且 `lazy_decode=True` (默认) 时，视频解码器会暂停以节省 CPU。
这在需要后台运行但不显示视频时非常有用。

### start_preview 处理流程

```
1. 检查视频是否已暂停 (_video_enabled=False)
2. 如果暂停:
   a. 调用 enable_video() 恢复解码器
   b. 调用 reset_video() 请求关键帧
   c. 记录 _preview_was_lazy = True
3. 创建 PreviewManager 并启动子进程
4. 启动帧发送线程
```

### stop_preview 处理流程

```
1. 停止 PreviewManager (终止子进程)
2. 如果 _preview_was_lazy=True:
   a. 调用 disable_video() 暂停解码器
   b. 恢复懒解码模式以节省 CPU
```

### 关键帧处理

恢复解码器后，解码器可能收到 P 帧（预测帧）而缺少参考帧。
调用 `reset_video()` 发送 RESET_VIDEO 控制消息，请求服务器发送新的 I 帧（关键帧）。

---

## 历史问题 (已解决)

### 1. Qt 事件循环与 HTTP 服务器冲突

```
问题: 两个事件循环无法在同一线程运行
解决: 分离进程，各自独立运行
```

### 2. 灵活开关窗口的 Bug

| Bug | 原因 | 解决 |
|-----|------|------|
| 不渲染 | Qt 事件循环未运行 | 子进程运行独立 Qt 循环 |
| 持续渲染 | 关闭窗口后解码器仍在写帧 | 子进程终止时自动停止 |
| 阻塞 | Qt 事件循环阻塞 HTTP | 分离进程无阻塞 |
| 关闭崩溃 | 资源清理顺序问题 | 子进程独立资源管理 |
