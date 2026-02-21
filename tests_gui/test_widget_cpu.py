"""
QOpenGLWidget CPU 测试

运行方式：
    python tests_gui/test_widget_cpu.py
"""

import sys
import time
import psutil
import threading
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtCore import QTimer
    from OpenGL import GL

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

    core_count = psutil.cpu_count(logical=True) or 1

    print("=" * 50)
    print("QOpenGLWidget CPU 测试")
    print("=" * 50)
    print(f"CPU 核心数: {core_count}")
    print("测试 10 秒，请观察任务管理器...")
    print()

    samples = []

    def measure_thread():
        start = time.time()
        while time.time() - start < 10:
            samples.append(process.cpu_percent())
            time.sleep(0.2)

    thread = threading.Thread(target=measure_thread)
    thread.start()

    while thread.is_alive():
        app.processEvents()
        time.sleep(0.001)

    thread.join()

    # 计算结果
    avg = sum(samples) / len(samples)
    median = sorted(samples)[len(samples) // 2]
    single_core = avg / core_count

    print(f"结果:")
    print(f"  多核 CPU: 平均={avg:.1f}%, 中位数={median:.1f}%")
    print(f"  单核等价: {single_core:.1f}% (对应任务管理器)")
    print(f"  paintGL 调用: {widget.paint_count}")
    print()
    print("按 Enter 关闭...")

    input()

    timer.stop()
    widget.close()
    app.quit()


if __name__ == "__main__":
    main()
