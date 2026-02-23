# 预览窗口跨进程事件驱动优化计划

## 文档索引

| 文档 | 说明 |
|------|------|
| [PLAN.md](PLAN.md) | 本文档 - 总体计划 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 架构设计详解 |
| [IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md) | 实现过程记录 |
| [TEST_RESULTS.md](TEST_RESULTS.md) | 测试结果 |

---

## 1. 问题分析

### 1.1 当前状态

| 组件 | 架构 | CPU 占用 | 触发方式 |
|------|------|----------|----------|
| 主窗口 (OpenGLVideoWindow) | 同进程 | ~1.5% | Signal 事件驱动 |
| MCP 预览窗口 (preview_process) | 独立进程 | ~8% | 16ms 定时器轮询 |

### 1.2 问题根因

```
预览进程 ──┬── 16ms QTimer ──→ _update_frame() ──→ read_frame_ex() ──→ render()
           │        ↓
           │   每秒 62.5 次中断
           │   即使没有新帧也要执行
           │
           └── 无法使用 Signal（跨进程）
```

**为什么必须用独立进程？**
- MCP 服务器使用 uvicorn (asyncio)
- Qt GUI 必须在主线程运行
- 同进程会导致 GIL 竞争，影响解码/网络性能

### 1.3 CPU 占用分解

| 操作 | 频率 | 单次耗时 | CPU 占用 |
|------|------|----------|----------|
| QTimer 中断 | 62.5/s | ~0.5ms | ~3% |
| read_frame_ex() 调用 | 62.5/s | ~0.3ms | ~2% |
| Qt 事件循环 | 持续 | - | ~1-2% |
| 实际渲染 | 60/s | ~1.2ms | ~1.5% |
| **总计** | - | - | **~8%** |

---

## 2. 目标

### 2.1 性能目标

| 指标 | 当前 | 目标 |
|------|------|------|
| 空闲 CPU（无帧） | ~6% | ~0.1% |
| 活跃 CPU（60fps） | ~8% | ~1.5-2% |
| 帧延迟 | ~16ms 轮询间隔 | 事件触发，<1ms |

### 2.2 功能目标

- ✅ 保持预览进程独立（避免 GIL 竞争）
- ✅ 实现事件驱动渲染
- ✅ 兼容现有 API
- ✅ 支持 Windows/Linux/macOS

---

## 3. 解决方案设计

### 3.1 核心思路

使用 **Pipe + QSocketNotifier** 实现跨进程事件通知：

```
┌─────────────────────────────────────────────────────────────────┐
│                        改进后架构                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────┐         ┌─────────────────────┐       │
│  │   MCP 服务器进程     │         │   预览进程 (独立)     │       │
│  │                     │         │                     │       │
│  │  send_frame()       │         │  QSocketNotifier    │       │
│  │       │             │         │       │             │       │
│  │       ├─→ SHM 写帧  │         │       │ (零CPU等待)  │       │
│  │       │             │         │       ↓             │       │
│  │       └─→ Pipe 写1字节┼─────────┼→ activated()       │       │
│  │                     │  跨进程  │       │             │       │
│  │                     │  通知   │       ├─→ 读 SHM    │       │
│  │                     │         │       └─→ render()  │       │
│  └─────────────────────┘         └─────────────────────┘       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 技术选型

| 方案 | 优点 | 缺点 | 选择 |
|------|------|------|------|
| socketpair + QSocketNotifier | 跨平台 | Windows 需特殊处理 | ✅ (Linux/macOS) |
| QWinEventNotifier + Win32 Event | Windows 最优 (~0.3μs) | 仅 Windows | ✅ (Windows) |
| multiprocessing.Event + 短轮询 | Python 原生 | 1-2ms 延迟 | ⚠️ 备选 |

**最终选择**：**平台分离策略**
- **Windows**: `QWinEventNotifier` + `win32event`（延迟 ~0.3μs）
- **Linux/macOS**: `socketpair` + `QSocketNotifier`（延迟 ~1μs）

**Agent 研究结论**（2026-02-23）：
1. `socketpair` 在 Windows 上使用 TCP loopback，可行但性能略差
2. `QWinEventNotifier` 是 Windows 上最优方案，延迟仅 ~0.3μs
3. 需要 `pywin32` 依赖（Windows）

### 3.3 数据流

```
                    ┌──────────────────────────────────┐
                    │           主进程                  │
                    │  ┌────────────────────────────┐  │
                    │  │  send_frame(frame)         │  │
                    │  │       │                    │  │
                    │  │       ├─→ SimpleSHMWriter  │  │
                    │  │       │     .write_frame() │  │
                    │  │       │                    │  │
                    │  │       └─→ notify_socket    │  │
                    │  │             .send(b'1')    │  │
                    │  └────────────────────────────┘  │
                    └──────────────────────────────────┘
                                     │
                         socketpair  │
                                     ↓
                    ┌──────────────────────────────────┐
                    │           预览进程                │
                    │  ┌────────────────────────────┐  │
                    │  │  QSocketNotifier           │  │
                    │  │       │                    │  │
                    │  │       ├─→ activated        │  │
                    │  │       │                    │  │
                    │  │       ├─→ notify.recv(1)   │  │
                    │  │       │                    │  │
                    │  │       ├─→ SHM.read_frame() │  │
                    │  │       │                    │  │
                    │  │       └─→ trigger_render() │  │
                    │  └────────────────────────────┘  │
                    └──────────────────────────────────┘
```

---

## 4. 实现计划

### 4.1 阶段一：基础设施（预计 2 小时）

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1.1 | `preview_process.py` | PreviewManager 添加 socketpair |
| 1.2 | `preview_process.py` | 传递 socket 给预览进程 |
| 1.3 | `preview_process.py` | 预览进程添加 QSocketNotifier |

### 4.2 阶段二：事件驱动（预计 1 小时）

| 步骤 | 文件 | 改动 |
|------|------|------|
| 2.1 | `preview_process.py` | 移除 16ms 定时器 |
| 2.2 | `preview_process.py` | QSocketNotifier 回调中读取帧 |
| 2.3 | `preview_process.py` | send_frame() 发送通知 |

### 4.3 阶段三：测试验证（预计 1 小时）

| 步骤 | 内容 |
|------|------|
| 3.1 | 单元测试：Pipe 通知机制 |
| 3.2 | 集成测试：MCP 预览窗口 |
| 3.3 | 性能测试：CPU 占用率对比 |

### 4.4 阶段四：文档更新（预计 0.5 小时）

| 步骤 | 文档 |
|------|------|
| 4.1 | 更新实现记录 |
| 4.2 | 记录测试结果 |
| 4.3 | 更新 CLAUDE.md |

---

## 5. 风险评估

### 5.1 技术风险

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| QSocketNotifier 在 Windows 上性能差 | 低 | 高 | 测试验证，备选方案 |
| socketpair 跨进程传递复杂 | 中 | 中 | 使用 fd 传递或 recreate |
| 通知丢失导致帧丢失 | 低 | 高 | SHM counter 校验 |

### 5.2 回滚计划

如果新架构有问题，可以快速回滚：
- 保留原有 16ms 定时器代码（注释）
- 通过配置开关切换新旧模式

---

## 6. 验收标准

### 6.1 性能验收

| 指标 | 验收标准 | 测试方法 |
|------|----------|----------|
| 空闲 CPU | < 0.5% | 任务管理器，无帧时 |
| 活跃 CPU | < 2.5% | 任务管理器，60fps 时 |
| 帧延迟 | < 5ms 增加 | E2E 延迟对比 |

### 6.2 功能验收

- [ ] MCP `--network` 模式预览正常
- [ ] MCP `--connect --preview` 模式预览正常
- [ ] 窗口旋转正常
- [ ] 长时间运行稳定（>1 小时）
- [ ] 多次启动/关闭无泄漏

---

## 7. 时间线

| 阶段 | 预计时间 | 状态 |
|------|----------|------|
| 计划文档 | 0.5h | ✅ 完成 |
| 阶段一：基础设施 | 2h | ⏳ 待开始 |
| 阶段二：事件驱动 | 1h | ⏳ 待开始 |
| 阶段三：测试验证 | 1h | ⏳ 待开始 |
| 阶段四：文档更新 | 0.5h | ⏳ 待开始 |
| **总计** | **5h** | - |

---

## 8. 参考文档

- [QSocketNotifier 文档](https://doc.qt.io/qt-6/qsocketnotifier.html)
- [multiprocessing.Pipe 文档](https://docs.python.org/3/library/multiprocessing.html#pipes-and-queues)
- [socket.socketpair 文档](https://docs.python.org/3/library/socket.html#socket.socketpair)

---

**创建日期**: 2026-02-23
**最后更新**: 2026-02-23
**负责人**: Claude AI
