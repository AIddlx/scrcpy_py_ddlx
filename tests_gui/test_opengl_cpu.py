"""
可靠的 OpenGL CPU 占用测试（子进程隔离版）

运行方式：
    python tests_gui/test_opengl_cpu.py
"""

import sys
import time
import psutil
import multiprocessing
import os
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def get_cpu_core_count():
    """获取 CPU 核心数"""
    return psutil.cpu_count(logical=True) or 1


def run_widget_test(duration_sec=10):
    """在独立进程中运行 QOpenGLWidget 测试"""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtCore import QTimer
    from OpenGL import GL
    import threading

    app = QApplication(sys.argv)

    class TestWidget(QOpenGLWidget):
        def __init__(self):
            super().__init__()
            self.paint_count = 0

        def paintGL(self):
            GL.glClearColor(0.0, 0.0, 0.0, 1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            self.paint_count += 1

    widget = TestWidget()
    widget.resize(800, 600)
    widget.setWindowTitle("QOpenGLWidget CPU 测试")
    widget.show()

    timer = QTimer()
    timer.timeout.connect(widget.update)
    timer.start(16)

    # 等待窗口显示
    for _ in range(50):
        app.processEvents()
        time.sleep(0.01)

    process = psutil.Process()
    process.cpu_percent()  # 初始化

    time.sleep(1)  # 稳定期

    samples = []
    result = {'samples': [], 'paint_count': 0}

    def measure_thread():
        nonlocal samples
        start = time.time()
        while time.time() - start < duration_sec:
            samples.append(process.cpu_percent())
            time.sleep(0.2)

    thread = threading.Thread(target=measure_thread)
    thread.start()

    while thread.is_alive():
        app.processEvents()
        time.sleep(0.001)

    thread.join()

    result['samples'] = samples
    result['paint_count'] = widget.paint_count

    timer.stop()
    widget.close()
    app.quit()

    return result


def run_window_test(duration_sec=10):
    """在独立进程中运行 QOpenGLWindow 测试"""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtOpenGL import QOpenGLWindow
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtCore import QTimer
    from OpenGL import GL
    import threading

    app = QApplication(sys.argv)

    fmt = QSurfaceFormat()
    fmt.setSamples(0)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)

    class TestWindow(QOpenGLWindow):
        def __init__(self):
            super().__init__()
            self.render_count = 0

        def render(self):
            GL.glClearColor(0.0, 0.0, 0.0, 1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            self.render_count += 1

    window = TestWindow()
    window.resize(800, 600)
    window.setTitle("QOpenGLWindow CPU 测试")
    window.show()

    timer = QTimer()
    timer.timeout.connect(window.update)
    timer.start(16)

    # 等待窗口显示
    for _ in range(50):
        app.processEvents()
        time.sleep(0.01)

    process = psutil.Process()
    process.cpu_percent()  # 初始化

    time.sleep(1)  # 稳定期

    samples = []
    result = {'samples': [], 'render_count': 0}

    def measure_thread():
        nonlocal samples
        start = time.time()
        while time.time() - start < duration_sec:
            samples.append(process.cpu_percent())
            time.sleep(0.2)

    thread = threading.Thread(target=measure_thread)
    thread.start()

    while thread.is_alive():
        app.processEvents()
        time.sleep(0.001)

    thread.join()

    result['samples'] = samples
    result['render_count'] = window.render_count

    timer.stop()
    window.hide()
    app.quit()

    return result


def analyze_result(samples, core_count):
    """分析结果"""
    if not samples:
        return {'avg': 0, 'min': 0, 'max': 0, 'median': 0, 'single_core': 0}

    avg = sum(samples) / len(samples)
    median = sorted(samples)[len(samples) // 2]
    single_core = avg / core_count

    return {
        'avg': avg,
        'min': min(samples),
        'max': max(samples),
        'median': median,
        'single_core': single_core,
        'samples': len(samples)
    }


def main():
    print("=" * 60)
    print("OpenGL CPU 占用可靠测试")
    print("=" * 60)

    core_count = get_cpu_core_count()
    print(f"CPU 核心数: {core_count}")
    print()

    results = {}

    # 测试 QOpenGLWidget
    print("测试 QOpenGLWidget (10秒)...")
    print("  请观察任务管理器中 python.exe CPU 占用")
    try:
        # 使用子进程运行测试
        ctx = multiprocessing.get_context('spawn')
        queue = ctx.Queue()
        p = ctx.Process(target=_run_widget_in_process, args=(queue, 10))
        p.start()
        p.join(timeout=30)
        if p.is_alive():
            p.terminate()
        if not queue.empty():
            widget_result = queue.get()
            results['QOpenGLWidget'] = analyze_result(widget_result['samples'], core_count)
            results['QOpenGLWidget']['paint_count'] = widget_result.get('paint_count', 0)
    except Exception as e:
        print(f"  失败: {e}")
        import traceback
        traceback.print_exc()

    print()

    # 测试 QOpenGLWindow
    print("测试 QOpenGLWindow (10秒)...")
    print("  请观察任务管理器中 python.exe CPU 占用")
    try:
        ctx = multiprocessing.get_context('spawn')
        queue = ctx.Queue()
        p = ctx.Process(target=_run_window_in_process, args=(queue, 10))
        p.start()
        p.join(timeout=30)
        if p.is_alive():
            p.terminate()
        if not queue.empty():
            window_result = queue.get()
            results['QOpenGLWindow'] = analyze_result(window_result['samples'], core_count)
            results['QOpenGLWindow']['render_count'] = window_result.get('render_count', 0)
    except Exception as e:
        print(f"  失败: {e}")
        import traceback
        traceback.print_exc()

    # 打印结果
    print("\n" + "=" * 60)
    print("测试结果")
    print("=" * 60)

    for name, result in results.items():
        print(f"\n{name}")
        print("-" * 50)
        print(f"  采样数: {result['samples']}")
        if 'paint_count' in result:
            print(f"  paintGL 调用: {result['paint_count']}")
        if 'render_count' in result:
            print(f"  render 调用: {result['render_count']}")
        print()
        print(f"  多核 CPU: 平均={result['avg']:.1f}%, 中位数={result['median']:.1f}%")
        print(f"  单核等价: {result['single_core']:.1f}% (对应任务管理器)")

    # 对比
    if len(results) == 2:
        widget_cpu = results['QOpenGLWidget']['single_core']
        window_cpu = results['QOpenGLWindow']['single_core']
        if widget_cpu > 0:
            reduction = (widget_cpu - window_cpu) / widget_cpu * 100
            print(f"\n{'=' * 60}")
            print(f"结论: QOpenGLWindow 比 QOpenGLWidget 降低 {reduction:.0f}% CPU")
            print(f"       ({widget_cpu:.1f}% → {window_cpu:.1f}%)")


if __name__ == "__main__":
    main()
