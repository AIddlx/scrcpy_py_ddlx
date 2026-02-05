"""
直接引用源码的测试脚本 - 无需安装包
支持播放音视频的同时录制音频

运行方式:
    cd C:\Project\IDEA\scrcpy-py-ddlx
    python -X utf8 tests_gui/test_direct.py
"""

import sys
import logging
import time
import threading
import subprocess
import re
import socket
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 配置日志
import logging.handlers

# 创建日志文件名（带时间戳）
log_filename = f"scrcpy_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# 创建格式化器
detailed_formatter = logging.Formatter(
    fmt='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# 配置根日志记录器
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

# 清除现有的处理器
root_logger.handlers.clear()

# 文件处理器 - 详细格式
file_handler = logging.FileHandler(log_filename, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(detailed_formatter)

# 控制台处理器 - 精简格式（只显示级别和消息）
console_formatter = logging.Formatter(
    fmt='%(levelname)s - %(message)s'
)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(console_formatter)

# 添加处理器
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)

# 提示日志文件位置
print(f"[INFO] 日志将保存到: {log_filename}")
print(f"[INFO] 详细级别: DEBUG (所有日志)")
print(f"[INFO] 控制台级别: INFO (INFO及以上)")
print()

# 全局变量保持窗口和客户端存活
_global_client = None
_global_window = None
_recording_stop_event = None

# ===== 音频录制配置 =====
# 录制开关
ENABLE_AUDIO_RECORDING = False  # 改为 False 禁用自动录制

# 录制格式: 'opus', 'mp3', 'wav'
AUDIO_FORMAT = 'opus'

# 录制时长（秒），None 表示无限制（直到手动停止）
RECORDING_DURATION = 10  # 例如 10 秒

# 录制文件名（自动添加时间戳）
def get_recording_filename():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f"recording_{timestamp}.{AUDIO_FORMAT}"


def list_devices():
    """获取已连接的设备列表"""
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=5
        )
        lines = result.stdout.strip().split('\n')
        device_list = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith('List of devices'):
                parts = line.split()
                if parts:
                    device_list.append(parts[0])
        return device_list
    except Exception as e:
        logger.error(f"获取设备列表失败: {e}")
        return []


def check_adb_port(ip):
    """检查指定 IP 的 5555 端口是否开放"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        result = sock.connect_ex((ip, 5555))
        sock.close()
        return ip if result == 0 else None
    except Exception:
        return None


def auto_discover_device():
    """自动发现并连接设备"""
    print("[WARN] 未检测到已连接设备，尝试自动发现...")

    try:
        connected = False

        # 策略1: 检查是否有 USB 设备，自动启用无线
        result = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True, timeout=5)
        usb_device = None

        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith('List of devices'):
                continue
            parts = line.split()
            # USB 设备：至少 2 列，第一列不含冒号
            if len(parts) >= 2 and 'device' in line and ':' not in parts[0]:
                usb_device = parts[0]
                break

        if usb_device:
            print(f"[INFO] 检测到 USB 设备: {usb_device}")
            print("[INFO] 正在自动启用无线模式...")

            # 步骤1: 启用 TCP/IP
            tcpip_result = subprocess.run(
                ["adb", "-s", usb_device, "tcpip", "5555"],
                capture_output=True, text=True, timeout=15
            )

            if tcpip_result.returncode != 0:
                print(f"[ERROR] 启用 TCP/IP 失败: {tcpip_result.stderr}")
                return None

            print("[INFO] TCP/IP 模式已启用")

            # 步骤2: 从设备获取 IP 地址
            interfaces = ["wlan0", "wifi0", "wlan1", "eth0"]
            device_ip = None

            for interface in interfaces:
                print(f"[INFO] 正在从 {interface} 获取 IP 地址...")
                ip_result = subprocess.run(
                    ["adb", "-s", usb_device, "shell", "ip", "addr", "show", interface],
                    capture_output=True, text=True, timeout=5
                )

                # 提取 IP 地址
                for match in re.finditer(r'inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', ip_result.stdout):
                    ip = match.group(1)
                    # 过滤掉特殊地址
                    if not ip.startswith(('127.', '169.254.', '0.0.0.')):
                        device_ip = ip
                        print(f"[INFO] 找到设备 IP: {device_ip} ({interface})")
                        break
                if device_ip:
                    break

            if not device_ip:
                print("[ERROR] 无法获取设备 IP 地址")
                print("[ERROR] 请确保手机连接了 WiFi")
                return None

            # 步骤3: 建立无线连接
            wireless_addr = f"{device_ip}:5555"
            print(f"[INFO] 正在连接到 {wireless_addr}...")
            connect_result = subprocess.run(
                ["adb", "connect", wireless_addr],
                capture_output=True, text=True, timeout=10
            )

            if "connected" in connect_result.stdout.lower() or "already connected" in connect_result.stdout.lower():
                print(f"[SUCCESS] 无线连接成功: {wireless_addr}")
                print("[INFO] USB 线已可安全拔除，设备保持无线连接")
                connected = True
                return wireless_addr
            else:
                print(f"[ERROR] 无线连接失败: {connect_result.stderr}")
                return None

        # 策略2: 如果没有 USB，扫描局域网寻找 ADB 设备
        if not connected:
            print("[INFO] 未检测到 USB 设备，正在扫描局域网...")

            # 获取本机 IP 和网段
            local_ip = None
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(2)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except Exception:
                local_ip = "192.168.1.1"

            print(f"[INFO] 本机 IP: {local_ip}")

            # 提取网段（如 192.168.1.0/24）
            ip_parts = local_ip.split('.')
            network_prefix = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}"

            print(f"[INFO] 正在扫描网段 {network_prefix}.0/24 中开启 ADB 无线调试的设备...")
            print("[INFO] 正在扫描设备（这可能需要 10-30 秒）...")

            # 扫描网段内常见 IP 范围（1-254）
            found_devices = []

            with ThreadPoolExecutor(max_workers=50) as executor:
                futures = {}
                for i in range(1, 255):
                    ip = f"{network_prefix}.{i}"
                    futures[executor.submit(check_adb_port, ip)] = ip

                for future in as_completed(futures):
                    ip = futures[future]
                    try:
                        result = future.result()
                        if result:
                            found_devices.append(result)
                            print(f"[SUCCESS] 发现设备: {result}:5555")
                    except Exception:
                        pass

            if found_devices:
                print(f"[INFO] 共发现 {len(found_devices)} 个设备，正在尝试连接...")

                # 尝试连接第一个找到的设备
                for device_ip in found_devices:
                    wireless_addr = f"{device_ip}:5555"
                    print(f"[INFO] 正在连接 {wireless_addr}...")

                    connect_result = subprocess.run(
                        ["adb", "connect", wireless_addr],
                        capture_output=True, text=True, timeout=10
                    )

                    if "connected" in connect_result.stdout.lower() or "already connected" in connect_result.stdout.lower():
                        print(f"[SUCCESS] 无线连接成功: {wireless_addr}")
                        return wireless_addr
                    else:
                        print(f"[INFO] 连接 {wireless_addr} 失败")

            # 未找到设备
            print("[ERROR]")
            print("[ERROR] 未在局域网内发现开启 ADB 无线调试的设备")
            print("[ERROR]")
            print("[ERROR] 请确保：")
            print("[ERROR]   • 手机和电脑在同一网络")
            print("[ERROR]   • 手机已启用 USB 调试")
            print("[ERROR]   • 手机已通过 USB 线启用过无线调试模式（adb tcpip 5555）")
            print("[ERROR]")
            print("[ERROR] 首次使用建议：")
            print("[ERROR]   1. 用 USB 线连接手机")
            print("[ERROR]   2. 运行此脚本，它会自动启用无线模式")
            print("[ERROR]   3. 之后拔掉 USB 线，设备将保持无线连接")
            print("[ERROR]")
            return None

    except subprocess.TimeoutExpired:
        print("[ERROR] 操作超时，请检查：")
        print("[ERROR]   • USB 线是否正确连接")
        print("[ERROR]   • 手机是否已解锁")
        print("[ERROR]   • USB 调试是否已开启")
        return None
    except Exception as e:
        print(f"[ERROR] 自动发现失败: {e}")
        logger.error(f"自动发现异常: {e}", exc_info=True)
        return None


def _timed_recording_thread(duration: float, client):
    """后台线程：定时停止录制"""
    try:
        logger.info(f"[定时器] 录制将在 {duration} 秒后自动停止...")
        time.sleep(duration)
        logger.info(f"[定时器] 录制时间到，正在停止...")
        filename = client.stop_opus_recording()
        if filename:
            file_size = Path(filename).stat().st_size / 1024
            print(f"\n========================================")
            print(f"[SUCCESS] 定时录制已完成!")
            print(f"  文件名: {filename}")
            print(f"  大小: {file_size:.1f} KB")
            print(f"  格式: OGG Opus (原始 OPUS 包)")
            print(f"========================================\n")
            print("[INFO] 窗口仍可继续使用，按 Ctrl+C 或关闭窗口退出")
    except Exception as e:
        logger.error(f"[定时器] 错误: {e}")


def main():
    """主测试入口"""
    global _global_client, _global_window, _recording_stop_event

    print("=" * 60)
    print("scrcpy-py-ddlsx 音视频录制测试")
    print("=" * 60)

    # 检查依赖
    try:
        import numpy as np
        print(f"[PASS] numpy: {np.__version__}")
    except ImportError:
        print("[FAIL] numpy 未安装")
        return

    try:
        from PySide6.QtWidgets import QApplication
        print(f"[PASS] PySide6 已安装")
    except ImportError:
        print("[FAIL] PySide6 未安装")
        return

    try:
        import av
        print(f"[PASS] PyAV: {av.__version__}")
    except ImportError:
        print("[FAIL] PyAV 未安装")
        return

    # 直接导入源码模块
    try:
        from scrcpy_py_ddlx.client import ScrcpyClient, ClientConfig
        from scrcpy_py_ddlx.core.player.video import create_video_window
        print("[PASS] 源码模块导入成功")
    except ImportError as e:
        print(f"[FAIL] 源码模块导入失败: {e}")
        return

    print("\n正在创建客户端...")

    # 音频录制提示
    if ENABLE_AUDIO_RECORDING:
        print(f"[INFO] 音频录制: 启用")
        print(f"[INFO] 录制格式: {AUDIO_FORMAT.upper()}")
        if RECORDING_DURATION:
            print(f"[INFO] 录制时长: {RECORDING_DURATION} 秒 (自动停止)")
        else:
            print(f"[INFO] 录制时长: 无限制（随窗口关闭停止）")
    else:
        print("[INFO] 音频录制: 禁用")

    # 设备检测和自动发现
    print("\n正在检测设备...")
    device_list = list_devices()

    if device_list:
        print(f"[INFO] 检测到 {len(device_list)} 个已连接设备:")
        for device in device_list:
            print(f"  - {device}")
        device_id = device_list[0]
    else:
        # 尝试自动发现
        device_id = auto_discover_device()
        if not device_id:
            print("[ERROR] 无法发现设备，程序退出")
            return

    print(f"\n[INFO] 使用设备: {device_id}")

    # 创建客户端配置，指定设备序列号
    config = ClientConfig(
        device_serial=device_id,  # 指定设备
        host="localhost",
        port=27183,
        show_window=True,  # 显示视频窗口
        audio=True,  # 启用音频
        clipboard_autosync=True,  # 启用剪贴板自动同步（PC ↔ 设备）
        bitrate=8000000,  # 8 Mbps - 标准码率，保证画质
        max_fps=60,  # 60fps - 流畅帧率
    )

    # 创建客户端
    client = ScrcpyClient(config)
    _global_client = client

    print("正在连接到设备...")

    try:
        # 连接到设备
        client.connect()

        print(f"\n========================================")
        print(f"[SUCCESS] 连接成功!")
        print(f"  设备名称: {client.state.device_name}")
        print(f"  设备分辨率: {client.state.device_size[0]}x{client.state.device_size[1]}")
        print(f"========================================\n")

        # 启动音频录制 (使用原始 OPUS 包录制，零 CPU 开销)
        if ENABLE_AUDIO_RECORDING:
            recording_filename = get_recording_filename()
            print(f"[INFO] 开始音频录制 (原始 OPUS): {recording_filename}")

            if not client.start_opus_recording(recording_filename):
                print("[WARN] OPUS 录制启动失败")
            else:
                if RECORDING_DURATION:
                    # 有时长限制的录制 - 启动后台定时线程
                    print(f"[INFO] 录制中... ({RECORDING_DURATION} 秒后自动停止)")
                    _recording_stop_event = threading.Thread(
                        target=_timed_recording_thread,
                        args=(RECORDING_DURATION, client),
                        daemon=True
                    )
                    _recording_stop_event.start()
                else:
                    # 无限制录制（随窗口关闭停止）
                    print(f"[INFO] 录制中... (关闭窗口将停止录制)")

        print("\n视频窗口已显示，音频正在录制，你可以:")
        print("  - 使用鼠标点击/拖拽控制设备")
        print("  - 使用键盘输入文字")
        print("  - 使用滚轮滚动")
        print("\n关闭窗口或按 Ctrl+C 断开连接...")

        # 使用Qt事件循环运行客户端
        # 这将启动Qt事件循环来处理视频渲染和用户输入
        client.run_with_qt()

    except KeyboardInterrupt:
        print("\n\n用户中断连接")
    except Exception as e:
        print(f"\n[ERROR] 连接失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 清理
        print("\n正在清理...")

        # 停止音频录制（如果还在录制中）
        if ENABLE_AUDIO_RECORDING and client:
            try:
                filename = client.stop_opus_recording()
                if filename:
                    file_size = Path(filename).stat().st_size / 1024
                    print(f"\n========================================")
                    print(f"[SUCCESS] OPUS 录制已保存!")
                    print(f"  文件名: {filename}")
                    print(f"  大小: {file_size:.1f} KB")
                    print(f"  格式: OGG Opus (原始 OPUS 包)")
                    print(f"========================================")
            except Exception as e:
                print(f"[WARN] 停止录制时出错: {e}")

        if _global_client is not None:
            try:
                _global_client.disconnect()
            except:
                pass
        print("测试完成")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[FATAL] {e}")
        import traceback
        traceback.print_exc()
