"""
无缝无线连接测试脚本

使用 tcpip=True 和 stay_awake=True 配置：
1. USB 连接时自动启用 TCP/IP 无线模式
2. 拔掉 USB 后自动切换到 WiFi
3. 保持设备唤醒状态

运行方式:
    cd C:\Project\IDEA\2\scrcpy-py-ddlx
    python -X utf8 tests_gui/test_seamless_wireless.py
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 配置日志
log_filename = f"seamless_wireless_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

import logging.handlers
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
root_logger.handlers.clear()

detailed_formatter = logging.Formatter(
    fmt='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

file_handler = logging.FileHandler(log_filename, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(detailed_formatter)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))

root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)
print(f"[INFO] 日志将保存到: {log_filename}")
print()


def main():
    """测试无缝无线连接"""

    print("=" * 60)
    print("无缝无线连接测试")
    print("=" * 60)
    print()
    print("功能说明:")
    print("  - USB 连接时自动启用 TCP/IP 无线模式")
    print("  - 拔掉 USB 后自动切换到 WiFi")
    print("  - 保持设备唤醒状态")
    print()

    # 导入模块
    try:
        from scrcpy_py_ddlx.client import ScrcpyClient, ClientConfig
        from scrcpy_py_ddlx.core.player.video import create_video_window
        print("[PASS] 模块导入成功")
    except ImportError as e:
        print(f"[FAIL] 模块导入失败: {e}")
        return

    # 创建客户端配置 - 启用无缝无线连接
    config = ClientConfig(
        # 无缝无线连接配置
        tcpip=True,           # 启用 TCP/IP 无线模式
        stay_awake=True,      # 保持设备唤醒

        # 音频和视频
        show_window=True,     # 显示视频窗口
        audio=True,           # 启用音频

        # 其他配置
        clipboard_autosync=True,
        bitrate=8000000,
        max_fps=60,
    )

    print()
    print("[INFO] 配置:")
    print(f"  - tcpip: {config.tcpip}")
    print(f"  - stay_awake: {config.stay_awake}")
    print(f"  - audio: {config.audio}")
    print(f"  - show_window: {config.show_window}")
    print()

    # 创建客户端
    client = ScrcpyClient(config)

    print("[INFO] 正在连接...")
    print()
    print("提示:")
    print("  1. 如果有 USB 设备，会自动启用 TCP/IP 无线模式")
    print("  2. 如果已有 TCP/IP 连接，会直接使用")
    print("  3. 连接成功后，可以拔掉 USB 线")
    print()

    try:
        # 连接到设备
        client.connect()

        print()
        print("=" * 60)
        print("[SUCCESS] 连接成功!")
        print("=" * 60)
        print(f"  设备名称: {client.state.device_name}")
        print(f"  设备分辨率: {client.state.device_size[0]}x{client.state.device_size[1]}")
        if client.state.tcpip_connected:
            print(f"  TCP/IP: {client.state.tcpip_ip}:{client.state.tcpip_port}")
        print()
        print("提示: 现在可以拔掉 USB 线，连接将自动切换到 WiFi")
        print("=" * 60)
        print()

        print("视频窗口已显示。关闭窗口或按 Ctrl+C 断开连接...")

        # 使用 Qt 事件循环
        client.run_with_qt()

    except KeyboardInterrupt:
        print()
        print("[INFO] 用户中断连接")
    except Exception as e:
        print()
        print(f"[ERROR] 连接失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print()
        print("[INFO] 正在断开连接...")
        try:
            client.disconnect()
            print("[SUCCESS] 已断开")
        except:
            pass


if __name__ == "__main__":
    main()
