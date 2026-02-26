# MCP 文件传输 API 文档

> **状态**: ✅ 已完成  
> **日期**: 2026-02-26

---

## 概述

scrcpy-py-ddlx 通过 MCP (Model Context Protocol) 提供文件传输功能，支持两种模式：

| 模式 | 实现方式 | 适用场景 |
|------|----------|----------|
| **ADB 模式** | `adb push/pull/shell` 命令 | USB 连接、ADB 隧道 |
| **网络模式** | 独立 TCP 文件通道 (第4条 socket) | WiFi 直连、远程控制 |

---

## 架构

### ADB 模式架构
```
┌─────────────┐      ADB Tunnel       ┌─────────────┐
│      PC     │ ◄──────────────────► │   Android   │
│             │   adb push/pull/shell │             │
└─────────────┘                       └─────────────┘
```

### 网络模式架构
```
┌─────────────┐                        ┌─────────────┐
│      PC     │ ◄── TCP 27184 ──────► │   Android   │
│   (客户端)   │     (控制通道)          │   (服务端)   │
│             │                        │             │
│             │ ◄── UDP 27185 ──────► │             │
│             │     (视频流)            │             │
│             │                        │             │
│             │ ◄── UDP 27186 ──────► │             │
│             │     (音频流)            │             │
│             │                        │             │
│             │ ◄── TCP 27187 ──────► │             │
│             │     (文件通道)          │             │
└─────────────┘                        └─────────────┘
```

---

## 端口配置

| 端口 | 用途 | 协议 | 说明 |
|------|------|------|------|
| 27183 | UDP 发现/唤醒 | UDP | 服务端发现和唤醒 |
| 27184 | 控制通道 | TCP | 触摸、按键等控制消息 |
| 27185 | 视频流 | UDP | H.264/H.265 视频数据 |
| 27186 | 音频流 | UDP | Opus/AAC 音频数据 |
| 27187 | 文件传输 | TCP | 文件操作通道 |

---

## 使用方法

### ADB 模式

```bash
# 启动 ADB 模式连接
python scrcpy_http_mcp_server.py --connect --audio --audio-dup --preview

# 文件操作自动使用 adb 命令
```

### 网络模式

```bash
# 方式1: 使用 --network-push (推荐)
python scrcpy_http_mcp_server.py --network-push 192.168.5.4 --audio --audio-dup

# 方式2: 分步操作
# 1. 先通过 USB 推送服务器
python scrcpy_http_mcp_server.py --push-server --stay-alive

# 2. 断开 USB，通过网络连接
python scrcpy_http_mcp_server.py --connect --audio --audio-dup --connection-mode network --host 192.168.5.4
```

---

## MCP 工具 API

### list_dir - 列出目录

**请求：**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "list_dir",
    "arguments": {
      "path": "/sdcard"
    }
  }
}
```

**响应：**
```json
{
  "success": true,
  "path": "/sdcard",
  "entries": [
    {"name": "Download", "type": "directory", "size": 4096, "mtime": 1234567890},
    {"name": "test.png", "type": "file", "size": 12345, "mtime": 1234567890}
  ],
  "count": 2,
  "mode": "network"
}
```

### file_stat - 获取文件信息

**请求：**
```json
{
  "name": "file_stat",
  "arguments": {
    "device_path": "/sdcard/test.png"
  }
}
```

**响应：**
```json
{
  "success": true,
  "exists": true,
  "path": "/sdcard/test.png",
  "type": "file",
  "size": 12345,
  "mtime": 1234567890,
  "mode": "network"
}
```

### push_file - 上传文件

**请求：**
```json
{
  "name": "push_file",
  "arguments": {
    "local_path": "./local_file.txt",
    "device_path": "/sdcard/remote_file.txt"
  }
}
```

**响应：**
```json
{
  "success": true,
  "local_path": "./local_file.txt",
  "device_path": "/sdcard/remote_file.txt",
  "size": 1234,
  "mode": "network"
}
```

### pull_file - 下载文件

**请求：**
```json
{
  "name": "pull_file",
  "arguments": {
    "device_path": "/sdcard/remote_file.txt",
    "local_path": "./local_file.txt"
  }
}
```

**响应：**
```json
{
  "success": true,
  "device_path": "/sdcard/remote_file.txt",
  "local_path": "./local_file.txt",
  "size": 1234,
  "mode": "network"
}
```

### make_dir - 创建目录

**请求：**
```json
{
  "name": "make_dir",
  "arguments": {
    "device_path": "/sdcard/new_directory"
  }
}
```

**响应：**
```json
{
  "success": true,
  "device_path": "/sdcard/new_directory",
  "mode": "network"
}
```

### delete_file - 删除文件或目录

**请求：**
```json
{
  "name": "delete_file",
  "arguments": {
    "device_path": "/sdcard/file_or_directory"
  }
}
```

**响应：**
```json
{
  "success": true,
  "device_path": "/sdcard/file_or_directory",
  "mode": "network"
}
```

---

## 技术实现

### ADB 模式 (file_ops.py)

| 操作 | ADB 命令 |
|------|----------|
| list_dir | `adb shell ls -laL <path>` |
| stat | `adb shell stat <path>` |
| push | `adb push <local> <remote>` |
| pull | `adb pull <remote> <local>` |
| delete | `adb shell rm -rf <path>` |
| mkdir | `adb shell mkdir -p <path>` |

### 网络模式 (file_channel.py)

**帧格式：**
```
[cmd: 1 byte][length: 4 bytes][payload: N bytes]
```

**命令类型：**

| cmd | 名称 | 说明 |
|-----|------|------|
| 0x00 | LIST | 列出目录 |
| 0x01 | PULL | 下载文件 |
| 0x02 | PUSH | 上传文件头 |
| 0x03 | PUSH_DATA | 上传数据块 |
| 0x04 | DELETE | 删除文件 |
| 0x05 | MKDIR | 创建目录 |
| 0x06 | STAT | 获取文件信息 |
| 0x07 | PULL_DATA | 下载数据块 |
| 0x7F | ERROR | 错误响应 |

**连接流程：**
1. PC 端创建 TCP socket 监听 27187 端口
2. Android 服务端启动后主动连接 PC 的 27187 端口
3. PC 端在后台线程中 accept 连接（非阻塞）
4. 连接建立后，PC 端启动 FileChannel 处理文件请求

---

## 测试

### 运行测试脚本

```bash
# ADB 模式测试
test_file_transfer.bat

# 网络模式测试
test_file_transfer_network_only.bat
```

### 预期结果

```
================================================================
  网络模式文件传输测试 (TCP File Channel: 27187)
================================================================

========== 1. list_dir ==========
[PASS] list_dir - 网络模式

========== 2. file_stat ==========
[PASS] file_stat - 网络模式

========== 3. make_dir ==========
[PASS] make_dir - 网络模式

========== 4. push_file ==========
[PASS] push_file - 网络模式

========== 5. pull_file ==========
[PASS] pull_file - 网络模式

========== 6. delete_file ==========
[PASS] delete_file - 网络模式

========== 7. delete_dir ==========
[PASS] delete_dir - 网络模式

================================================================
  结果: 7/7 通过 (必须全部显示 "网络模式")
================================================================
SUCCESS - 网络模式文件通道工作正常!
```

---

## 故障排除

### 问题：网络模式下文件操作失败

**可能原因：**
1. 服务端未连接到文件 socket
2. 防火墙阻止了 27187 端口

**解决方案：**
```bash
# 检查端口是否被占用
netstat -an | grep 27187

# 检查防火墙规则
```

### 问题：画面卡死

**原因：** 旧版本中 `accept_file_connection` 是阻塞调用

**解决方案：** 确保使用最新版本，文件 socket accept 在后台线程中执行

### 问题：ADB 模式文件操作失败

**解决方案：**
```bash
# 检查设备连接
adb devices

# 重启 ADB 服务
adb kill-server
adb start-server
```

---

## 文件结构

```
scrcpy_py_ddlx/
├── core/
│   └── file/
│       ├── file_ops.py        # ADB 模式文件操作
│       ├── file_channel.py    # 网络模式文件通道客户端
│       └── file_commands.py   # 文件命令常量
│
├── client/
│   ├── client.py              # 客户端主类 (文件操作入口)
│   └── connection.py          # 连接管理 (文件 socket)
│
└── mcp_server.py              # MCP 服务器 (文件工具定义)

scrcpy/server/src/main/java/com/genymobile/scrcpy/
├── device/
│   └── DesktopConnection.java # 服务端连接管理 (文件 socket)
│
└── file/
    ├── FileServer.java        # ADB 模式文件服务器
    ├── FileChannelHandler.java # 网络模式文件处理器
    └── FileCommands.java      # 文件命令常量
```

---

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0 | 2026-02-26 | 初始实现，支持 ADB 和网络模式 |
| 1.1 | 2026-02-26 | 修复网络模式画面卡死问题（后台线程 accept） |
