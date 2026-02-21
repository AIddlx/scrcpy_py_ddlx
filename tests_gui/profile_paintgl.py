"""
详细的 paintGL 性能分析

这个脚本会修改 OpenGLWidget 来添加性能监控。
运行方式：
    python tests_gui/profile_paintgl.py
"""

import sys
import time
from pathlib import Path
from collections import defaultdict

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def main():
    print("=" * 60)
    print("paintGL 详细性能分析")
    print("=" * 60)

    # 导入并猴子补丁 OpenGLWidget
    from scrcpy_py_ddlx.core.player.video import opengl_widget

    # 保存原始方法
    original_paintGL = None
    original_paint_nv12 = None
    original_paint_rgb = None

    # 计时数据
    timings = defaultdict(list)
    call_counts = defaultdict(int)

    def make_timed_wrapper(name, original_func):
        """创建计时包装器"""
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            result = original_func(*args, **kwargs)
            t1 = time.perf_counter()
            elapsed = (t1 - t0) * 1000
            timings[name].append(elapsed)
            call_counts[name] += 1
            return result
        return wrapper

    # 查找 OpenGLVideoWidget 类
    widget_class = opengl_widget.create_opengl_video_widget_class()
    if widget_class is None:
        print("OpenGL 不可用")
        return

    # 包装 paintGL
    original_paintGL = widget_class.paintGL

    def timed_paintGL(self):
        t0 = time.perf_counter()

        # 调用原始 paintGL
        original_paintGL(self)

        t1 = time.perf_counter()
        timings['paintGL_total'].append((t1 - t0) * 1000)

        # 每 60 帧输出一次
        if len(timings['paintGL_total']) % 60 == 0:
            avg = sum(timings['paintGL_total'][-60:]) / 60
            print(f"\r[paintGL] #{len(timings['paintGL_total'])}: avg={avg:.2f}ms", end='')

    widget_class.paintGL = timed_paintGL

    # 运行测试
    print("\n启动测试...")
    print("运行 15 秒后自动停止")
    print("-" * 40)

    try:
        from tests_gui.test_network_direct import parse_args, get_device_ip_via_adb, start_server
        from scrcpy_py_ddlx.client import ScrcpyClient, ClientConfig
        from scrcpy_py_ddlx.client.config import ConnectionMode

        args = parse_args()
        if args.device_ip is None:
            args.device_ip = get_device_ip_via_adb()

        if args.device_ip is None:
            print("[ERROR] 无法检测设备 IP")
            return

        if not start_server(args):
            print("[ERROR] 服务器启动失败")
            return

        config = ClientConfig(
            connection_mode=ConnectionMode.NETWORK,
            host=args.device_ip,
            control_port=args.control_port,
            video_port=args.video_port,
            audio_port=args.audio_port,
            video=True,
            codec=args.video_codec,
            bitrate=args.video_bitrate,
            max_fps=args.max_fps,
            audio=args.audio_enabled,
            show_window=True,
            control=True,
            server_jar=str(project_root / "scrcpy-server"),
        )

        client = ScrcpyClient(config)
        client.connect()

        start_time = time.time()
        while time.time() - start_time < 15:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.processEvents()
            time.sleep(0.001)

        client.disconnect()

    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()

    # 输出报告
    print(f"\n\n{'=' * 60}")
    print("性能分析报告")
    print(f"{'=' * 60}")

    for name, times in timings.items():
        if times:
            avg = sum(times) / len(times)
            max_t = max(times)
            min_t = min(times)
            print(f"\n{name}:")
            print(f"  调用次数: {len(times)}")
            print(f"  平均耗时: {avg:.3f}ms")
            print(f"  最小: {min_t:.3f}ms, 最大: {max_t:.3f}ms")

    # 分析
    if timings['paintGL_total']:
        avg_paint = sum(timings['paintGL_total']) / len(timings['paintGL_total'])
        fps_potential = 1000 / avg_paint if avg_paint > 0 else 0
        print(f"\n{'=' * 60}")
        print("分析结论")
        print(f"{'=' * 60}")
        print(f"paintGL 平均耗时: {avg_paint:.2f}ms")
        print(f"理论最大帧率: {fps_potential:.0f} fps")

        if avg_paint < 1:
            print("结论: paintGL 非常快，CPU 占用可能来自其他地方")
        elif avg_paint < 5:
            print("结论: paintGL 有一定开销，但应该不会导致高 CPU")
        else:
            print("结论: paintGL 耗时较高，需要优化渲染代码")


if __name__ == "__main__":
    main()
