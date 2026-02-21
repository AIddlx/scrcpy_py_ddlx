"""
对比 QOpenGLWidget vs QOpenGLWindow 的 CPU 开销

运行方式：
    python tests_gui/test_opengl_modes.py
"""

import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def test_qopenglwidget():
    """测试 QOpenGLWidget"""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtCore import QTimer
    from OpenGL import GL
    import psutil
    import threading

    app = QApplication(sys.argv)

    class TestWidget(QOpenGLWidget):
        def __init__(self):
            super().__init__()
            self.count = 0
        def paintGL(self):
            GL.glClearColor(0, 0, 0, 1)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            self.count += 1

    widget = TestWidget()
    widget.resize(800, 600)
    widget.show()

    timer = QTimer()
    timer.timeout.connect(widget.update)
    timer.start(16)

    process = psutil.Process()
    samples = []

    def collect():
        for _ in range(30):
            samples.append(process.cpu_percent())
            time.sleep(0.1)

    thread = threading.Thread(target=collect)
    thread.start()

    start = time.time()
    while time.time() - start < 3:
        app.processEvents()
        time.sleep(0.001)

    thread.join()
    timer.stop()
    widget.close()

    avg = sum(samples) / len(samples) if samples else 0
    print(f"QOpenGLWidget: {avg:.1f}% CPU, paintGL calls: {widget.count}")
    return avg


def test_qopenglwindow():
    """测试 QOpenGLWindow"""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtOpenGL import QOpenGLWindow
    from PySide6.QtCore import QTimer
    from OpenGL import GL
    import psutil
    import threading

    app = QApplication.instance()

    class TestWindow(QOpenGLWindow):
        def __init__(self):
            super().__init__()
            self.count = 0
        def render(self):
            GL.glClearColor(0, 0, 0, 1)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            self.count += 1

    window = TestWindow()
    window.resize(800, 600)
    window.show()

    timer = QTimer()
    timer.timeout.connect(window.update)
    timer.start(16)

    process = psutil.Process()
    samples = []

    def collect():
        for _ in range(30):
            samples.append(process.cpu_percent())
            time.sleep(0.1)

    thread = threading.Thread(target=collect)
    thread.start()

    start = time.time()
    while time.time() - start < 3:
        app.processEvents()
        time.sleep(0.001)

    thread.join()
    timer.stop()
    window.hide()

    avg = sum(samples) / len(samples) if samples else 0
    print(f"QOpenGLWindow: {avg:.1f}% CPU, render calls: {window.count}")
    return avg


def test_raw_qwidget():
    """测试普通 QWidget 作为基准"""
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QPainter, QColor
    import psutil
    import threading

    app = QApplication.instance()

    class TestWidget(QWidget):
        def __init__(self):
            super().__init__()
            self.count = 0
        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(0, 0, 0))
            painter.end()
            self.count += 1

    widget = TestWidget()
    widget.resize(800, 600)
    widget.show()

    timer = QTimer()
    timer.timeout.connect(widget.update)
    timer.start(16)

    process = psutil.Process()
    samples = []

    def collect():
        for _ in range(30):
            samples.append(process.cpu_percent())
            time.sleep(0.1)

    thread = threading.Thread(target=collect)
    thread.start()

    start = time.time()
    while time.time() - start < 3:
        app.processEvents()
        time.sleep(0.001)

    thread.join()
    timer.stop()
    widget.close()

    avg = sum(samples) / len(samples) if samples else 0
    print(f"QWidget (software): {avg:.1f}% CPU, paintEvent calls: {widget.count}")
    return avg


def main():
    print("=" * 60)
    print("OpenGL 渲染方式对比")
    print("=" * 60)
    print()

    try:
        cpu1 = test_qopenglwidget()
    except Exception as e:
        print(f"QOpenGLWidget 测试失败: {e}")
        cpu1 = 0

    try:
        cpu2 = test_qopenglwindow()
    except Exception as e:
        print(f"QOpenGLWindow 测试失败: {e}")
        cpu2 = 0

    try:
        cpu3 = test_raw_qwidget()
    except Exception as e:
        print(f"QWidget 测试失败: {e}")
        cpu3 = 0

    print()
    print("=" * 60)
    print("结论")
    print("=" * 60)
    print(f"QOpenGLWidget: {cpu1:.1f}%")
    print(f"QOpenGLWindow: {cpu2:.1f}%")
    print(f"QWidget (软件): {cpu3:.1f}%")

    if cpu1 > 20 and cpu2 < cpu1 / 2:
        print("\n建议: QOpenGLWidget 开销大，考虑改用 QOpenGLWindow")
    elif cpu1 > 20 and cpu3 < cpu1 / 2:
        print("\n建议: OpenGL 开销大，考虑使用软件渲染")

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app:
        app.quit()


if __name__ == "__main__":
    main()
