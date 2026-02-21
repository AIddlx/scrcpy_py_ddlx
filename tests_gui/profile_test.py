"""
CPU 性能分析启动器

使用方法:
    python tests_gui/profile_test.py

运行 30 秒后会自动生成分析结果。
"""

import sys
import cProfile
import pstats
import io
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def main():
    print("=" * 60)
    print("CPU 性能分析")
    print("运行 30 秒后自动停止并输出结果")
    print("=" * 60)

    # 创建分析器
    profiler = cProfile.Profile()

    # 开始分析
    profiler.enable()

    # 导入并运行测试
    import argparse
    test_args = [
        '--bitrate', '2500000',
        '--max-fps', '30',
        '--codec', 'h265',
    ]

    # 解析参数
    sys.argv = ['profile_test.py'] + test_args

    # 导入测试模块
    from tests_gui.test_network_direct import parse_args, get_device_ip_via_adb, start_server, setup_logging
    from scrcpy_py_ddlx.client import ScrcpyClient, ClientConfig
    from scrcpy_py_ddlx.client.config import ConnectionMode
    import time

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

    print(f"[INFO] 已连接，运行 30 秒...")

    # 运行 30 秒
    start_time = time.time()
    while time.time() - start_time < 30:
        # 处理 Qt 事件
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.processEvents()
        time.sleep(0.01)

    # 停止分析
    profiler.disable()

    # 输出结果
    print("\n" + "=" * 60)
    print("分析结果")
    print("=" * 60)

    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
    ps.print_stats(50)

    print(s.getvalue())

    # 保存到文件
    profile_file = project_root / "test_logs" / "cpu_profile.txt"
    profile_file.parent.mkdir(exist_ok=True)
    with open(profile_file, 'w', encoding='utf-8') as f:
        ps = pstats.Stats(profiler, stream=f).sort_stats('cumulative')
        ps.print_stats(100)

    print(f"\n详细结果已保存到: {profile_file}")

    # 断开连接
    client.disconnect()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n用户中断")
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
