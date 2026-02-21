# 窗口缩放问题修复记录 - 索引

本文档是窗口缩放问题的**索引和简述**，详细内容请参阅各专题文档。

> **规范文档**：参见 [WINDOW_RESIZE_DESIGN.md](WINDOW_RESIZE_DESIGN.md)

---

## 文档索引

| 文档 | 内容 |
|------|------|
| [WINDOW_RESIZE_FIXES.md](WINDOW_RESIZE_FIXES.md) | 本索引 + 主窗口修复（#1-#8） |
| [WINDOW_RESIZE_FIXES_PREVIEW.md](WINDOW_RESIZE_FIXES_PREVIEW.md) | 预览窗口 + CPU 优化修复（#9-#19） |
| [CPU_OPTIMIZATION_RESEARCH.md](CPU_OPTIMIZATION_RESEARCH.md) | CPU 优化调查报告（QOpenGLWidget 问题） |

---

## 主窗口修复（#1-#8）

### #1：重复回调导致窗口调整混乱 ✅
**原因**：帧尺寸变化回调被设置了两次
**修复**：移除 `components.py` 中的重复回调

### #2：resizeEvent 中强制纠正窗口大小 ✅
**原因**：`resizeEvent` 中调用 `self.resize()` 导致"锁定"效果
**修复**：移除 `resizeEvent` 中的 `resize()` 调用

### #3：两个同名 resizeEvent 方法互相覆盖 ✅
**原因**：Python 中后定义的方法覆盖前面的
**修复**：合并为单个方法

### #4：第二次旋转后帧尺寸检测失败 ✅
**原因**：`_reinitialize_decoder` 使用旧的 width/height
**修复**：重置 `_width`, `_height` 为 0

### #5：服务端旋转时不发送新的尺寸信息 ✅
**原因**：服务端只在首次发送 video header
**修复**：每次尺寸变化都发送 video header

### #6：窗口缩放时宽高比保持不正确 ✅
**原因**：使用 oldSize 判断用户意图不可靠
**修复**：使用相对变化比率 + QTimer 延迟调整

### #7：旋转后手动调整被跳过 ✅
**原因**：`_skip_resize_count = 5` 太多
**修复**：改为 1

### #8：PyAV codec context close 错误 ✅
**原因**：`VideoCodecContext` 没有 `close()` 方法
**修复**：直接设置为 `None`

---

## 预览窗口修复（#9-#17）

详见：[WINDOW_RESIZE_FIXES_PREVIEW.md](WINDOW_RESIZE_FIXES_PREVIEW.md)

### #9：预览窗口横竖屏切换失败 ✅
**原因**：数据流链路缺失帧尺寸信息
**修复**：添加 width/height 到 FrameWithMetadata、SimpleSHM

### #10：预览窗口检测到旋转但不调整窗口大小 ✅
**原因**：只更新变量，没调用 `set_device_size()` 方法
**修复**：调用完整方法

### #11：ADB tunnel 模式下 E2E 延迟始终为 0ms ✅
**原因**：`StreamingVideoDemuxer` 没有设置 `packet_id`
**修复**：添加 latency tracking

### #12：macOS 硬件解码器支持不完整 ✅
**原因**：缺少 VideoToolbox 解码器
**修复**：添加平台特定的解码器优先级

### #13：预览窗口 CPU 占用过高（定时器） ✅
**原因**：1ms 定时器间隔太高频
**修复**：改为 16ms

### #14：预览窗口 NV12 渲染 CPU 占用高 ✅
**原因**：每帧 CPU 分离 U/V 分量
**修复**：2 纹理方案（Y + UV），GPU shader 处理

### #15：主窗口 OpenGL 定时器 + MSAA 不必要 ✅
**原因**：4ms 定时器 + 4x MSAA
**修复**：16ms 定时器，禁用 MSAA

### #16：numpy 数组分配导致 CPU 占用过高 ✅
**原因**：每帧 `np.ascontiguousarray` 创建新数组（~320MB/s）
**修复**：预分配缓冲区 + `np.copyto()` 复用

### #17：OpenGL 每帧重新分配纹理导致 CPU 高占用 ✅
**原因**：`_paint_nv12` 每帧调用 `glTexImage2D` 而非 `glTexSubImage2D`
**修复**：添加尺寸追踪，只在尺寸变化时重新分配纹理

### #18：性能监控代码添加 ✅
**原因**：需要精确监控各操作耗时，而非猜测
**修复**：添加 `PROFILE_OPENGL=1` 环境变量启用性能监控

### #19：QOpenGLWidget 在 Windows 上性能问题 ⚠️
**原因**：QOpenGLWidget 使用 FBO 离屏渲染，Windows 上开销大
**发现**：QOpenGLWidget 68% CPU vs QOpenGLWindow 7% CPU
**状态**：待决定方案

---

## 设计教训总结

### 数据流中的信息丢失

```
┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐
│  组件A   │ → │  组件B   │ → │  组件C   │ → │  组件D   │
│ 有信息X  │   │ 信息X丢失 │   │ 不知道X  │   │ 需要X   │
└─────────┘   └─────────┘   └─────────┘   └─────────┘
```

### 教训清单

| # | 教训 | 适用场景 |
|---|------|---------|
| 1 | **追踪完整数据流**：确保每个组件获得需要的信息 | 新功能设计 |
| 2 | **不要假设尺寸上限**：使用足够大的默认值（如 4096） | 涉及尺寸的配置 |
| 3 | **元数据必须传递**：width/height/format 要和 frame 一起传递 | 数据结构设计 |
| 4 | **NV12 没有 shape**：必须显式传递尺寸 | NV12 相关代码 |
| 5 | **避免 CPU 图像处理**：让 GPU shader 处理格式转换 | 性能优化 |
| 6 | **定时器间隔要合理**：匹配实际数据产生频率 | 事件驱动设计 |
| 7 | **调用完整方法**：不要重复部分逻辑 | 代码复用 |
| 8 | **跨平台检查**：确保所有平台支持 | 新功能开发 |
| 9 | **避免循环内内存分配**：高频调用中创建大数组代价巨大 | 性能优化 |
| 10 | **预分配 + 复用**：经典的性能优化模式 | 高频循环代码 |
| 11 | **工具验证假设**：直觉可能错，用 profiler 验证 | 性能调试 |
| 12 | **glTexImage2D vs glTexSubImage2D**：前者重新分配内存，后者只更新数据 | OpenGL 渲染 |
| 13 | **前后台 CPU 差异**：后台窗口 OS 可能跳过渲染，但代码本身应高效 | 性能分析 |

### 检查清单：修改数据流时

```
□ 画出完整的数据流图
□ 标出每个组件需要什么信息
□ 验证信息是否在传递中丢失
□ 检查是否有隐含的尺寸假设
□ 考虑横竖屏切换场景
□ 考虑跨平台兼容性
```

---

## 待修复问题

| 问题 | 优先级 | 状态 |
|------|--------|------|
| QMutex 锁没有使用 try/finally | 中 | ⏳ |
| NV12 渲染失败时没有回退方案 | 中 | ⏳ |
| 解码器重初始化的错误恢复不完善 | 中 | ⏳ |

---

**文档版本**: 2.1
**更新日期**: 2026-02-21
**维护者**: 发现新问题或修复问题后，创建专题文档并更新本索引
