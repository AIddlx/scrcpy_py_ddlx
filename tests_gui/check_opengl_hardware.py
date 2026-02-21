"""
检查 OpenGL 硬件加速状态

运行方式：
    python tests_gui/check_opengl_hardware.py
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def check_opengl():
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QSurfaceFormat
    from OpenGL import GL

    app = QApplication(sys.argv)

    # 检查默认表面格式
    fmt = QSurfaceFormat.defaultFormat()
    print("=" * 60)
    print("QSurfaceFormat 默认配置")
    print("=" * 60)
    print(f"  版本: {fmt.majorVersion()}.{fmt.minorVersion()}")
    print(f"  Profile: {fmt.profile()}")
    print(f"  RenderableType: {fmt.renderableType()}")
    print(f"  Samples (MSAA): {fmt.samples()}")
    print(f"  SwapBehavior: {fmt.swapBehavior()}")
    print(f"  SwapInterval (VSync): {fmt.swapInterval()}")

    class InfoGLWidget(QOpenGLWidget):
        def initializeGL(self):
            print("\n" + "=" * 60)
            print("OpenGL 信息")
            print("=" * 60)
            print(f"  Vendor: {GL.glGetString(GL.GL_VENDOR)}")
            print(f"  Renderer: {GL.glGetString(GL.GL_RENDERER)}")
            print(f"  Version: {GL.glGetString(GL.GL_VERSION)}")
            print(f"  GLSL Version: {GL.glGetString(GL.GL_SHADING_LANGUAGE_VERSION)}")

            # 检查是否是软件渲染
            renderer = GL.glGetString(GL.GL_RENDERER).decode() if isinstance(GL.glGetString(GL.GL_RENDERER), bytes) else GL.glGetString(GL.GL_RENDERER)
            vendor = GL.glGetString(GL.GL_VENDOR).decode() if isinstance(GL.glGetString(GL.GL_VENDOR), bytes) else GL.glGetString(GL.GL_VENDOR)

            print()
            is_software = any(x in renderer.lower() for x in ['swiftshader', 'llvmpipe', 'software', 'mesa', 'gdi'])
            is_software = is_software or any(x in vendor.lower() for x in ['mesa', 'software'])

            if is_software:
                print("  ⚠️ 警告: 检测到软件渲染！")
                print("  这会导致高 CPU 占用。")
            else:
                print("  ✓ 使用硬件渲染")

            # 检查扩展
            ext_count = GL.glGetIntegerv(GL.GL_NUM_EXTENSIONS)
            print(f"\n  扩展数量: {ext_count}")

            # 检查关键扩展
            key_exts = [
                'GL_ARB_framebuffer_object',
                'GL_ARB_texture_non_power_of_two',
                'GL_EXT_texture_format_BGRA8888',
            ]
            print("  关键扩展:")
            for ext in key_exts:
                supported = any(ext in GL.glGetStringi(GL.GL_EXTENSIONS, i).decode()
                              for i in range(ext_count)
                              if GL.glGetStringi(GL.GL_EXTENSIONS, i))
                print(f"    {ext}: {'✓' if supported else '✗'}")

        def paintGL(self):
            GL.glClearColor(0, 0, 0, 1)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)

    widget = InfoGLWidget()
    widget.resize(400, 300)
    widget.show()

    # 等待一帧渲染
    QTimer.singleShot(100, app.quit)
    app.exec()

    print("\n" + "=" * 60)
    print("建议")
    print("=" * 60)
    print("""
如果检测到软件渲染，可能的解决方案：
1. 更新显卡驱动
2. 检查是否有多 GPU（尝试指定使用独立显卡）
3. 设置环境变量强制使用硬件加速：
   set QT_OPENGL=angle    (Windows, 使用 ANGLE)
   set QT_OPENGL=desktop  (使用桌面 OpenGL)
   set QT_OPENGL=software (强制软件渲染 - 不推荐)
""")


if __name__ == "__main__":
    check_opengl()
