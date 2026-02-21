# Python multiprocessing 最佳实践

> 本文档记录 multiprocessing 编程的经验教训，避免重复踩坑。
>
> **最后更新**: 2026-02-17

---

## 1. 核心问题：QueueFeederThread 阻塞进程退出

### 1.1 问题描述

使用 `multiprocessing.Queue` 后，即使调用了 `queue.close()`，进程仍然无法退出。

### 1.2 根本原因

```
multiprocessing.Queue 内部架构:

┌─────────────────────────────────────────────────────────┐
│                      主进程                              │
│  ┌──────────┐      ┌─────────────────────┐              │
│  │  Queue   │◄─────│ QueueFeederThread   │ ← 后台线程   │
│  │  对象    │      │ (负责 Pipe 通信)     │              │
│  └──────────┘      └─────────────────────┘              │
│       │                    │                             │
│       │            close() 时默认                        │
│       │            会 join() 等待                        │
│       │                    ▼                             │
│       │            ┌─────────────┐                       │
│       └───────────►│  可能阻塞!  │                       │
│                    └─────────────┘                       │
└─────────────────────────────────────────────────────────┘
```

`Queue.close()` 默认会调用 `join_thread()` 等待 feeder 线程结束。如果线程阻塞，进程就无法退出。

### 1.3 解决方案

```python
# ❌ 错误：只调用 close()
def cleanup_wrong(queue):
    if queue is not None:
        queue.close()  # 可能阻塞！
        queue = None

# ✅ 正确：先取消 join，再关闭
def cleanup_correct(queue):
    if queue is not None:
        try:
            queue.cancel_join_thread()  # 关键！取消等待
            queue.close()
        except Exception:
            pass
        finally:
            queue = None
```

---

## 2. 完整的 multiprocessing 资源清理模板

### 2.1 Queue 清理

```python
import multiprocessing as mp

def cleanup_queue(queue: mp.Queue) -> None:
    """
    安全清理 multiprocessing Queue。

    Args:
        queue: 要清理的 Queue 对象
    """
    if queue is None:
        return

    try:
        # 1. 取消 join（关键步骤！）
        queue.cancel_join_thread()

        # 2. 清空队列中的数据（可选）
        while not queue.empty():
            try:
                queue.get_nowait()
            except:
                break

        # 3. 关闭队列
        queue.close()

    except Exception as e:
        # 记录但不抛出异常
        import logging
        logging.debug(f"Queue cleanup error: {e}")

    finally:
        # 4. 清除引用
        queue = None
```

### 2.2 Process 清理

```python
def cleanup_process(process: mp.Process, timeout: float = 1.0) -> None:
    """
    安全终止 multiprocessing Process。

    Args:
        process: 要终止的进程
        timeout: 等待超时时间（秒）
    """
    if process is None:
        return

    try:
        if process.is_alive():
            # 1. 先尝试优雅停止
            # 如果有 stop_event，先设置
            # stop_event.set()

            # 2. 等待进程结束
            process.join(timeout=timeout)

            if process.is_alive():
                # 3. 超时后强制终止
                process.terminate()
                process.join(timeout=0.5)

                if process.is_alive():
                    # 4. 最后手段：kill
                    try:
                        process.kill()
                        process.join(timeout=0.5)
                    except:
                        pass

    except Exception as e:
        import logging
        logging.debug(f"Process cleanup error: {e}")

    finally:
        process = None
```

### 2.3 Event 清理

```python
def cleanup_event(event: mp.Event) -> None:
    """
    清理 multiprocessing Event。

    Args:
        event: 要清理的 Event 对象
    """
    if event is None:
        return

    try:
        # Event 通常不需要特殊清理
        # 但可以显式清除状态
        event.clear()
    except:
        pass
    finally:
        event = None
```

### 2.4 完整的资源管理类

```python
import multiprocessing as mp
import logging
from typing import Optional, List

class MultiprocessResourceManager:
    """
    multiprocessing 资源管理器。

    统一管理所有 multiprocessing 资源的生命周期。

    Example:
        with MultiprocessResourceManager() as manager:
            queue = manager.create_queue(maxsize=10)
            process = manager.create_process(target=worker, args=(queue,))
            process.start()
            # ... 使用资源 ...
        # 退出 with 块时自动清理所有资源
    """

    def __init__(self):
        self._queues: List[mp.Queue] = []
        self._processes: List[mp.Process] = []
        self._events: List[mp.Event] = []
        self._logger = logging.getLogger(__name__)

    def create_queue(self, maxsize: int = 0) -> mp.Queue:
        """创建并注册 Queue"""
        q = mp.Queue(maxsize=maxsize)
        self._queues.append(q)
        return q

    def create_process(self, target, args=(), kwargs=None, daemon: bool = True) -> mp.Process:
        """创建并注册 Process"""
        kwargs = kwargs or {}
        p = mp.Process(target=target, args=args, kwargs=kwargs, daemon=daemon)
        self._processes.append(p)
        return p

    def create_event(self) -> mp.Event:
        """创建并注册 Event"""
        e = mp.Event()
        self._events.append(e)
        return e

    def cleanup(self) -> None:
        """清理所有资源"""

        # 1. 先停止所有进程
        for p in reversed(self._processes):
            self._cleanup_process(p)
        self._processes.clear()

        # 2. 清理所有队列
        for q in reversed(self._queues):
            self._cleanup_queue(q)
        self._queues.clear()

        # 3. 清理所有事件
        for e in reversed(self._events):
            self._cleanup_event(e)
        self._events.clear()

        self._logger.debug("All multiprocessing resources cleaned up")

    def _cleanup_queue(self, queue: mp.Queue) -> None:
        if queue is None:
            return
        try:
            queue.cancel_join_thread()
            queue.close()
        except Exception as e:
            self._logger.debug(f"Queue cleanup error: {e}")

    def _cleanup_process(self, process: mp.Process) -> None:
        if process is None:
            return
        try:
            if process.is_alive():
                process.join(timeout=1.0)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=0.5)
        except Exception as e:
            self._logger.debug(f"Process cleanup error: {e}")

    def _cleanup_event(self, event: mp.Event) -> None:
        if event is None:
            return
        try:
            event.clear()
        except:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False
```

---

## 3. Windows vs Linux 差异

### 3.1 启动方法差异

| 平台 | 默认方法 | 说明 |
|------|---------|------|
| Linux | `fork` | 子进程是父进程的副本 |
| Windows | `spawn` | 创建全新进程，需要序列化 |
| macOS | `spawn` (Python 3.8+) | 同 Windows |

### 3.2 推荐做法

```python
import multiprocessing as mp

# 显式指定启动方法（跨平台一致）
mp.set_start_method('spawn', force=True)

# 或使用上下文
ctx = mp.get_context('spawn')
queue = ctx.Queue()
process = ctx.Process(target=worker, args=(queue,))
```

### 3.3 Windows 特有注意事项

```python
# ❌ Windows 上会出错：pickle 无法序列化
def process_with_lambda():
    # lambda 和嵌套函数无法 pickle
    p = mp.Process(target=lambda x: print(x), args=(1,))
    p.start()

# ✅ 正确：使用顶层函数
def worker_function(x):
    print(x)

def process_with_function():
    p = mp.Process(target=worker_function, args=(1,))
    p.start()

# ✅ 必须保护入口点
if __name__ == '__main__':
    # Windows 上必须这样保护
    process_with_function()
```

---

## 4. 常见陷阱清单

### 4.1 Queue 相关

| 陷阱 | 症状 | 解决方案 |
|------|------|---------|
| 只调用 `close()` | 进程无法退出 | 先 `cancel_join_thread()` |
| 队列满时 `put()` | 阻塞 | 使用 `put(timeout=...)` 或检查 `full()` |
| 队列空时 `get()` | 阻塞 | 使用 `get(timeout=...)` 或检查 `empty()` |
| 大对象传输慢 | 性能差 | 减少数据大小或使用 SharedMemory |

### 4.2 Process 相关

| 陷阱 | 症状 | 解决方案 |
|------|------|---------|
| daemon 进程创建子进程 | 子进程变成孤儿 | daemon 进程不要 fork |
| 不等待进程结束 | 僵尸进程 | 始终 `join()` 或 `terminate()` |
| 强制 `kill()` | 资源泄漏 | 先 `terminate()`，最后才 `kill()` |

### 4.3 调试技巧

```python
# 检查活跃线程
import threading
print(f"Active threads: {threading.active_count()}")
for t in threading.enumerate():
    print(f"  - {t.name} (daemon={t.daemon})")

# 检查活跃子进程
import multiprocessing as mp
print(f"Active children: {len(mp.active_children())}")
for p in mp.active_children():
    print(f"  - {p.name} (alive={p.is_alive()})")
```

---

## 5. 本次问题复盘

### 5.1 问题现象

开启实时预览后，MCP 服务器按 Ctrl+C 无法退出，需要强制关闭。

### 5.2 根本原因

`preview_process.py` 中的 `_cleanup()` 方法只调用了 `queue.close()`，没有调用 `cancel_join_thread()`，导致 `QueueFeederThread` 阻塞进程退出。

### 5.3 修复内容

```python
# 修复前
def _cleanup(self):
    if self._frame_queue is not None:
        self._frame_queue.close()
        self._frame_queue = None

# 修复后
def _cleanup(self):
    if self._frame_queue is not None:
        try:
            self._frame_queue.cancel_join_thread()  # 关键！
            self._frame_queue.close()
        except Exception:
            pass
        finally:
            self._frame_queue = None
```

### 5.4 教训总结

1. **显式资源管理**：不要依赖垃圾回收或隐式清理
2. **了解底层机制**：知道 Queue 使用后台线程
3. **跨平台测试**：Windows 和 Linux 行为可能不同
4. **防御性编程**：使用 try/finally 确保清理

---

## 6. 参考资料

- [Python multiprocessing 官方文档](https://docs.python.org/3/library/multiprocessing.html)
- [multiprocessing.Queue.cancel_join_thread()](https://docs.python.org/3/library/multiprocessing.html#multiprocessing.Queue.cancel_join_thread)
- [Python 并发编程最佳实践](https://realpython.com/python-multiprocessing/)
