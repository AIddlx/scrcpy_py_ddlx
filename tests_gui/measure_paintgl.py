"""
测量 paintGL 开销的简单测试

运行方式：
    python tests_gui/measure_paintgl.py
"""

import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def main():
    print("=" * 60)
    print("paintGL 开销测量")
    print("=" * 60)

    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QPainter, QColor

    app = QApplication(sys.argv)

    # 测试 1：空 QWidget
    class EmptyWidget(QWidget):
        def __init__(self):
            super().__init__()
            self.paint_count = 0
            self.total_time = 0
            self.start_time = None

        def paintEvent(self, event):
            if self.start_time is None:
                self.start_time = time.perf_counter()

            t0 = time.perf_counter()
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(0, 0, 0))
            painter.end()
            t1 = time.perf_counter()

            self.paint_count += 1
            self.total_time += (t1 - t0) * 1000

            if self.paint_count % 60 == 0:
                elapsed = time.perf_counter() - self.start_time
                fps = self.paint_count / elapsed if elapsed > 0 else 0
                avg_time = self.total_time / self.paint_count
                print(f"[空Widget] #{self.paint_count}: "
                      f"paintEvent={avg_time:.3f}ms, fps={fps:.0f}")

    # 测试 2：OpenGL Widget (无纹理上传)
    try:
        from PySide6.QtOpenGLWidgets import QOpenGLWidget
        from OpenGL.GL import glClear, GL_COLOR_BUFFER_BIT, glClearColor

        class EmptyGLWidget(QOpenGLWidget):
            def __init__(self):
                super().__init__()
                self.paint_count = 0
                self.total_time = 0
                self.start_time = None

            def paintGL(self):
                if self.start_time is None:
                    self.start_time = time.perf_counter()

                t0 = time.perf_counter()
                glClearColor(0.0, 0.0, 0.0, 1.0)
                glClear(GL_COLOR_BUFFER_BIT)
                t1 = time.perf_counter()

                self.paint_count += 1
                self.total_time += (t1 - t0) * 1000

                if self.paint_count % 60 == 0:
                    elapsed = time.perf_counter() - self.start_time
                    fps = self.paint_count / elapsed if elapsed > 0 else 0
                    avg_time = self.total_time / self.paint_count
                    print(f"[空GLWidget] #{self.paint_count}: "
                          f"paintGL={avg_time:.3f}ms, fps={fps:.0f}")

        gl_available = True
    except ImportError:
        print("OpenGL 不可用，跳过 GLWidget 测试")
        gl_available = False

    # 运行测试
    print("\n测试 1: 空 QWidget (10秒)")
    print("-" * 40)
    widget1 = EmptyWidget()
    widget1.resize(800, 600)
    widget1.show()

    def test1_update():
        widget1.update()

    timer1 = QTimer()
    timer1.timeout.connect(test1_update)
    timer1.start(16)  # 60fps

    def finish_test1():
        timer1.stop()
        widget1.close()
        print(f"\n测试 1 完成: {widget1.paint_count} 次 paintEvent")
        print(f"平均耗时: {widget1.total_time / widget1.paint_count:.3f}ms")

        if gl_available:
            start_test2()

    QTimer.singleShot(10000, finish_test1)

    def start_test2():
        print("\n测试 2: 空 OpenGL Widget (10秒)")
        print("-" * 40)

        widget2 = EmptyGLWidget()
        widget2.resize(800, 600)
        widget2.show()

        def test2_update():
            widget2.update()

        timer2 = QTimer()
        timer2.timeout.connect(test2_update)
        timer2.start(16)

        def finish_test2():
            timer2.stop()
            widget2.close()
            print(f"\n测试 2 完成: {widget2.paint_count} 次 paintGL")
            print(f"平均耗时: {widget2.total_time / widget2.paint_count:.3f}ms")
            print("\n" + "=" * 60)
            print("结论")
            print("=" * 60)
            print("如果空 GLWidget 的 paintGL 耗时 >0.5ms，")
            print("说明 Qt/OpenGL 本身有基础开销。")
            app.quit()

        QTimer.singleShot(10000, finish_test2)

    app.exec()


if __name__ == "__main__":
    main()
