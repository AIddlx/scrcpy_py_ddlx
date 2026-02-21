"""
独立测试 QOpenGLWidget 的 CPU 占用

运行方式：
    python tests_gui/test_qopenglwidget_alone.py
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

    print("=" * 60)
    print("独立测试 QOpenGLWidget")
    print("=" * 60)
    print("测试 5 秒，请观察 CPU 占用...")
    print()

    widget = TestWidget()
    widget.resize(800, 600)
    widget.setWindowTitle("QOpenGLWidget CPU 测试")
    widget.show()

    # 让窗口显示
    app.processEvents()
    time.sleep(0.5)

    # 定时器触发重绘 (16ms = 60fps)
    timer = QTimer()
    timer.timeout.connect(widget.update)
    timer.start(16)

    process = psutil.Process()
    samples = []

    def collect_cpu():
        for _ in range(50):
            samples.append(process.cpu_percent())
            time.sleep(0.1)

    thread = threading.Thread(target=collect_cpu)
    thread.start()

    # 运行 5 秒
    start = time.time()
    while time.time() - start < 5:
        app.processEvents()
        time.sleep(0.001)

    thread.join()

    avg_cpu = sum(samples) / len(samples) if samples else 0
    max_cpu = max(samples) if samples else 0
    min_cpu = min(samples) if samples else 0

    print(f"CPU 占用统计:")
    print(f"  平均: {avg_cpu:.1f}%")
    print(f"  最小: {min_cpu:.1f}%")
    print(f"  最大: {max_cpu:.1f}%")
    print(f"  paintGL 调用次数: {widget.paint_count}")
    print()
    print("请同时观察任务管理器中的 python.exe CPU 占用")
    print("按 Enter 关闭窗口...")

    # 等待用户确认
    input()

    app.quit()


if __name__ == "__main__":
    main()
