# 变更日志：QOpenGLWindow 重构

**日期**: 2026-02-21
**类型**: 重构
**风险等级**: 高
**状态**: 规划中

---

## 变更原因

当前使用 `QOpenGLWidget` 进行视频渲染，在 Windows 平台上 CPU 占用过高（6-9%）。

**根本原因**：QOpenGLWidget 使用 FBO（Framebuffer Object）进行离屏渲染，这层额外的合成在 Windows 上开销很大。

**解决方案**：改用 `QOpenGLWindow`，直接渲染到屏幕，预期 CPU 降至 ~1%。

详细调查报告：[CPU_OPTIMIZATION_RESEARCH.md](../CPU_OPTIMIZATION_RESEARCH.md)

---

## 性能对比

| 渲染方式 | CPU (单核) | 说明 |
|---------|-----------|------|
| QOpenGLWidget (当前) | 6.6% | FBO 离屏渲染 |
| QOpenGLWindow (目标) | 0.5% | 直接渲染 |
| QWidget (软件) | ~0.5% | 软件渲染 |

**预期收益**：CPU 降低 85%+

---

## 变更内容

### 修改的文件

| 文件 | 修改内容 | 风险 |
|------|----------|------|
| `core/player/video/opengl_widget.py` | 重命名/重构为 QOpenGLWindow | 高 |
| `core/player/video/video_window.py` | 调整容器逻辑 | 中 |
| `core/player/video/factory.py` | 更新创建逻辑 | 低 |
| `gui/preview_window.py` | 适配新接口 | 中 |
| `preview_process.py` | 适配新接口 | 中 |

### API 差异

| 功能 | QOpenGLWidget (旧) | QOpenGLWindow (新) |
|------|-------------------|-------------------|
| 渲染方法 | `paintGL()` | `render()` |
| 初始化 | `initializeGL()` | `initialize()` |
| 尺寸变化 | `resizeGL(w, h)` | `resize(w, h)` |
| 父类 | QWidget | QWindow |
| 嵌入方式 | 直接嵌入布局 | `QWidget.createWindowContainer()` |
| 上下文 | `self.context()` | `self.openglContext()` |

### 接口变更

**保持不变**（必须遵守）：

```python
# 以下方法签名必须保持不变
def set_delay_buffer(self, delay_buffer): ...
def set_control_queue(self, queue): ...
def set_consume_callback(self, callback): ...
def set_frame_size_changed_callback(self, callback): ...
def set_nv12_mode(self, enabled) -> bool: ...
def is_nv12_supported(self) -> bool: ...

# 以下属性必须保持
@property
def device_width(self) -> int: ...
@property
def device_height(self) -> int: ...
```

**内部变更**（不影响调用方）：

```python
# 旧 (QOpenGLWidget)
class OpenGLVideoWidget(QOpenGLWidget, InputHandler, CoordinateMapper):
    def paintGL(self): ...
    def initializeGL(self): ...
    def resizeGL(self, w, h): ...

# 新 (QOpenGLWindow)
class OpenGLVideoWindow(QOpenGLWindow):
    def render(self): ...
    def initialize(self): ...
    def resize(self, w, h): ...
```

---

## 影响分析

### 直接影响

```
OpenGLVideoWidget
    │
    ├── OpenGLVideoWindow (video_window.py)
    │   └── factory.py
    │       └── client/components.py
    │           └── 用户代码
    │
    └── PreviewWindow (gui/preview_window.py)
        └── GUI 应用

    └── PreviewProcess (preview_process.py)
        └── 独立进程预览
```

### 间接影响

- 窗口事件处理可能略有差异
- 嵌入方式变化可能影响布局
- 多显示器场景可能需要测试

### 不受影响

- 数据接收和解析（demuxer）
- 解码流程（decoder）
- 控制消息处理
- 网络通信
- 录制功能

---

## 实施步骤

### Phase 1: 准备

- [x] 完成 CPU 优化调查
- [x] 创建备份 tag
- [x] 推送到阿里云效仓库
- [x] 创建接口契约文档
- [ ] 创建重构分支

### Phase 2: 核心重构

- [ ] 创建 `opengl_window.py` 新文件
- [ ] 实现 QOpenGLWindow 版本
- [ ] 保持所有接口签名不变
- [ ] 本地测试通过

### Phase 3: 集成

- [ ] 更新 `video_window.py`
- [ ] 更新 `factory.py`
- [ ] 更新 `preview_window.py`
- [ ] 更新 `preview_process.py`
- [ ] 运行所有测试

### Phase 4: 验证

- [ ] 主窗口功能测试
- [ ] 预览窗口功能测试
- [ ] 旋转场景测试
- [ ] CPU 占用验证 (< 2%)
- [ ] 长时间运行测试 (内存泄漏)

### Phase 5: 清理

- [ ] 移除旧代码
- [ ] 更新文档
- [ ] 合并到主分支
- [ ] 创建新 tag

---

## 测试清单

### 功能测试

```
□ 主窗口基础
  ├── 窗口正常显示
  ├── 帧正常渲染
  ├── 窗口缩放正常
  └── 宽高比保持正确

□ 旋转场景
  ├── 竖屏 → 横屏
  ├── 横屏 → 竖屏
  ├── 连续多次旋转
  └── 旋转后尺寸正确

□ 输入处理
  ├── 鼠标点击
  ├── 鼠标拖动
  ├── 鼠标滚轮
  ├── 键盘输入
  └── 中文输入

□ 预览窗口
  ├── 独立进程模式
  ├── GUI 模式
  └── 切换正常
```

### 性能测试

```
□ CPU 占用
  ├── 前台窗口 < 2%
  ├── 后台窗口 < 1%
  └── 无异常峰值

□ 内存
  ├── 无泄漏（30分钟运行）
  └── 峰值 < 200MB

□ 渲染
  ├── 帧率稳定 (~60fps)
  └── 无撕裂/卡顿
```

### 兼容性测试

```
□ Windows 10/11
□ macOS (如果可用)
□ 不同分辨率
□ 多显示器
```

---

## 回滚方案

### 方案 1: Git Revert

```bash
# 查找重构开始的 commit
git log --oneline

# 回滚整个重构
git revert <start_commit>..<end_commit>
```

### 方案 2: 恢复到备份 Tag

```bash
# 查看备份
git tag -l "backup_*"

# 恢复
git checkout backup_20260221_before_qopenglwindow
```

### 方案 3: 分支切换

```bash
# 如果在单独分支重构，直接切回 main
git checkout main
```

---

## 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| 接口不兼容导致调用失败 | 中 | 高 | 严格遵循接口契约 |
| 某些平台不支持 | 低 | 中 | 保留 QOpenGLWidget 作为 fallback |
| 性能改进不如预期 | 低 | 中 | 已有测试验证 |
| 嵌入问题导致布局异常 | 中 | 中 | 充分测试各种布局 |

---

## 相关文档

- [CPU_OPTIMIZATION_RESEARCH.md](../CPU_OPTIMIZATION_RESEARCH.md) - 性能调查报告
- [REFACTOR_SAFETY_GUIDE.md](../REFACTOR_SAFETY_GUIDE.md) - 重构安全保障指南
- [INTERFACE_CONTRACTS.md](../INTERFACE_CONTRACTS.md) - 接口契约定义
- [WINDOW_RESIZE_FIXES.md](../WINDOW_RESIZE_FIXES.md) - 窗口缩放修复记录

---

## 变更历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-02-21 | 0.1 | 初始规划 |

---

**维护者**: 重构过程中持续更新本文档
