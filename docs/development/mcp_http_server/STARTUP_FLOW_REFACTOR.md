# 启动流程重构 - 网络模式命令行设计

> 日期：2026-03-02
> 原因：`--stay-alive` 语义不清晰，需要明确区分"推送驻留"和"复用驻留"
> 影响：命令行参数、参数验证、`on_startup` 函数

## 问题背景

### 原有问题

1. **`--stay-alive` 语义混乱**：
   - 既表示"推送驻留服务端"
   - 又表示"复用已驻留的服务端"
   - 用户无法区分这两种操作

2. **缺少终止驻留服务端的命令**：
   - 驻留服务端启动后，没有明确的终止命令
   - 用户只能通过手动 `adb shell` 命令终止

3. **参数组合无验证**：
   - `--adb --stay-alive --force` 这样的无效组合不会报错
   - 用户得不到明确的错误提示

### 设计目标

```
┌─────────────────────────────────────────────────────────────────┐
│                      命令行设计目标                              │
├─────────────────────────────────────────────────────────────────┤
│  1. 一次性模式 vs 驻留模式 → 明确区分                               │
│  2. 推送 vs 复用 vs 强制 → 独立控制                                │
│  3. 无效参数组合 → 友好错误提示                                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 新命令设计

### 命令一览

| 命令 | 说明 | USB | 网络连接 | 服务端生命周期 |
|------|------|-----|---------|---------------|
| `--adb` | USB ADB 隧道模式 | 需要 | ADB 转发 | 客户端断开后终止 |
| `--net` | 网络一次性模式 | 推送用 | TCP/UDP | 客户端断开后终止 |
| `--net --stay-alive` | 网络驻留模式 | 推送用* | TCP/UDP | 驻留在设备上 |
| `--net --stay-alive --force` | 强制推送驻留 | 需要 | TCP/UDP | 终止旧的，推送新的 |
| `--net <IP>` | 直接 TCP 连接 | 不需要 | TCP/UDP | 依赖已驻留的服务端 |
| `--stop-server` | 仅终止驻留服务端 | 需要 | 无 | 终止后退出 |

> *`--net --stay-alive` 优先尝试 UDP discovery 复用，无则通过 USB 推送

### 流程图

```
                         ┌──────────────────┐
                         │  --stop-server?  │
                         └────────┬─────────┘
                                  │ Yes
                                  ▼
                         ┌──────────────────┐
                         │   USB 检测设备    │
                         │   终止驻留服务端  │
                         │      退出        │
                         └──────────────────┘

                         │ No
                         ▼
              ┌───────────────────────────────────────┐
              │            --net 模式?                │
              └───────────────────┬───────────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │ No                │                   │ Yes
              ▼                   │                   ▼
     ┌─────────────────┐        │       ┌─────────────────────────────┐
     │   --adb 模式     │        │       │     指定了具体 IP?           │
     │  USB ADB 隧道   │        │       └─────────────┬───────────────┘
     └─────────────────┘        │                     │
                                  │           ┌─────────┴─────────┐
                                  │           │ Yes               │ No
                                  │           ▼                   ▼
                                  │   ┌──────────────┐   ┌──────────────────┐
                                  │   │  直接 TCP    │   │ --stay-alive?    │
                                  │   │  连接指定IP  │   └────────┬─────────┘
                                  │   └──────────────┘            │
                                  │                      ┌────────┴────────┐
                                  │                      │ Yes             │ No
                                  │                      ▼                 ▼
                                  │           ┌─────────────────┐  ┌─────────────┐
                                  │           │ --force?        │  │ USB 推送    │
                                  │           └────────┬───────┘  │ 一次性服务端 │
                                  │             ┌──────┴──────┐   └─────────────┘
                                  │             │ Yes    No   │
                                  │             ▼         ▼   │
                                  │   ┌────────────┐ ┌────────────────────┐
                                  │   │ USB 强制   │ │ 1. UDP Discovery  │
                                  │   │ 推送驻留   │ │ 2. 复用 或 USB推送 │
                                  │   └────────────┘ └────────────────────┘
```

---

## 参数验证规则

### 新增全局变量

```python
_force_push = False      # Force push new server (stop old first)
_stop_server_only = False  # Only stop server, don't connect
```

### 新增命令行参数

```python
parser.add_argument("--force", action="store_true", default=False,
                    help="Force push: stop old server and push new one (requires USB)")
parser.add_argument("--stop-server", action="store_true", default=False,
                    help="Stop persisted server on device and exit (requires USB)")
```

### 验证逻辑

```python
validation_errors = []

# --force 只能与 --net --stay-alive 组合使用
if args.force and not args.net:
    validation_errors.append("--force 只能与 --net --stay-alive 组合使用")

# --force 需要同时指定 --stay-alive
if args.force and not args.stay_alive:
    validation_errors.append("--force 需要同时指定 --stay-alive")

# --stop-server 不能与其他连接模式组合
if args.stop_server and (args.adb or args.net):
    validation_errors.append("--stop-server 不能与 --adb 或 --net 同时使用")

# --stay-alive 在 ADB 模式下无意义
if args.stay_alive and args.adb:
    validation_errors.append("--stay-alive 仅用于 --net 网络模式")

if validation_errors:
    # 显示错误和正确用法
    sys.exit(1)
```

---

## on_startup 函数重构

### 新结构

```python
async def on_startup():
    # 1. --stop-server 模式
    if _stop_server_only:
        # USB 检测 → 终止驻留服务端 → 退出
        ...

    if _network_push_device:
        # 2. 指定 IP 直接连接
        if _network_push_device != "auto":
            # 直接 TCP 连接
            ...

        # 3. --net --stay-alive --force (强制推送)
        if _stay_alive and _force_push:
            # USB 检测 → 终止旧服务端 → 推送新驻留服务端 → 网络连接
            ...

        # 4. --net --stay-alive (优先复用)
        if _stay_alive:
            # UDP Discovery → 发现则复用，无则 USB 推送
            ...

        # 5. --net (一次性模式)
        # USB 检测 → 推送非驻留服务端 → 网络连接
        ...

    elif _auto_connect:
        # 6. --adb 模式
        # USB ADB 隧道连接
        ...
```

### 关键改进

1. **情况分支明确**：每种命令组合对应一个明确的处理分支
2. **提前返回**：每种情况处理完成后立即 `return`，避免代码纠缠
3. **错误处理统一**：使用 `_exit_on_failure()` 统一失败处理

---

## Windows 控制台兼容性

### 问题

Windows 控制台默认使用 GBK 编码，无法显示 Unicode 特殊字符：

```
UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'
```

### 解决方案

替换所有 Unicode 符号为 ASCII 兼容字符：

| 原字符 | 替换为 |
|--------|--------|
| `✓` | `[OK]` |
| `✗` | `[X]` |
| `⚠` | `[!]` |
| `•` | `-` |

### 实施方法

```python
# 批量替换
edit(replace_all=True, old_string=" ✓", new_string="[OK]")
edit(replace_all=True, old_string=" ✗", new_string="[X]")
```

---

## ADB 模式组件信息显示

### 改进前

```
→ 初始化组件...
   视频解码器: h264 (1080x2400)
   音频解码器: opus
[OK]
```

### 改进后

```
→ 初始化组件...
   视频解码器: h264 (1080x2400)
   请求参数: 8M, 60fps
   音频解码器: opus
[OK]
```

### 代码

```python
# 获取实际使用的编码器（从 connect 结果）
actual_codec = result.get("codec", {}).get("name", _video_codec)
_video_codec_used = actual_codec

# 格式化码率
bitrate_val = _video_bitrate
if bitrate_val >= 1000000:
    bitrate_str = f"{bitrate_val // 1000000}M"
else:
    bitrate_str = f"{bitrate_val // 1000}K"

if _device_resolution:
    print_detail(f"视频解码器: {actual_codec} ({_device_resolution})")
print_detail(f"请求参数: {bitrate_str}, {_video_fps}fps")
```

---

## 已知问题

### Qt 线程错误

```
QObject: Cannot create children for a parent that is in a different thread.
(Parent is QLocalSocket(0x...), parent's thread is QThread(0x...), current thread is QThread(0x...))
```

**原因**：`QLocalSocket` 在一个线程创建，但在另一个线程被使用。

**状态**：待修复

**可能原因**：
1. 预览窗口在子进程创建，但 QLocalSocket 在主进程
2. 信号槽跨线程连接时对象所有权不清晰

---

## 测试验证

### 参数验证测试

```bash
# 无效组合应该报错
python scrcpy_http_mcp_server.py --adb --stay-alive --force
# 预期：显示参数错误，列出正确用法

python scrcpy_http_mcp_server.py --net --force
# 预期：显示 "--force 需要同时指定 --stay-alive"
```

### 功能测试

```bash
# 1. 网络一次性模式
python scrcpy_http_mcp_server.py --net --video --audio

# 2. 网络驻留模式（复用或推送）
python scrcpy_http_mcp_server.py --net --stay-alive --video --audio

# 3. 强制推送驻留服务端
python scrcpy_http_mcp_server.py --net --stay-alive --force --video --audio

# 4. 终止驻留服务端
python scrcpy_http_mcp_server.py --stop-server

# 5. 指定 IP 连接
python scrcpy_http_mcp_server.py --net 192.168.5.4 --video
```

---

## 文件变更

| 文件 | 变更 |
|------|------|
| `scrcpy_http_mcp_server.py` | 参数验证、启动流程重构、Unicode 替换 |
| `docs/development/mcp_http_server/STARTUP_FLOW_REFACTOR.md` | 新增本文档 |

---

## 经验总结

1. **命令设计要明确无歧义**：每个命令应该只对应一种行为
2. **参数组合验证很重要**：无效组合应该给出明确的错误提示
3. **流程图帮助理解**：复杂的条件分支用流程图可视化
4. **Windows 兼容性**：避免 Unicode 特殊字符，使用 ASCII
5. **函数内全局变量**：使用 `global` 声明确保修改生效

---

*此文档记录启动流程重构的设计决策和实现经验。*
