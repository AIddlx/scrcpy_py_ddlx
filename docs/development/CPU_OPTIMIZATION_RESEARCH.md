# CPU 优化调查报告

**日期**: 2026-02-21
**状态**: 调查完成，重构待实现

---

## 问题现象

- 主窗口前台时 python.exe CPU 占用: **6~9%**
- 主窗口后台/最小化时 CPU 占用: **~1%**
- 预览窗口前台时 CPU 占用: **~9%**

---

## 调查过程

### 1. 初步排查

使用 `PROFILE_OPENGL=1` 启用内置性能监控：

```
OpenGL 性能报告（每帧耗时）:
array_contiguous: avg=0.002ms
consume:          avg=0.015ms
get_frame:        avg=0.003ms
glClear:          avg=0.091ms
paint_nv12:       avg=0.731ms
projection:       avg=0.054ms
tex_upload:       avg=0.310ms
总计 paintGL: 约 1.2ms/帧
```

**结论**: paintGL 总耗时只有 1.2ms，不足以解释 9% CPU。

### 2. Qt 基础开销测试

```
空 Qt + 16ms 定时器: 0.9% CPU
```

**结论**: Qt 本身开销很低，问题在其他地方。

### 3. QOpenGLWidget vs QOpenGLWindow 对比

| 渲染方式 | 单核 CPU | 说明 |
|---------|---------|------|
| QOpenGLWidget | **6.6%** | 当前使用，FBO 离屏渲染 |
| QOpenGLWindow | **0.5%** | 直接渲染到屏幕 |
| QWidget (软件) | ~0.5% | 软件渲染 + DWM 合成 |

**结论**: QOpenGLWidget 在 Windows 上有严重的性能问题。

---

## 根本原因

**QOpenGLWidget 使用 FBO（Framebuffer Object）进行离屏渲染**：

```
QOpenGLWidget 渲染流程:
GPU: OpenGL 渲染 → FBO (离屏纹理)
     ↓
CPU/GPU: FBO → 窗口表面 (Qt 内部合成)
     ↓
GPU: Windows DWM 合成显示
```

这层额外的 FBO 合成在 Windows 上开销很大。

**QOpenGLWindow 直接渲染到屏幕**，跳过 FBO，所以快得多。

---

## 解决方案

### 方案对比

| 方案 | CPU | 改动量 | 可行性 |
|------|-----|-------|-------|
| QOpenGLWindow 重构 | ~1% | 大 | ✓ 推荐 |
| QWidget 软件渲染 | ~0.5% | 中 | 可能增加延迟 |
| ANGLE | N/A | 小 | ✗ Qt 6 已移除 |

### 推荐方案: QOpenGLWindow 重构

**预期收益**: CPU 从 6-9% 降到 ~1%（降低 85%+）

**API 差异**:
```python
# QOpenGLWidget
class MyWidget(QOpenGLWidget):
    def paintGL(self):
        pass

# QOpenGLWindow
class MyWindow(QOpenGLWindow):
    def render(self):  # 注意方法名不同
        pass
```

**需要修改的文件**:
- `scrcpy_py_ddlx/core/player/video/opengl_widget.py`
- `scrcpy_py_ddlx/core/player/video/video_window.py`

---

## 测试文件

| 文件 | 用途 |
|------|------|
| `tests_gui/test_widget_cpu.py` | QOpenGLWidget CPU 测试 |
| `tests_gui/test_window_cpu.py` | QOpenGLWindow CPU 测试 |
| `tests_gui/check_opengl_hardware.py` | 检查 OpenGL 硬件加速状态 |

---

## 相关问题修复记录

详见 [WINDOW_RESIZE_FIXES_PREVIEW.md](WINDOW_RESIZE_FIXES_PREVIEW.md):

- #13: 预览窗口定时器间隔优化
- #14: NV12 渲染优化（2 纹理方案）
- #15: 主窗口定时器 + MSAA 优化
- #16: numpy 数组预分配优化
- #17: glTexImage2D → glTexSubImage2D 优化
- #18: 性能监控代码添加
- #19: QOpenGLWidget 性能问题（本次发现）

---

## 参考

- [QTBUG-57992: QOpenGLWidget performance issues on Windows](https://bugreports.qt.io/browse/QTBUG-57992)
- [Qt Documentation: QOpenGLWindow](https://doc.qt.io/qt-6/qopenglwindow.html)

---

**维护者**: 重构完成后更新本文档状态
