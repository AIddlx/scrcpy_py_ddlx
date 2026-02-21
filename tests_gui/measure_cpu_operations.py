"""
精确测量各操作 CPU 耗时

运行方式：
    python tests_gui/measure_cpu_operations.py
"""

import numpy as np
import time

# 模拟 2800x1264 分辨率
WIDTH, HEIGHT = 2800, 1264
ITERATIONS = 300  # 模拟 5 秒 @ 60fps


def measure_time(name: str, func, iterations: int = ITERATIONS):
    """测量函数执行时间"""
    # 预热
    for _ in range(10):
        func()

    # 测量
    start = time.perf_counter()
    for _ in range(iterations):
        func()
    end = time.perf_counter()

    total_ms = (end - start) * 1000
    per_frame_ms = total_ms / iterations
    fps_equivalent = 1000 / per_frame_ms if per_frame_ms > 0 else float('inf')

    print(f"{name}:")
    print(f"  总时间: {total_ms:.0f}ms ({iterations} 帧)")
    print(f"  每帧: {per_frame_ms:.3f}ms (等效 {fps_equivalent:.0f} fps)")
    print()


def test_1_create_arrays():
    """测试 1: 每帧创建新数组 (原始方法)"""
    y_data = np.random.randint(0, 256, (HEIGHT, WIDTH), dtype=np.uint8)
    uv_data = np.random.randint(0, 256, (HEIGHT // 2, WIDTH), dtype=np.uint8)

    def operation():
        # 原始方法：每帧创建新数组
        y_array = np.ascontiguousarray(y_data)
        u_array = np.ascontiguousarray(uv_data[:, 0::2])
        v_array = np.ascontiguousarray(uv_data[:, 1::2])
        return y_array, u_array, v_array

    measure_time("测试 1: 每帧创建新数组 (ascontiguousarray)", operation)


def test_2_preallocated_buffers():
    """测试 2: 预分配缓冲区 + copyto"""
    y_data = np.random.randint(0, 256, (HEIGHT, WIDTH), dtype=np.uint8)
    uv_data = np.random.randint(0, 256, (HEIGHT // 2, WIDTH), dtype=np.uint8)

    # 预分配缓冲区
    y_buffer = np.empty((HEIGHT, WIDTH), dtype=np.uint8)
    u_buffer = np.empty((HEIGHT // 2, WIDTH // 2), dtype=np.uint8)
    v_buffer = np.empty((HEIGHT // 2, WIDTH // 2), dtype=np.uint8)

    def operation():
        # 优化方法：复用预分配缓冲区
        np.copyto(y_buffer, y_data)
        np.copyto(u_buffer, uv_data[:, 0::2])
        np.copyto(v_buffer, uv_data[:, 1::2])
        return y_buffer, u_buffer, v_buffer

    measure_time("测试 2: 预分配缓冲区 (np.copyto)", operation)


def test_3_two_texture_uv():
    """测试 3: 2 纹理方案 (Y + UV，不分离 U/V)"""
    y_data = np.random.randint(0, 256, (HEIGHT, WIDTH), dtype=np.uint8)
    uv_data = np.random.randint(0, 256, (HEIGHT // 2, WIDTH // 2, 2), dtype=np.uint8)

    def operation():
        # 直接使用 Y + UV 交错，不分离
        # 只需要简单的 reshape
        return y_data, uv_data

    measure_time("测试 3: 2 纹理方案 (Y + UV)", operation)


def test_4_tobytes():
    """测试 4: tobytes() 操作"""
    y_data = np.random.randint(0, 256, (HEIGHT, WIDTH), dtype=np.uint8)

    def operation():
        return y_data.tobytes()

    measure_time("测试 4: tobytes() 操作", operation)


def test_5_stride_handling():
    """测试 5: 处理 stride padding 的复制"""
    # 模拟 FFmpeg 的 stride（比实际宽度大）
    LINESIZE = WIDTH + 64  # 有 padding
    y_raw = np.random.randint(0, 256, (HEIGHT, LINESIZE), dtype=np.uint8)
    uv_raw = np.random.randint(0, 256, (HEIGHT // 2, LINESIZE), dtype=np.uint8)

    # 预分配缓冲区
    y_buffer = np.empty((HEIGHT, WIDTH), dtype=np.uint8)
    u_buffer = np.empty((HEIGHT // 2, WIDTH // 2), dtype=np.uint8)
    v_buffer = np.empty((HEIGHT // 2, WIDTH // 2), dtype=np.uint8)

    def operation():
        # 处理 stride：切片 + 复制
        np.copyto(y_buffer, y_raw[:, :WIDTH])
        uv_sliced = uv_raw[:, :WIDTH]
        np.copyto(u_buffer, uv_sliced[:, 0::2])
        np.copyto(v_buffer, uv_sliced[:, 1::2])
        return y_buffer, u_buffer, v_buffer

    measure_time("测试 5: 处理 stride padding", operation)


def test_6_qt_timer_overhead():
    """测试 6: Qt 定时器开销估算"""
    try:
        from PySide6.QtCore import QTimer, QCoreApplication
        import sys

        app = QCoreApplication.instance()
        if app is None:
            app = QCoreApplication(sys.argv)

        count = [0]
        start_time = [time.perf_counter()]

        def timer_callback():
            count[0] += 1
            if count[0] >= 100:
                elapsed = time.perf_counter() - start_time[0]
                print(f"测试 6: Qt 16ms 定时器开销:")
                print(f"  100 次回调耗时: {elapsed * 1000:.0f}ms")
                print(f"  预期耗时: 1600ms")
                print(f"  额外开销: {(elapsed * 1000 - 1600):.0f}ms")
                print()
                app.quit()

        timer = QTimer()
        timer.timeout.connect(timer_callback)
        timer.start(16)  # 16ms 定时器
        start_time[0] = time.perf_counter()

        # 运行事件循环
        app.exec()

    except ImportError:
        print("Qt 不可用，跳过测试 6")
        print()


def test_7_combined_overhead():
    """测试 7: 综合开销估算"""
    # 预分配
    y_buffer = np.empty((HEIGHT, WIDTH), dtype=np.uint8)
    u_buffer = np.empty((HEIGHT // 2, WIDTH // 2), dtype=np.uint8)
    v_buffer = np.empty((HEIGHT // 2, WIDTH // 2), dtype=np.uint8)

    # 模拟输入（带 stride）
    LINESIZE = WIDTH + 64
    y_raw = np.random.randint(0, 256, (HEIGHT, LINESIZE), dtype=np.uint8)
    uv_raw = np.random.randint(0, 256, (HEIGHT // 2, LINESIZE), dtype=np.uint8)

    def operation():
        # 1. 处理 stride
        np.copyto(y_buffer, y_raw[:, :WIDTH])
        uv_sliced = uv_raw[:, :WIDTH]

        # 2. 分离 U/V
        np.copyto(u_buffer, uv_sliced[:, 0::2])
        np.copyto(v_buffer, uv_sliced[:, 1::2])

        # 3. 模拟 OpenGL 数据指针获取
        y_ptr = y_buffer.ctypes.data
        u_ptr = u_buffer.ctypes.data
        v_ptr = v_buffer.ctypes.data

        return y_ptr, u_ptr, v_ptr

    measure_time("测试 7: 综合开销 (stride + U/V分离 + 指针获取)", operation)


def main():
    print("=" * 60)
    print(f"CPU 操作耗时测量 (分辨率: {WIDTH}x{HEIGHT}, {ITERATIONS} 帧)")
    print("=" * 60)
    print()

    test_1_create_arrays()
    test_2_preallocated_buffers()
    test_3_two_texture_uv()
    test_4_tobytes()
    test_5_stride_handling()
    test_7_combined_overhead()

    print("=" * 60)
    print("结论")
    print("=" * 60)
    print()
    print("如果 60fps 视频每帧处理超过 16.7ms，CPU 占用会很高。")
    print("重点优化超过 1ms/帧 的操作。")


if __name__ == "__main__":
    main()
