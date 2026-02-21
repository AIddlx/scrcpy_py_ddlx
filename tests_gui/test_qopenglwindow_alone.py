"""
独立测试 QOpenGLWindow 的 CPU 占用

运行方式：
    python tests_gui/test_qopenglwindow_alone.py
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
    from PySide6.QtOpenGL import QOpenGLWindow
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtCore import QTimer
    from OpenGL import GL

    app = QApplication(sys.argv)

    # 设置 OpenGL 格式
    fmt = QSurfaceFormat()
    fmt.setSamples(0)  # 无 MSAA
    fmt.setSwapInterval(1)  # VSync
    QSurfaceFormat.setDefaultFormat(fmt)

    class TestWindow(QOpenGLWindow):
        def __init__(self):
            super().__init__()
            self.render_count = 0

        def initializeGL(self):
            print("OpenGL 初始化完成")

        def render(self):
            GL.glClearColor(0.0, 0.0, 0.0, 1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            self.render_count += 1

    print("=" * 60)
    print("独立测试 QOpenGLWindow")
    print("=" * 60)
    print("测试 5 秒，请观察 CPU 占用...")
    print()

    window = TestWindow()
    window.resize(800, 600)
    window.setTitle("QOpenGLWindow CPU 测试")
    window.show()

    # 让窗口显示
    app.processEvents()
    time.sleep(0.5)

    # 定时器触发重绘 (16ms = 60fps)
    timer = QTimer()
    timer.timeout.connect(window.update)
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

    print(f"CPU 占用统计 (多核):")
    print(f"  平均: {avg_cpu:.1f}%")
    print(f"  最小: {min_cpu:.1f}%")
    print(f"  最大: {max_cpu:.1f}%")
    print(f"  render 调用次数: {window.render_count}")
    print()
    print("请同时观察任务管理器中的 python.exe CPU 占用")
    print("按 Enter 关闭窗口...")

    input()

    app.quit()


if __name__ == "__main__":
    main()
