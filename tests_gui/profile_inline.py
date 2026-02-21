"""
带内置 CPU 分析的测试脚本

运行方式：
    python tests_gui/profile_inline.py
"""

import sys
import cProfile
import pstats
import io
import time
from pathlib import Path
import threading

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 全局分析器
profiler = cProfile.Profile()
profiler_enabled = threading.Event()
profiler_enabled.set()


def profiled_run():
    """运行带分析的测试"""
    print("=" * 60)
    print("CPU 分析模式")
    print("运行 20 秒后自动停止并输出热点")
    print("=" * 60)

    # 导入测试模块
    from tests_gui.test_network_direct import parse_args, get_device_ip_via_adb, start_server
    from scrcpy_py_ddlx.client import ScrcpyClient, ClientConfig
    from scrcpy_py_ddlx.client.config import ConnectionMode

    args = parse_args()

    # 自动检测 IP
    if args.device_ip is None:
        args.device_ip = get_device_ip_via_adb()

    if args.device_ip is None:
        print("[ERROR] 无法检测设备 IP")
        return

    print(f"[INFO] Device IP: {args.device_ip}")

    # 启动服务器
    if not start_server(args):
        print("[ERROR] 服务器启动失败")
        return

    # 创建客户端
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

    print(f"[INFO] 已连接，运行 20 秒...")
    print(f"[INFO] 请确保窗口在前台！")

    # 启动分析
    profiler.enable()

    # 运行 20 秒
    start_time = time.time()
    frame_count = 0
    while time.time() - start_time < 20:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.processEvents()
        frame_count += 1
        time.sleep(0.001)

    # 停止分析
    profiler.disable()

    print(f"\n[INFO] 处理了 {frame_count} 个事件循环")

    # 输出结果
    print("\n" + "=" * 60)
    print("CPU 热点分析 (按累计时间排序)")
    print("=" * 60)

    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
    ps.print_stats(40)
    print(s.getvalue())

    print("\n" + "=" * 60)
    print("CPU 热点分析 (按自身时间排序)")
    print("=" * 60)

    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats('time')
    ps.print_stats(30)
    print(s.getvalue())

    # 保存详细结果
    profile_file = project_root / "test_logs" / "inline_profile.txt"
    profile_file.parent.mkdir(exist_ok=True)
    with open(profile_file, 'w', encoding='utf-8') as f:
        ps = pstats.Stats(profiler, stream=f).sort_stats('cumulative')
        ps.print_stats(200)

    print(f"\n详细结果已保存到: {profile_file}")

    # 断开连接
    client.disconnect()


if __name__ == "__main__":
    try:
        profiled_run()
    except KeyboardInterrupt:
        print("\n用户中断")
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
