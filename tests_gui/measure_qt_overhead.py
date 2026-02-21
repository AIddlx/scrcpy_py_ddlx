"""
Qt 事件循环开销测量

运行方式：
    python tests_gui/measure_qt_overhead.py
"""

import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def measure_empty_qt():
    """测量空 Qt 事件循环的开销"""
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtCore import QTimer

    app = QApplication(sys.argv)

    # 创建一个简单窗口
    widget = QWidget()
    widget.resize(800, 600)
    widget.show()

    # 测量 processEvents 开销
    times = []
    for _ in range(1000):
        t0 = time.perf_counter()
        app.processEvents()
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)

    avg = sum(times) / len(times)
    max_t = max(times)
    print(f"[空 Qt] processEvents: avg={avg:.4f}ms, max={max_t:.4f}ms")

    # 测量带定时器的开销
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(16)

    times2 = []
    for _ in range(1000):
        t0 = time.perf_counter()
        app.processEvents()
        t1 = time.perf_counter()
        times2.append((t1 - t0) * 1000)

    avg2 = sum(times2) / len(times2)
    max2 = max(times2)
    print(f"[定时器 16ms] processEvents: avg={avg2:.4f}ms, max={max2:.4f}ms")

    # 测量实际 CPU 占用
    import psutil
    import threading

    process = psutil.Process()
    cpu_samples = []

    def measure_cpu():
        for _ in range(50):
            cpu_samples.append(process.cpu_percent())
            time.sleep(0.1)

    thread = threading.Thread(target=measure_cpu)
    thread.start()

    # 运行 5 秒
    start = time.time()
    while time.time() - start < 5:
        app.processEvents()
        time.sleep(0.001)

    thread.join()

    avg_cpu = sum(cpu_samples) / len(cpu_samples)
    print(f"\n[5秒测量] 空 Qt + 16ms 定时器 CPU: {avg_cpu:.1f}%")

    # 不退出 app，让后续测试使用
    widget.close()
    timer.stop()

    return avg_cpu


def measure_qt_with_opengl():
    """测量 Qt + OpenGL 的开销"""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtCore import QTimer
    from OpenGL.GL import glClear, GL_COLOR_BUFFER_BIT, glClearColor

    # 使用现有的 QApplication 实例
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    class SimpleGLWidget(QOpenGLWidget):
        def __init__(self):
            super().__init__()
            self.paint_count = 0
            self.total_time = 0

        def paintGL(self):
            t0 = time.perf_counter()
            glClearColor(0.0, 0.0, 0.0, 1.0)
            glClear(GL_COLOR_BUFFER_BIT)
            t1 = time.perf_counter()
            self.paint_count += 1
            self.total_time += (t1 - t0) * 1000

    widget = SimpleGLWidget()
    widget.resize(800, 600)
    widget.show()

    # 定时器触发重绘
    timer = QTimer()
    timer.timeout.connect(widget.update)
    timer.start(16)

    # 运行 5 秒
    import psutil
    import threading

    process = psutil.Process()
    cpu_samples = []

    def measure_cpu():
        for _ in range(50):
            cpu_samples.append(process.cpu_percent())
            time.sleep(0.1)

    thread = threading.Thread(target=measure_cpu)
    thread.start()

    start = time.time()
    while time.time() - start < 5:
        app.processEvents()
        time.sleep(0.001)

    thread.join()

    avg_cpu = sum(cpu_samples) / len(cpu_samples)
    avg_paint = widget.total_time / widget.paint_count if widget.paint_count > 0 else 0
    print(f"\n[5秒测量] Qt + OpenGL (空渲染) CPU: {avg_cpu:.1f}%")
    print(f"[OpenGL] paintGL: avg={avg_paint:.4f}ms, calls={widget.paint_count}")

    return avg_cpu


def main():
    print("=" * 60)
    print("Qt 事件循环开销测量")
    print("=" * 60)
    print()

    try:
        cpu1 = measure_empty_qt()
        print()
        cpu2 = measure_qt_with_opengl()

        print(f"\n{'=' * 60}")
        print("结论")
        print(f"{'=' * 60}")
        print(f"空 Qt: {cpu1:.1f}%")
        print(f"Qt + OpenGL: {cpu2:.1f}%")
        print(f"OpenGL 额外开销: {cpu2 - cpu1:.1f}%")
        print()
        print("如果空 Qt 已有较高 CPU，说明是 Qt/系统本身的开销")
        print("如果 OpenGL 增加较多，说明是渲染相关")

        # 清理
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.quit()

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
