# 重构安全保障指南

**版本**: 1.0
**日期**: 2026-02-21
**目的**: 确保重构过程中不会破坏现有功能，提供可追溯的变更记录

---

## 一、核心原则

### 1.1 不可破坏的契约

以下接口**必须保持向后兼容**，任何修改都需要：
1. 先更新本文档
2. 创建迁移计划
3. 同时更新所有调用方

```
┌─────────────────────────────────────────────────────────────────┐
│                    不可破坏的接口契约                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  DelayBuffer ──────────────────────────────────────────────────│
│  ├── consume() -> Optional[Frame]                              │
│  └── push(frame)                                               │
│                                                                 │
│  OpenGLVideoWidget ────────────────────────────────────────────│
│  ├── set_delay_buffer(delay_buffer)                            │
│  ├── set_control_queue(queue)                                  │
│  ├── set_consume_callback(callback)                            │
│  ├── set_frame_size_changed_callback(callback)                 │
│  ├── set_nv12_mode(enabled) -> bool                            │
│  └── is_nv12_supported() -> bool                               │
│                                                                 │
│  VideoWindow / OpenGLVideoWindow ──────────────────────────────│
│  ├── set_device_info(name, width, height)                      │
│  ├── update_frame(frame)                                       │
│  ├── set_control_queue(queue)                                  │
│  ├── set_delay_buffer(delay_buffer)                            │
│  └── video_widget (property)                                   │
│                                                                 │
│  InputHandler / CoordinateMapper ──────────────────────────────│
│  ├── set_control_queue(queue)                                  │
│  ├── get_device_coords(x, y, widget_size, device_size)         │
│  └── 所有鼠标/键盘事件处理方法                                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 数据流不变性

```
┌───────────────┐    ┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│  VideoDecoder │───►│  DelayBuffer  │───►│ VideoWidget   │───►│   Display     │
│  (生产者)      │    │  (缓冲区)     │    │ (消费者)      │    │  (屏幕)       │
└───────────────┘    └───────────────┘    └───────────────┘    └───────────────┘
       │                    │                    │
       ▼                    ▼                    ▼
  VideoPacket          consume()            paintGL/render
  (H264/H265)          返回 Frame           OpenGL渲染
```

**不变性保证**：
1. Decoder 生产的帧格式（RGB/NV12）必须被 Widget 正确处理
2. DelayBuffer 的 `consume()` 必须是线程安全的
3. Widget 的渲染方法必须能处理 None 帧

---

## 二、重构前必做检查清单

### 2.1 文档准备

```
□ 已阅读本文档（REFACTOR_SAFETY_GUIDE.md）
□ 已阅读相关组件文档
□ 已绘制变更影响图
□ 已创建变更日志文件（CHANGELOG_REFRACT_*.md）
```

### 2.2 代码分析

```
□ 已列出所有受影响的文件
□ 已识别所有调用方
□ 已识别所有被调用方
□ 已标记不可破坏的接口
□ 已识别隐式依赖（全局变量、单例等）
```

### 2.3 测试准备

```
□ 已确认现有测试通过
□ 已为变更部分编写新测试
□ 已准备回滚方案
□ 已创建备份 tag
```

---

## 三、变更影响矩阵

### 3.1 组件依赖关系

| 组件 | 被谁依赖 | 依赖谁 | 修改风险 |
|------|----------|--------|----------|
| `OpenGLVideoWidget` | VideoWindow, PreviewWindow, PreviewProcess | DelayBuffer, InputHandler, CoordinateMapper | **高** |
| `VideoWindow` | client/components.py, factory.py | OpenGLVideoWidget | 中 |
| `DelayBuffer` | VideoDecoder, VideoWidget | Frame | **高** |
| `InputHandler` | VideoWidget | ControlMessage | 中 |
| `CoordinateMapper` | VideoWidget | 无 | 低 |
| `factory.py` | components.py, tests | VideoWindow | 低 |

### 3.2 修改风险等级

| 风险等级 | 定义 | 需要的操作 |
|----------|------|------------|
| **高** | 被多个组件依赖，接口变化会级联影响 | 全面的回归测试 + 多人审查 |
| 中 | 被1-2个组件依赖 | 针对性测试 + 审查 |
| 低 | 内部实现，不影响外部接口 | 自测即可 |

---

## 四、QOpenGLWindow 重构专项

### 4.1 变更范围

```
┌─────────────────────────────────────────────────────────────────┐
│                     QOpenGLWindow 重构范围                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  核心变更文件：                                                  │
│  ├── scrcpy_py_ddlx/core/player/video/opengl_widget.py         │
│  │   └── OpenGLVideoWidget → OpenGLVideoWindow (QOpenGLWindow) │
│  └── scrcpy_py_ddlx/core/player/video/video_window.py          │
│      └── OpenGLVideoWindow 容器逻辑调整                          │
│                                                                 │
│  可能影响文件：                                                  │
│  ├── scrcpy_py_ddlx/core/player/video/factory.py               │
│  ├── scrcpy_py_ddlx/gui/preview_window.py                       │
│  ├── scrcpy_py_ddlx/preview_process.py                          │
│  └── tests_gui/*.py                                             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 API 差异对照

| 功能 | QOpenGLWidget | QOpenGLWindow |
|------|---------------|---------------|
| 渲染方法 | `paintGL()` | `render()` |
| 初始化 | `initializeGL()` | `initialize()` |
| 尺寸变化 | `resizeGL(w, h)` | `resize(w, h)` |
| 父类 | QWidget | QWindow |
| 嵌入方式 | 直接嵌入布局 | 需要 QWidget::createWindowContainer() |
| 上下文获取 | `self.context()` | `self.openglContext()` |

### 4.3 必须保持不变的接口

```python
# 以下方法签名必须保持不变
class OpenGLVideoWidgetBase:
    def set_delay_buffer(self, delay_buffer: 'DelayBuffer') -> None: ...
    def set_control_queue(self, queue: Queue) -> None: ...
    def set_consume_callback(self, callback: Callable) -> None: ...
    def set_frame_size_changed_callback(self, callback: Callable) -> None: ...
    def set_nv12_mode(self, enabled: bool) -> bool: ...
    def is_nv12_supported(self) -> bool: ...

# 以下属性必须存在
@property
def video_widget(self) -> 'OpenGLVideoWidgetBase': ...
```

### 4.4 测试清单

```
□ 基础渲染测试
  ├── 空帧渲染不崩溃
  ├── RGB 帧正确显示
  ├── NV12 帧正确显示
  └── 尺寸变化正确处理

□ 功能测试
  ├── 延迟追踪正常
  ├── 控制消息正常
  ├── 鼠标事件映射正确
  ├── 键盘事件映射正确
  └── 旋转切换正常

□ 性能测试
  ├── CPU 占用 < 2% (单核)
  ├── 无内存泄漏
  └── 无 OpenGL 错误

□ 集成测试
  ├── 主窗口正常工作
  ├── 预览窗口正常工作
  └── ADB tunnel 模式正常
```

---

## 五、变更日志模板

每次修改创建新文件：`docs/development/changelog/REFACTOR_YYYYMMDD_简述.md`

```markdown
# 变更日志：[简述]

**日期**: YYYY-MM-DD
**类型**: 重构 / 修复 / 新功能
**风险等级**: 高 / 中 / 低

---

## 变更原因
[为什么要做这个变更]

## 变更内容

### 修改的文件
| 文件 | 修改内容 |
|------|----------|
| path/to/file.py | 描述 |

### 接口变更
| 接口 | 旧签名 | 新签名 | 影响 |
|------|--------|--------|------|
| method | old | new | 调用方 |

## 影响分析
- 直接影响：[哪些组件]
- 间接影响：[可能波及的范围]

## 测试结果
- [ ] 单元测试通过
- [ ] 集成测试通过
- [ ] 手动测试通过

## 回滚方案
[如何恢复到变更前状态]

## 相关文档
- [链接到相关文档]
```

---

## 六、回滚策略

### 6.1 备份策略

每次重大变更前执行：

```bash
# 创建备份 tag
python scripts/local_backup.py before_xxx_refactor

# 或手动创建
git tag -a "backup_$(date +%Y%m%d_%H%M%S)" -m "Backup before xxx refactor"
```

### 6.2 回滚步骤

```bash
# 查看所有备份 tag
git tag -l "backup_*"

# 回滚到指定备份
git checkout <tag_name>

# 如果已经 push，创建回滚 commit
git revert <commit_hash>
```

### 6.3 分支策略

对于大型重构：

```bash
# 创建重构分支
git checkout -b refactor/qopenglwindow

# 定期合并主分支保持同步
git fetch origin
git merge origin/main

# 完成后合并回主分支
git checkout main
git merge refactor/qopenglwindow
```

---

## 七、持续保障机制

### 7.1 每日检查

```
□ 运行所有测试
□ 检查是否有新的依赖
□ 更新变更日志
```

### 7.2 每周检查

```
□ 审查变更日志完整性
□ 检查接口契约是否有意外变化
□ 确认文档与代码同步
```

### 7.3 重构完成后

```
□ 更新本文档的组件依赖关系
□ 确认所有测试通过
□ 更新 API 文档
□ 通知所有相关开发者
```

---

## 八、常见错误和预防

### 8.1 错误类型

| 错误 | 表现 | 预防措施 |
|------|------|----------|
| 接口签名变化 | 调用方报错 | 使用 ABC 定义接口 |
| 隐式依赖丢失 | 运行时错误 | 显式传递所有依赖 |
| 线程安全问题 | 偶发崩溃 | 所有跨线程访问加锁 |
| 资源泄漏 | 内存/句柄增长 | 使用 with 或 finally |
| 事件循环冲突 | 卡死或崩溃 | 单线程处理 Qt 事件 |

### 8.2 代码审查要点

```
□ 接口签名是否变化
□ 是否有新的全局变量/单例
□ 线程安全是否考虑
□ 资源是否正确释放
□ 异常是否正确处理
□ 日志是否足够
□ 文档是否更新
```

---

## 九、文档索引

| 文档 | 用途 |
|------|------|
| [WINDOW_RESIZE_FIXES.md](WINDOW_RESIZE_FIXES.md) | 窗口缩放问题修复索引 |
| [WINDOW_RESIZE_FIXES_PREVIEW.md](WINDOW_RESIZE_FIXES_PREVIEW.md) | 预览窗口修复详情 |
| [CPU_OPTIMIZATION_RESEARCH.md](CPU_OPTIMIZATION_RESEARCH.md) | CPU 优化调查报告 |
| [NETWORK_PIPELINE.md](NETWORK_PIPELINE.md) | 网络管道设计 |
| [VIDEO_AUDIO_PIPELINE.md](VIDEO_AUDIO_PIPELINE.md) | 音视频管道设计 |
| [PROTOCOL_CHANGE_CHECKLIST.md](PROTOCOL_CHANGE_CHECKLIST.md) | 协议修改检查清单 |

---

**维护者**: 每次重大变更后更新本文档
