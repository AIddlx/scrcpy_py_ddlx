# GPU NV12渲染实现详解

## 概述

本文档详细记录了如何实现GPU加速的NV12 YUV渲染，将平均延迟从199ms降低到20ms（10倍提升）。

**读者对象**：需要在Python+Qt环境中实现低延迟视频渲染的开发者

**复现环境**：
- Python 3.10+
- PySide6 (Qt6)
- PyOpenGL
- PyAV
- Windows 11 / Linux

## 问题背景

### 原始问题
```
用户报告：视频预览延迟"6秒"
实际测量：TRUE_E2E显示80-120ms
问题：为什么测量值和体感差距这么大？
```

### 根因分析过程

1. **初步排查**：服务端编码、网络传输、解码都不是瓶颈
2. **发现Qt问题**：`update()`会合并请求，导致帧积压
3. **修复Qt问题**：改用`repaint()`立即渲染
4. **发现CPU瓶颈**：YUV→RGB转换占用GIL，阻塞UDP接收
5. **最终方案**：GPU渲染，避免GIL竞争

## 完整架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                           手机端 (Android)                          │
│  ┌─────────┐    ┌─────────────┐    ┌──────────┐    ┌───────────┐  │
│  │屏幕捕获 │ → │ H.264编码   │ → │ UDP发送  │ → │ WiFi发送  │  │
│  │(_surface)│   │(MediaCodec) │    │(scrcpy)  │    │(5GHz)     │  │
│  └─────────┘    └─────────────┘    └──────────┘    └───────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓ UDP包 (~8Mbps, 60fps)
                                    ↓ 网络延迟 ~6ms (ping)
┌─────────────────────────────────────────────────────────────────────┐
│                         PC端 - 主进程 (asyncio)                      │
│  ┌──────────┐    ┌─────────────┐    ┌──────────┐    ┌───────────┐  │
│  │UDP接收   │ → │ H.264解码   │ → │NV12转换  │ → │ SHM写入   │  │
│  │(socket)  │    │(PyAV/FFmpeg)│    │(reformat)│    │(共享内存) │  │
│  │独立线程  │    │ ~3ms/帧     │    │ ~1ms     │    │ ~1ms      │  │
│  └──────────┘    └─────────────┘    └──────────┘    └───────────┘  │
│       ↑                                                             │
│       │ GIL影响：NV12模式仅持有GIL 1-5ms，不阻塞UDP接收             │
│       │ CPU模式持有GIL 10-50ms，会导致UDP超时                       │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓ 共享内存 (~22MB)
┌─────────────────────────────────────────────────────────────────────┐
│                       PC端 - 预览进程 (Qt事件循环)                    │
│  ┌──────────┐    ┌─────────────┐    ┌──────────┐    ┌───────────┐  │
│  │SHM读取   │ → │ 纹理上传    │ → │GPU渲染   │ → │ Qt显示    │  │
│  │(1ms)     │    │(glTexImage) │    │(Shader)  │    │(QOpenGL)  │  │
│  │          │    │ ~2ms        │    │ ~2ms     │    │(repaint)  │  │
│  └──────────┘    └─────────────┘    └──────────┘    └───────────┘  │
│                           GPU显存中完成YUV→RGB，不占用CPU           │
└─────────────────────────────────────────────────────────────────────┘
```

## NV12格式详解

### 数据布局
```
分辨率：1080×2400 (宽×高)

NV12数据 = Y平面 + UV平面
         = (1080×2400) + (1080×1200)
         = 2592000 + 1296000
         = 3888000 字节

内存布局：
┌───────────────────────────────────────┐
│ 字节 0 - 2591999: Y平面               │
│   每行 1080 字节 × 2400 行            │
│   Y = 亮度，范围 16-235               │
├───────────────────────────────────────┤
│ 字节 2592000 - 3887999: UV平面        │
│   每行 1080 字节 × 1200 行            │
│   UV交错：U0 V0 U1 V1 U2 V2 ...       │
│   U/V = 色度，范围 16-240，128=中性   │
└───────────────────────────────────────┘
```

### Stride对齐问题（重要！）

**问题描述**：FFmpeg会将每行对齐到32字节边界
```
图像宽度：1080
对齐后宽度：1088 (= ceil(1080/32) × 32)
每行填充：8字节
```

**解决代码**：
```python
# 检测stride
y_linesize = y_plane.line_size  # 1088
uv_linesize = uv_plane.line_size  # 1088

if y_linesize != actual_width:
    # 有填充，需要处理
    y_array = np.frombuffer(y_plane, np.uint8).reshape(height, y_linesize)
    y_data = y_array[:, :actual_width]  # 只取有效像素
else:
    # 无填充，直接使用
    y_data = bytes(y_plane)
```

## 完整代码实现

### 步骤1：解码器输出NV12

文件：`scrcpy_py_ddlx/core/decoder/video.py`

```python
def _frame_to_nv12(self, frame: av.VideoFrame) -> tuple:
    """
    将PyAV VideoFrame转换为NV12格式

    Args:
        frame: PyAV解码后的视频帧（可能是YUV420P或NV12）

    Returns:
        (nv12_bytes, width, height) 或 (None, 0, 0) 失败时

    关键点：
        1. reformat("nv12")只做格式转换，不做颜色空间转换
        2. 必须处理stride对齐（1080→1088）
        3. 快速路径：无stride时直接bytes()
    """
    try:
        actual_width = frame.width
        actual_height = frame.height

        if actual_width <= 0 or actual_height <= 0:
            logger.warning(f"Invalid frame dimensions: {actual_width}x{actual_height}")
            return None, 0, 0

        # 尝试转换为NV12格式
        try:
            frame_nv12 = frame.reformat(
                width=actual_width, height=actual_height, format="nv12"
            )
        except Exception as e:
            logger.debug(f"NV12 reformat failed, falling back to YUV420P: {e}")

            # 回退：先转YUV420P再手动组装NV12
            frame_yuv = frame.reformat(format="yuv420p")
            planes = frame_yuv.planes
            if len(planes) != 3:
                return None, 0, 0

            # 手动组装NV12
            y_plane = np.frombuffer(planes[0], np.uint8).reshape(actual_height, actual_width)
            u_plane = np.frombuffer(planes[1], np.uint8).reshape(actual_height // 2, actual_width // 2)
            v_plane = np.frombuffer(planes[2], np.uint8).reshape(actual_height // 2, actual_width // 2)

            # 创建交错的UV平面
            uv_plane = np.empty((actual_height // 2, actual_width), dtype=np.uint8)
            uv_plane[:, 0::2] = u_plane  # U在偶数列
            uv_plane[:, 1::2] = v_plane  # V在奇数列

            nv12_data = np.concatenate([y_plane.ravel(), uv_plane.ravel()])
            return nv12_data.tobytes(), actual_width, actual_height

        # NV12格式 - 处理stride
        planes = frame_nv12.planes
        if planes is None or len(planes) < 2:
            return None, 0, 0

        y_plane = planes[0]
        uv_plane = planes[1]
        y_linesize = y_plane.line_size
        uv_linesize = uv_plane.line_size

        # 快速路径：无stride填充（最高效）
        if y_linesize == actual_width and uv_linesize == actual_width:
            return bytes(y_plane) + bytes(uv_plane), actual_width, actual_height

        # 慢速路径：处理stride填充
        y_array = np.frombuffer(y_plane, np.uint8).reshape(actual_height, y_linesize)
        y_data = y_array[:, :actual_width].ravel()

        uv_array = np.frombuffer(uv_plane, np.uint8).reshape(actual_height // 2, uv_linesize)
        uv_data = uv_array[:, :actual_width].ravel()

        nv12_data = np.concatenate([y_data, uv_data])
        return nv12_data.tobytes(), actual_width, actual_height

    except Exception as e:
        logger.warning(f"Failed to convert frame to NV12: {e}")
        return None, 0, 0
```

### 步骤2：OpenGL Shader

文件：`scrcpy_py_ddlx/preview_process.py`

```python
class OpenGLPreviewWidget(QOpenGLWidget):
    # 顶点着色器 - 使用兼容模式（gl_ModelViewProjectionMatrix）
    NV12_VERTEX_SHADER = """
    varying highp vec2 v_texCoord;

    void main() {
        // 使用固定管线的矩阵，兼容性更好
        gl_Position = gl_ModelViewProjectionMatrix * gl_Vertex;
        v_texCoord = gl_MultiTexCoord0.xy;
    }
    """

    # 片段着色器 - BT.601 YUV→RGB转换
    NV12_FRAGMENT_SHADER = """
    varying highp vec2 v_texCoord;

    uniform sampler2D y_texture;  // 纹理单元0：Y平面
    uniform sampler2D u_texture;  // 纹理单元1：U平面
    uniform sampler2D v_texture;  // 纹理单元2：V平面

    void main() {
        // 采样Y分量（GL_LUMINANCE格式，取.r通道）
        mediump float y = texture2D(y_texture, v_texCoord).r;

        // 采样U和V分量（半分辨率，OpenGL自动双线性插值）
        mediump float u = texture2D(u_texture, v_texCoord).r - 0.5;  // U范围[-0.5, 0.5]
        mediump float v = texture2D(v_texture, v_texCoord).r - 0.5;  // V范围[-0.5, 0.5]

        // BT.601 YUV→RGB转换公式
        // R = Y + 1.402 × (V - 128)
        // G = Y - 0.344 × (U - 128) - 0.714 × (V - 128)
        // B = Y + 1.772 × (U - 128)
        // 注意：shader中已将U/V减去0.5，相当于减去128
        highp float r = y + 1.402 * v;
        highp float g = y - 0.344136 * u - 0.714136 * v;
        highp float b = y + 1.772 * u;

        gl_FragColor = vec4(r, g, b, 1.0);
    }
    """
```

### 步骤3：Shader初始化

```python
def _init_nv12_shader(self):
    """初始化NV12渲染shader"""
    from PySide6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram

    self._nv12_shader = QOpenGLShaderProgram(self)

    # 编译顶点着色器
    if not self._nv12_shader.addShaderFromSourceCode(
        QOpenGLShader.Vertex, self.NV12_VERTEX_SHADER
    ):
        logger.error(f"Vertex shader error: {self._nv12_shader.log()}")
        return False

    # 编译片段着色器
    if not self._nv12_shader.addShaderFromSourceCode(
        QOpenGLShader.Fragment, self.NV12_FRAGMENT_SHADER
    ):
        logger.error(f"Fragment shader error: {self._nv12_shader.log()}")
        return False

    # 链接shader程序
    if not self._nv12_shader.link():
        logger.error(f"Shader link error: {self._nv12_shader.log()}")
        return False

    self._nv12_initialized = True
    logger.info("NV12 GPU shader initialized successfully")
    return True
```

### 步骤4：纹理初始化

```python
def initializeGL(self):
    """初始化OpenGL资源"""
    # 关键：设置像素对齐为1字节
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1)

    # 初始化Y纹理
    self._y_texture_id = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, self._y_texture_id)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

    # 初始化U纹理（复用_uv_texture_id）
    self._uv_texture_id = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, self._uv_texture_id)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

    # 初始化V纹理
    self._v_texture_id = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, self._v_texture_id)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

    # 初始化shader
    self._init_nv12_shader()
```

### 步骤5：GPU渲染

```python
def _paint_nv12_gpu(self, nv12_data: np.ndarray, w: int, h: int):
    """使用GPU渲染NV12帧"""

    # ===== 关键设置1：像素对齐 =====
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1)

    # ===== 关键设置2：正交投影 =====
    # 没有这个设置，顶点坐标会被解释为-1到1，导致黑屏
    widget_w = self.width()
    widget_h = self.height()
    glMatrixMode(GL_PROJECTION)
    glLoadIdentity()
    glOrtho(0, widget_w, widget_h, 0, -1, 1)  # Y轴翻转（屏幕坐标Y向下）
    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()

    # ===== 步骤A：分离Y、U、V平面 =====
    y_size = w * h
    expected_size = int(y_size * 1.5)

    if len(nv12_data) < expected_size:
        logger.error(f"NV12 data too small: {len(nv12_data)} < {expected_size}")
        return False

    # Y平面：直接切片
    y_plane = nv12_data[:y_size]

    # UV平面：需要分离U和V
    uv_plane = nv12_data[y_size:expected_size]
    uv_array = np.frombuffer(uv_plane, dtype=np.uint8).reshape(h // 2, w)
    u_plane = uv_array[:, 0::2].copy()  # U在偶数列
    v_plane = uv_array[:, 1::2].copy()  # V在奇数列

    # ===== 步骤B：上传Y纹理 =====
    glActiveTexture(GL_TEXTURE0)
    glBindTexture(GL_TEXTURE_2D, self._y_texture_id)
    glTexImage2D(
        GL_TEXTURE_2D,      # target
        0,                  # level
        GL_LUMINANCE,       # internalformat (兼容性最好)
        w, h,               # width, height
        0,                  # border
        GL_LUMINANCE,       # format
        GL_UNSIGNED_BYTE,   # type
        y_plane.tobytes()   # data
    )

    # ===== 步骤C：上传U纹理 =====
    glActiveTexture(GL_TEXTURE1)
    glBindTexture(GL_TEXTURE_2D, self._uv_texture_id)
    glTexImage2D(
        GL_TEXTURE_2D, 0, GL_LUMINANCE,
        w // 2, h // 2, 0,  # 半分辨率
        GL_LUMINANCE, GL_UNSIGNED_BYTE,
        u_plane.tobytes()
    )

    # ===== 步骤D：上传V纹理 =====
    glActiveTexture(GL_TEXTURE2)
    glBindTexture(GL_TEXTURE_2D, self._v_texture_id)
    glTexImage2D(
        GL_TEXTURE_2D, 0, GL_LUMINANCE,
        w // 2, h // 2, 0,
        GL_LUMINANCE, GL_UNSIGNED_BYTE,
        v_plane.tobytes()
    )

    # ===== 步骤E：绑定shader =====
    self._nv12_shader.bind()

    # 设置uniform变量（纹理单元索引）
    y_loc = self._nv12_shader.uniformLocation("y_texture")
    u_loc = self._nv12_shader.uniformLocation("u_texture")
    v_loc = self._nv12_shader.uniformLocation("v_texture")
    self._nv12_shader.setUniformValue(y_loc, 0)  # GL_TEXTURE0
    self._nv12_shader.setUniformValue(u_loc, 1)  # GL_TEXTURE1
    self._nv12_shader.setUniformValue(v_loc, 2)  # GL_TEXTURE2

    # ===== 步骤F：计算绘制位置（保持宽高比）=====
    scale = min(widget_w / w, widget_h / h)
    img_w = int(w * scale)
    img_h = int(h * scale)
    x = (widget_w - img_w) // 2
    y = (widget_h - img_h) // 2

    # ===== 步骤G：绑定纹理（绘制前必须！）=====
    glActiveTexture(GL_TEXTURE0)
    glBindTexture(GL_TEXTURE_2D, self._y_texture_id)
    glActiveTexture(GL_TEXTURE1)
    glBindTexture(GL_TEXTURE_2D, self._uv_texture_id)
    glActiveTexture(GL_TEXTURE2)
    glBindTexture(GL_TEXTURE_2D, self._v_texture_id)

    # ===== 步骤H：绘制四边形 =====
    glEnable(GL_TEXTURE_2D)
    glColor3f(1.0, 1.0, 1.0)

    glBegin(GL_QUADS)
    glTexCoord2f(0.0, 0.0); glVertex2f(x, y)              # 左上
    glTexCoord2f(1.0, 0.0); glVertex2f(x + img_w, y)      # 右上
    glTexCoord2f(1.0, 1.0); glVertex2f(x + img_w, y + img_h)  # 右下
    glTexCoord2f(0.0, 1.0); glVertex2f(x, y + img_h)      # 左下
    glEnd()

    glDisable(GL_TEXTURE_2D)
    self._nv12_shader.release()

    return True
```

### 步骤6：Qt立即渲染（关键！）

```python
def _update_frame(self):
    """定时器回调 - 读取并渲染帧"""

    # 读取SHM
    result = shared_mem_reader.read_frame_ex()

    if result is not None:
        frame, pts, capture_time, udp_recv_time, frame_format = result

        # 更新帧数据
        self._widget.update_frame(frame, self._frame_count, frame_format)

        # 关键：使用repaint()而不是update()
        # update()会合并请求，导致帧积压
        # repaint()立即同步渲染，无积压
        self._widget.repaint()
```

## 踩坑记录

### 坑1：黑屏
```
现象：窗口黑屏，无任何内容
原因：没有设置正交投影矩阵
排查：添加glGetError()检查，确认无GL错误
解决：添加glOrtho()设置投影矩阵
```

### 坑2：画面扭曲（斜线）
```
现象：画面显示为斜向条纹，颜色异常
原因：FFmpeg stride对齐（1080→1088）+ GL_UNPACK_ALIGNMENT默认4字节
排查：打印line_size发现是1088而不是1080
解决：
  1. 解码端：切片去除填充 y_array[:, :actual_width]
  2. 渲染端：glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
```

### 坑3：画面上下颠倒
```
现象：画面Y轴翻转
原因：OpenGL纹理Y轴向上，图像Y轴向下
解决：调整纹理坐标Y值
  glTexCoord2f(0.0, 1.0); glVertex2f(x, y)  # 原来是0.0
  glTexCoord2f(1.0, 1.0); glVertex2f(...)   # 原来是0.0
```

### 坑4：画面左右镜像
```
现象：画面X轴翻转
原因：纹理坐标X值需要调整
解决：调整纹理坐标X值
  glTexCoord2f(0.0, ...); glVertex2f(x, y)      # 从左开始
  glTexCoord2f(1.0, ...); glVertex2f(x + w, y)  # 到右结束
```

### 坑5：延迟累积到数秒
```
现象：E2E显示正常，但体感延迟数秒
原因：Qt的update()会合并多个重绘请求
排查：日志显示同一帧被读取多次，E2E不断增加
解决：使用repaint()立即同步渲染
  self._widget.update()   # ❌ 会合并
  self._widget.repaint()  # ✅ 立即渲染
```

### 坑6：GL_RED/GL_RG崩溃
```
现象：使用GL_RED或GL_RG格式时程序崩溃
原因：某些OpenGL驱动不支持这些格式
解决：使用GL_LUMINANCE格式，兼容性最好
```

### 坑7：GIL竞争导致Socket超时
```
现象：CPU模式下Socket频繁超时，GPU模式正常
原因：CPU颜色转换持有GIL 10-50ms，阻塞UDP接收线程
排查：对比CPU和GPU模式的Socket超时日志
解决：使用GPU渲染，颜色转换在GPU进行
```

## 性能数据

### 延迟分解（GPU模式，1080×2400@60fps）

```
总延迟 ~20ms
├── 网络传输 (WiFi 5GHz)    ~6ms   [不可控]
├── 手机端编码 (H.264)      ~5ms   [scrcpy控制]
├── UDP接收                  ~1ms
├── PyAV解码                 ~3ms
├── reformat("nv12")         ~1ms   [关键：不做颜色转换]
├── SHM传输                   <1ms
└── GPU渲染                   ~3ms   [GPU并行]
```

### 对比数据

| 指标 | CPU模式 | GPU模式 | 改善倍数 |
|------|---------|---------|----------|
| 本地处理延迟 | 10-50ms | 2-5ms | 10x |
| 平均E2E延迟 | 199ms | 20ms | 10x |
| 最高E2E延迟 | 3493ms | 322ms | 10x |
| Socket超时频率 | 频繁 | 很少 | - |
| CPU占用 | 高 | 低 | - |

## 配置说明

### 启用GPU模式

```python
# scrcpy_http_mcp_server.py
decoder._output_nv12 = True   # 启用NV12输出
decoder._output_nv12 = False  # 禁用（使用CPU RGB模式）
```

### 推荐配置

| 场景 | 码率 | 帧率 | 渲染模式 |
|------|------|------|----------|
| WiFi 5GHz | 8Mbps | 60fps | GPU |
| WiFi 2.4GHz | 4Mbps | 30fps | GPU |
| 网络不稳定 | 2Mbps | 30fps | GPU |
| GPU不可用 | ≤2Mbps | 30fps | CPU |

## 常见问题

### Q1：为什么使用3个纹理而不是2个？
A：NV12原始格式是2个平面（Y + UV），但GL_RG格式在某些驱动上不稳定。使用GL_LUMINANCE + 3个纹理兼容性最好。

### Q2：为什么不用QOpenGLTexture？
A：QOpenGLTexture的API在PySide6中有兼容性问题。直接使用PyOpenGL更稳定。

### Q3：repaint()会不会阻塞UI？
A：会，但这是必要的权衡。对于视频预览场景，低延迟比UI响应更重要。实际测试中paintGL只需2-5ms，影响很小。

### Q4：如何调试GL问题？
A：
```python
from OpenGL.GL import glGetError, GL_NO_ERROR
err = glGetError()
if err != GL_NO_ERROR:
    logger.error(f"GL error: {err}")
```

## 参考资料

- NV12格式：https://www.fourcc.org/pixel-format/yuv-nv12/
- BT.601色彩空间：https://en.wikipedia.org/wiki/Rec._601
- OpenGL纹理：https://www.khronos.org/opengl/wiki/Texture
- PyAV文档：https://pyav.basswood-io.com/
- Qt OpenGL：https://doc.qt.io/qt-6/qopenglwidget.html
