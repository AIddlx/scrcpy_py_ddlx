# PyAV GPU帧自动传输问题

## 文档信息
- **创建时间**: 2026-02-19
- **更新时间**: 2026-02-19
- **问题类型**: 架构限制（已在新版本解决）
- **影响**: 无法实现零拷贝GPU渲染

---

## 重大发现（2026-02-19）

**PyAV源码已经支持零拷贝！** 通过 `HWAccel(is_hw_owned=True)` 参数。

### 相关Commit
- **Commit**: `085b4d291c403a6c24b8df1c38eeecbfa6bea164`
- **日期**: 2026-02-08
- **标题**: Preserving hardware memory during hw decoding, exporting/importing via dlpack (#2155)
- **状态**: **未发布**（在v9.2.0之后）

### 源码位置
`av/video/codeccontext.py` 第130-132行：
```python
if self.hwaccel_ctx.is_hw_owned:
    cython.cast(VideoFrame, frame)._device_id = self.hwaccel_ctx.device_id
    return frame  # 直接返回GPU帧，不传输到CPU！
```

### 实现零拷贝的代码
```python
from av.codec.hwaccel import HWAccel

# 关键：is_hw_owned=True 禁用自动传输
hwaccel = HWAccel(
    device_type='cuda',
    is_hw_owned=True,  # ← 这是关键！
    allow_software_fallback=False
)

container = av.open('video.mp4', hwaccel=hwaccel)
for packet in container.demux(video=0):
    for frame in packet.decode():
        # frame.format.name == 'cuda' (在GPU显存中)
        # 通过DLPack零拷贝导出到CuPy/PyTorch
        import cupy as cp
        y_gpu = cp.fromDlpack(frame.planes[0].__dlpack__())
```

---

## 问题发现（原始记录）

### 背景
尝试实现"全GPU零拷贝"链路：
```
GPU解码 → GPU显存 → GPU CSC → GPU渲染
```

### 现象
使用PyAV的HWAccel时，解码后的帧格式确实是`cuda`（在GPU），但PyAV会**自动**把帧传输到CPU。

```python
from av.codec.hwaccel import HWAccel
hw = HWAccel(device_type='cuda', allow_software_fallback=True)
ctx = av.CodecContext.create('hevc', 'r', hwaccel=hw)

print(f'pix_fmt: {ctx.pix_fmt}')  # 输出: cuda ✓
print(f'is_hwaccel: {ctx.is_hwaccel}')  # 输出: True ✓
```

### 根因
PyAV源码 `av/video/codeccontext.pyx` 中的 `_transfer_hwframe` 方法：

```python
cdef _transfer_hwframe(self, Frame frame):
    if self.hwaccel_ctx is None:
        return frame

    # ... 检查是否需要传输 ...

    cdef Frame frame_sw
    frame_sw = self._alloc_next_frame()

    # 这里！自动把GPU帧传到CPU！
    err_check(lib.av_hwframe_transfer_data(frame_sw.ptr, frame.ptr, 0))

    frame_sw.pts = frame.pts
    return frame_sw  # 返回CPU帧，不是GPU帧！
```

调用链：
```
decode() → _send_packet_and_recv() → _recv_frame()
    → avcodec_receive_frame()  [GPU帧]
    → _transfer_hwframe()      [自动传输到CPU]
    → 返回CPU帧
```

### 结论
- **PyAV设计决策**：自动将GPU帧传输到CPU，让Python代码可以访问
- **无法绕过**：`_transfer_hwframe`是Cython编译代码，Python层面无法拦截
- **帧格式确认**：`pix_fmt: cuda` 说明GPU帧确实存在，但被自动传输

---

## 实际数据流对比

### 当前方案（hevc_cuvid）
```
GPU解码 → 自动输出NV12(CPU) → numpy操作(CPU) → 上传GPU → 渲染
   3ms        0ms(解码器内置)       3-5ms         2-5ms     <1ms
                           总延迟: ~10-15ms
```

### HWAccel方案
```
GPU解码 → GPU帧 → 自动传输到CPU → numpy操作 → 上传GPU → 渲染
   3ms     0ms       2-5ms(PCIe)     3-5ms      2-5ms    <1ms
                           总延迟: ~12-18ms (反而更慢!)
```

**HWAccel方案反而更慢**，因为增加了额外的GPU→CPU传输。

---

## 零拷贝方案可行性分析

| 方案 | 可行性 | 复杂度 | 跨平台 | 说明 |
|------|--------|--------|--------|------|
| 修改PyAV源码 | ✅ | 中 | ✅ | 删除`_transfer_hwframe`传输代码，重新编译PyAV |
| ctypes直接调FFmpeg | ✅ | 高 | ⚠️ | 完全绕过PyAV，需要自己处理所有细节 |
| CUDA-OpenGL Interop | ✅ | 高 | ❌ | 仅NVIDIA GPU，需要专用代码 |
| Vaapi-EGL Interop | ✅ | 高 | ❌ | 仅Linux/Intel |
| D3D11-OpenGL Interop | ✅ | 高 | ❌ | 仅Windows |

### 推荐方案（长期）
修改PyAV源码，添加一个选项来控制是否自动传输：
```python
# 理想API
hw = HWAccel(device_type='cuda', auto_transfer=False)
```

---

## 当前决策

**回退到 hevc_cuvid 方案**：
- hevc_cuvid解码器内部处理GPU→CPU传输，更高效
- 避免PyAV HWAccel的额外开销
- PC端延迟已经很低（~8-10ms），不是主要瓶颈

**主要瓶颈在**：
- 设备端编码：~20-40ms
- 网络传输：~6-10ms
- Qt渲染延迟：~16-30ms

---

## 参考资料

- PyAV源码: https://github.com/PyAV-Org/PyAV/blob/master/av/video/codeccontext.pyx
- FFmpeg hwaccel: https://ffmpeg.org/ffmpeg.html#Hardware-Acceleration
- FFmpeg hwmap: https://ffmpeg.org/ffmpeg-filters.html#hwmap

---

## 附录：日志证据

```
2026-02-19 21:38:29,937 - [HW_DEBUG] Original frame format: nv12, is_hw=False, planes=2
```

说明：使用hevec_cuvid时，解码后帧格式已经是`nv12`（CPU），`is_hw=False`确认不是硬件帧。

使用HWAccel时：
```
pix_fmt: cuda
is_hwaccel: True
```
但帧仍会被自动传输到CPU。

---

## 实现全GPU零拷贝的方案

### 步骤1：从源码安装最新PyAV

```bash
# 克隆最新源码
git clone https://github.com/PyAV-Org/PyAV.git
cd PyAV

# 安装依赖
pip install cython numpy

# 编译安装（需要FFmpeg开发库）
pip install -e .

# Windows可能需要指定FFmpeg路径
# set FFMPEG_ROOT=C:\path\to\ffmpeg
# pip install -e .
```

### 步骤2：使用is_hw_owned=True

```python
import av
from av.codec.hwaccel import HWAccel

# 创建GPU解码器，帧保留在GPU
hwaccel = HWAccel(
    device_type='cuda',
    device=0,  # GPU设备ID
    is_hw_owned=True,  # 关键：禁用GPU→CPU传输
    allow_software_fallback=False
)

# 解码
ctx = av.CodecContext.create('hevc', 'r', hwaccel=hwaccel)
ctx.width = 1920
ctx.height = 1080

for packet in packets:
    for frame in ctx.decode(packet):
        print(f"Frame format: {frame.format.name}")  # 输出: cuda
        # 帧在GPU显存中，可以通过DLPack导出
```

### 步骤3：通过DLPack连接OpenGL

```python
import cupy as cp
from OpenGL.GL import *

# 从PyAV帧导出到CuPy（零拷贝）
y_gpu = cp.fromDlpack(frame.planes[0].__dlpack__())
uv_gpu = cp.fromDlpack(frame.planes[1].__dlpack__())

# CuPy → OpenGL纹理（需要CUDA-OpenGL Interop）
# 这部分需要使用CUDA的图形互操作API
import cuda.cuda as cuda
import cuda.cudart as cudart

# 注册OpenGL纹理
cudaGraphicsGLRegisterImage(...)

# 映射CUDA资源
cudaGraphicsMapResources(...)

# 复制数据（GPU内部，零拷贝）
cudaMemcpy2D(...)

# 解除映射
cudaGraphicsUnmapResources(...)
```

### 理想数据流

```
┌─────────────────────────────────────────────────────────────────┐
│                    全GPU零拷贝链路                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  hevc + HWAccel(is_hw_owned=True)                               │
│                                                                 │
│  GPU解码 → GPU显存(cuda格式) → DLPack → CuPy → OpenGL纹理       │
│    3ms        0ms(无传输)      0ms      0ms      <1ms           │
│                                                                 │
│  总延迟: ~4-5ms (vs 当前 ~10-15ms)                              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 当前限制

1. **PyAV版本**：需要从源码编译，尚未发布到PyPI ✅ **已解决：从源码编译成功**
2. **DLPack → OpenGL**：需要CUDA-OpenGL Interop，代码复杂
3. **跨平台**：CUDA方案仅支持NVIDIA GPU

### 编译成功的版本信息

```
PyAV version: 17.0.0pre
is_hw_owned: True
pix_fmt: cuda
```

### 优先级建议

| 优先级 | 方案 | 说明 |
|--------|------|------|
| **高** | 等待PyAV发布新版本 | `is_hw_owned`功能已经merge |
| **中** | 从源码编译PyAV | 立即可用，但需要编译环境 |
| **已实现** | CUDA-OpenGL Interop | 完整实现，真正的零拷贝GPU渲染 ✅ |

---

## CUDA-OpenGL Interop 实现（2026-02-19）

### 实现状态
✅ **已完成** - 真正的零拷贝GPU渲染链路已实现

### 实现细节

#### 1. 数据流
```
Android设备 → H.265/HEVC码流 → 网络传输
    → PyAV解码 (hevc_cuvid, is_hw_owned=True)
    → GPU帧 (cuda格式, 在GPU显存中)
    → DLPack导出 (零拷贝)
    → CuPy数组 (仍在GPU)
    → CUDA-OpenGL Interop (cudaMemcpy2DToArray)
    → OpenGL纹理 (GPU渲染)
    → 显示器
```

#### 2. 关键代码文件
- `scrcpy_py_ddlx/core/decoder/video.py`:
  - `ZERO_COPY_GPU_ENABLED` 环境变量检测
  - `_frame_to_nv12_dict_gpu()` GPU帧处理方法
  - 使用DLPack导出到CuPy

- `scrcpy_py_ddlx/core/player/video/opengl_widget.py`:
  - `_init_cuda_gl_interop_lib()` CUDA库加载
  - `_init_cuda_gl_interop()` OpenGL纹理注册
  - `_upload_gpu_to_opengl()` GPU到OpenGL纹理传输
  - 使用ctypes直接调用CUDA Runtime API

#### 3. 启用方式
```batch
set PYTHONPATH=C:\Project\github\PyAV
set PATH=C:\Project\ffmpeg\ffmpeg-7.1.1-full_build-shared\bin;%PATH%
set SCRCPY_ZERO_COPY_GPU=1
python tests_gui/test_network_direct.py
```

或使用启动脚本：
```batch
run_zero_copy_test.bat
```

#### 4. 依赖要求
- **PyAV 17+** (需要从源码编译，支持`is_hw_owned`)
- **CuPy** (CUDA 12.x版本)
- **CUDA Toolkit** (11.x, 12.x或13.x)
- **FFmpeg 7.x** (共享库)

#### 5. 回退机制
如果CUDA-OpenGL Interop不可用，系统会自动回退到：
- GPU帧 → CuPy → `cp.asnumpy()` → CPU → `glTexImage2D()` → GPU纹理

这虽然不是完全零拷贝，但仍然比传统的CPU解码路径快很多。

### 性能预期
- **理想情况** (CUDA-OpenGL Interop工作):
  - 解码延迟: ~5-8ms
  - 纹理上传: ~1-2ms (GPU内部传输)
  - 总延迟: ~6-10ms

- **回退情况** (CPU上传):
  - 解码延迟: ~5-8ms
  - GPU→CPU下载: ~2-3ms
  - CPU→GPU上传: ~2-3ms
  - 总延迟: ~9-14ms

### 测试验证
运行 `python test_zero_copy_full.py` 进行完整测试：
1. PyAV `is_hw_owned` 支持
2. CuPy和DLPack功能
3. CUDA-OpenGL Interop函数可用性
4. GPU帧模拟处理

---

## CUDA-OpenGL Interop 实验结果（2026-02-20）

### 实验状态
❌ **失败** - CUDA-OpenGL Interop无法正常工作

### 问题描述
在完整实现并测试后，发现 `cudaMemcpy2DToArray` 始终返回错误21（cudaErrorInvalidDevicePointer）。

### 调试过程

#### 1. 验证项
- ✅ CUDA Runtime库加载成功（cudart64_13.dll）
- ✅ CUDA-OpenGL Interop函数加载成功
- ✅ OpenGL纹理注册成功（cudaGraphicsGLRegisterImage返回0）
- ✅ CuPy数组内存类型正确（cudaMemoryTypeDevice = 2）
- ✅ CuPy数组可以正常访问（`y_gpu[0, 0]`成功）
- ❌ cudaMemcpy2DToArray始终返回error=21

#### 2. 尝试的解决方案
1. **使用CuPy分配的标准设备内存** - 失败
2. **使用cudaMemcpyToArray替代cudaMemcpy2DToArray** - 失败
3. **同步CUDA stream** - 失败
4. **检查内存类型** - 确认是设备内存（type=2）

#### 3. 可能的根本原因
1. **CUDA Context问题**：Qt/OpenGL的CUDA context可能与CuPy/PyAV使用的不兼容
2. **OpenGL纹理格式限制**：GL_LUMINANCE格式可能不被CUDA-OpenGL Interop完全支持
3. **驱动/API版本问题**：CUDA 13.0与OpenGL的互操作可能有未知的限制

### 当前最佳方案

**使用标准NV12模式**（非零拷贝）：
- 解码延迟：3-4ms（hevc_cuvid硬件解码）
- 总延迟：9-11ms
- 数据流：
  ```
  hevc_cuvid解码 → NV12(Y/U/V平面) → CPU内存
      → glTexImage2D → OpenGL纹理 → GPU shader色彩转换 → 显示
  ```

### 参考日志
- **低延迟正常模式**：`scrcpy_network_test_20260219_211638.log`
  - decode_time: 3.0-4.1ms
  - total_pipeline: 9-11ms
  - 使用NV12 GPU shader渲染

### 未来可能的解决方案
1. **使用CUDA Surface**：直接用CUDA kernel写入OpenGL纹理
2. **使用Vulkan**：Vulkan有更好的跨API互操作支持
3. **使用CUDA-OpenGL Interop的正确纹理格式**：可能需要使用GL_RGBA8而不是GL_LUMINANCE
4. **等待NVIDIA/CUDA更新**：可能是驱动或API的bug

