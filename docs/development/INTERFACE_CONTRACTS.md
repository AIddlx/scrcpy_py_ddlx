# 接口契约定义

**版本**: 1.0
**日期**: 2026-02-21
**目的**: 明确定义所有必须保持向后兼容的接口

---

## 一、视频渲染接口契约

### 1.1 VideoWidgetBase (抽象基类)

所有视频渲染组件必须实现此接口：

```python
from abc import ABC, abstractmethod
from typing import Optional, Callable, Any
from queue import Queue


class VideoWidgetBase(ABC):
    """视频渲染组件的抽象基类"""

    # ========== 必须实现的属性 ==========

    @property
    @abstractmethod
    def device_width(self) -> int:
        """设备屏幕宽度"""
        pass

    @property
    @abstractmethod
    def device_height(self) -> int:
        """设备屏幕高度"""
        pass

    # ========== 必须实现的方法 ==========

    @abstractmethod
    def set_delay_buffer(self, delay_buffer: 'DelayBuffer') -> None:
        """
        设置延迟缓冲区

        Args:
            delay_buffer: DelayBuffer 实例，用于消费解码后的帧

        契约:
        - 必须在开始渲染前调用
        - 设置后，widget 应从 delay_buffer.consume() 获取帧
        - 必须是线程安全的（可能在解码线程调用）
        """
        pass

    @abstractmethod
    def set_control_queue(self, queue: Queue) -> None:
        """
        设置控制消息队列

        Args:
            queue: 用于发送控制消息到设备的队列

        契约:
        - 用户输入事件应封装为 ControlMessage 放入队列
        - 必须在处理输入事件前调用
        """
        pass

    @abstractmethod
    def set_consume_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """
        设置帧消费回调

        Args:
            callback: 每次成功消费帧后调用的回调函数

        契约:
        - 回调在渲染线程中调用
        - 可用于延迟追踪或帧率统计
        """
        pass

    @abstractmethod
    def set_frame_size_changed_callback(
        self, callback: Optional[Callable[[int, int], None]]
    ) -> None:
        """
        设置帧尺寸变化回调

        Args:
            callback: 帧尺寸变化时调用，参数为 (width, height)

        契约:
        - 当检测到帧尺寸变化时调用
        - 通常用于处理设备旋转
        """
        pass

    @abstractmethod
    def set_nv12_mode(self, enabled: bool) -> bool:
        """
        设置 NV12 渲染模式

        Args:
            enabled: 是否启用 NV12 GPU 渲染

        Returns:
            实际是否启用（可能因不支持而失败）

        契约:
        - 如果硬件不支持，应返回 False
        - 切换模式不应导致崩溃
        """
        pass

    @abstractmethod
    def is_nv12_supported(self) -> bool:
        """
        检查是否支持 NV12 GPU 渲染

        Returns:
            True 如果支持 NV12 渲染
        """
        pass
```

### 1.2 VideoWindowBase (抽象基类)

所有视频窗口必须实现此接口：

```python
class VideoWindowBase(ABC):
    """视频窗口的抽象基类"""

    @property
    @abstractmethod
    def video_widget(self) -> VideoWidgetBase:
        """
        获取内部的视频渲染组件

        契约:
        - 返回的对象必须实现 VideoWidgetBase 接口
        """
        pass

    @abstractmethod
    def set_device_info(self, name: str, width: int, height: int) -> None:
        """
        设置设备信息

        Args:
            name: 设备名称（用于窗口标题）
            width: 设备屏幕宽度
            height: 设备屏幕高度

        契约:
        - 设置后窗口应调整大小以适应设备
        - 应保持正确的宽高比
        """
        pass

    @abstractmethod
    def update_frame(self, frame: Optional[Any]) -> None:
        """
        更新显示的帧

        Args:
            frame: 新帧，可以是 None

        契约:
        - None 帧应被安全处理（不崩溃）
        - 应立即触发重绘
        """
        pass

    @abstractmethod
    def set_control_queue(self, queue: Queue) -> None:
        """设置控制消息队列（代理到内部 widget）"""
        pass

    @abstractmethod
    def set_delay_buffer(self, delay_buffer: 'DelayBuffer') -> None:
        """设置延迟缓冲区（代理到内部 widget）"""
        pass
```

---

## 二、延迟缓冲区接口契约

### 2.1 DelayBuffer

```python
class DelayBuffer:
    """单帧缓冲区，用于低延迟渲染"""

    def consume(self) -> Optional['Frame']:
        """
        消费当前帧

        Returns:
            当前帧，如果没有帧则返回 None

        契约:
        - 必须是线程安全的
        - 返回后，内部帧被清除（下次调用返回 None）
        - 不会阻塞
        """
        pass

    def push(self, frame: 'Frame') -> None:
        """
        推入新帧

        Args:
            frame: 解码后的帧

        契约:
        - 必须是线程安全的
        - 覆盖之前的帧（不累积）
        - 不会阻塞
        """
        pass

    @property
    def width(self) -> int:
        """当前帧宽度"""
        pass

    @property
    def height(self) -> int:
        """当前帧高度"""
        pass
```

---

## 三、输入处理接口契约

### 3.1 InputHandler

```python
class InputHandler:
    """用户输入处理器"""

    def set_control_queue(self, queue: Queue) -> None:
        """
        设置控制消息队列

        契约:
        - 所有输入事件都应封装为消息放入队列
        """
        pass

    # 以下方法由 Qt 事件系统调用，契约是：
    # - 正确映射坐标到设备坐标系
    # - 正确处理鼠标按钮状态
    # - 正确处理键盘修饰键

    def mousePressEvent(self, event): pass
    def mouseReleaseEvent(self, event): pass
    def mouseMoveEvent(self, event): pass
    def wheelEvent(self, event): pass
    def keyPressEvent(self, event): pass
    def keyReleaseEvent(self, event): pass
```

### 3.2 CoordinateMapper

```python
class CoordinateMapper:
    """坐标映射器"""

    @staticmethod
    def get_device_coords(
        x: int,
        y: int,
        widget_size: tuple[int, int],
        device_size: tuple[int, int]
    ) -> tuple[int, int]:
        """
        将窗口坐标转换为设备坐标

        Args:
            x: 窗口 X 坐标
            y: 窗口 Y 坐标
            widget_size: (width, height) 窗口尺寸
            device_size: (width, height) 设备尺寸

        Returns:
            (device_x, device_y) 设备坐标

        契约:
        - 必须考虑黑边（letterbox）进行正确映射
        - 必须处理设备旋转（宽高互换）
        - 坐标不应超出设备范围
        """
        pass
```

---

## 四、帧数据格式契约

### 4.1 Frame 结构

帧对象必须提供以下属性：

```python
class Frame:
    """帧数据"""

    @property
    def format(self) -> str:
        """
        帧格式

        可能的值:
        - 'rgb24': RGB 24位
        - 'nv12': NV12 (YUV420 semi-planar)
        - 'yuv420p': YUV420 planar

        契约:
        - 必须是已知格式之一
        - 格式决定数据布局
        """
        pass

    @property
    def width(self) -> int:
        """帧宽度（像素）"""
        pass

    @property
    def height(self) -> int:
        """帧高度（像素）"""
        pass

    @property
    def data(self) -> bytes:
        """
        帧数据

        契约:
        - RGB24: width * height * 3 字节
        - NV12: width * height * 1.5 字节
        - 数据生命周期由生产者管理
        """
        pass

    @property
    def pts(self) -> Optional[int]:
        """
        显示时间戳（Presentation Time Stamp）

        契约:
        - 可能为 None
        - 单位由解码器决定（通常是微秒）
        """
        pass
```

### 4.2 NV12 格式说明

```
NV12 内存布局:
┌────────────────────────────────────┐
│            Y 平面                   │
│      width × height 字节            │
│     (亮度，逐行存储)                 │
├────────────────────────────────────┤
│           UV 平面                   │
│   width × height / 2 字节           │
│  (色度，U/V 交错存储: U0V0U1V1...)   │
└────────────────────────────────────┘

总大小: width × height × 1.5 字节
```

---

## 五、OpenGL 渲染契约

### 5.1 生命周期

```
initializeGL/initialize
        │
        ▼
┌───────────────────┐
│  创建着色器程序    │
│  创建纹理对象      │
│  设置 OpenGL 状态  │
└─────────┬─────────┘
          │
          ▼
    resizeGL/resize
          │
          ▼
┌───────────────────┐
│  设置视口          │
│  更新投影矩阵      │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐    ┌───────────────────┐
│    paintGL/       │◄───│  定时器/帧就绪     │
│    render         │    │  触发 update()    │
└─────────┬─────────┘    └───────────────────┘
          │
          ▼
┌───────────────────┐
│  consume() 帧数据  │
│  上传纹理          │
│  绘制四边形        │
└───────────────────┘
```

### 5.2 线程安全契约

```
┌─────────────────────────────────────────────────────────────┐
│                       线程模型                               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  解码线程               主线程 (Qt GUI)                      │
│  ┌─────────────┐        ┌─────────────────────┐            │
│  │ VideoDecoder │───────►│ VideoWidget         │            │
│  │ decode()     │ push() │ paintGL/render      │            │
│  │              │        │ consume()           │            │
│  └─────────────┘        └─────────────────────┘            │
│        │                         │                          │
│        ▼                         ▼                          │
│  ┌─────────────┐        ┌─────────────────────┐            │
│  │DelayBuffer  │◄───────│ OpenGL 上下文        │            │
│  │ push()      │        │ (仅在主线程操作)     │            │
│  │ consume()   │        └─────────────────────┘            │
│  └─────────────┘                                            │
│  (线程安全)                                                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**契约**：
1. DelayBuffer 的 `push()` 和 `consume()` 必须线程安全
2. OpenGL 操作只能在主线程（或创建上下文的线程）
3. Widget 属性设置可以在任何线程，但内部需要处理

---

## 六、契约违反检测

### 6.1 运行时检查

```python
# 在基类中添加检查
class VideoWidgetBase(ABC):
    def __init__(self):
        self._delay_buffer = None
        self._control_queue = None

    def set_delay_buffer(self, delay_buffer):
        if delay_buffer is None:
            raise ValueError("delay_buffer cannot be None")
        if not hasattr(delay_buffer, 'consume'):
            raise TypeError("delay_buffer must have consume() method")
        self._delay_buffer = delay_buffer
```

### 6.2 类型检查

```python
# 使用 typing 进行静态检查
from typing import Protocol, runtime_checkable

@runtime_checkable
class DelayBufferProtocol(Protocol):
    def consume(self) -> Optional[Frame]: ...
    def push(self, frame: Frame) -> None: ...

def set_delay_buffer(self, delay_buffer: DelayBufferProtocol):
    if not isinstance(delay_buffer, DelayBufferProtocol):
        raise TypeError("Invalid delay_buffer type")
```

---

## 七、契约变更流程

当需要修改接口契约时：

1. **创建变更提案**
   - 在 `docs/development/changelog/` 创建文件
   - 说明变更原因和影响范围

2. **标记过渡期**
   - 保留旧接口，添加 deprecation 警告
   - 提供迁移指南

3. **更新所有调用方**
   - 确保所有使用该接口的代码都更新

4. **移除旧接口**
   - 确认无调用方后移除

```python
# 过渡期示例
def old_method(self, arg):
    import warnings
    warnings.warn(
        "old_method is deprecated, use new_method instead",
        DeprecationWarning,
        stacklevel=2
    )
    return self.new_method(arg)
```

---

**维护者**: 任何接口变更必须更新本文档
