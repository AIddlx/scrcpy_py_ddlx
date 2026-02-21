"""
内置 CPU 监控的测试脚本

运行方式：
    python tests_gui/monitor_cpu.py
"""

import sys
import time
import threading
import psutil
from pathlib import Path
from collections import defaultdict

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 全局监控数据
class CPUMonitor:
    def __init__(self):
        self.process = psutil.Process()
        self.running = True
        self.samples = []
        self.thread_times = defaultdict(list)

    def get_thread_cpu(self):
        """获取每个线程的 CPU 时间"""
        try:
            threads = self.process.threads()
            return {t.id: t.user_time + t.system_time for t in threads}
        except:
            return {}

    def monitor_loop(self):
        """监控循环"""
        last_thread_times = self.get_thread_cpu()
        last_time = time.time()

        while self.running:
            time.sleep(0.5)

            now = time.time()
            current_thread_times = self.get_thread_cpu()
            cpu_percent = self.process.cpu_percent()

            # 计算每个线程的 CPU 使用
            thread_cpu = {}
            for tid, ttime in current_thread_times.items():
                if tid in last_thread_times:
                    elapsed = now - last_time
                    thread_cpu_pct = (ttime - last_thread_times[tid]) / elapsed * 100
                    thread_cpu[tid] = thread_cpu_pct

            self.samples.append({
                'time': now,
                'cpu': cpu_percent,
                'threads': thread_cpu
            })

            last_thread_times = current_thread_times
            last_time = now

            # 实时输出
            if len(self.samples) % 2 == 0:  # 每秒输出一次
                print(f"\r[CPU] 总计: {cpu_percent:.1f}% | "
                      f"线程数: {len(thread_cpu)} | "
                      f"主线程: {max(thread_cpu.values()) if thread_cpu else 0:.1f}%", end='')

    def start(self):
        self.thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.thread.join(timeout=1)

    def report(self):
        """输出报告"""
        if not self.samples:
            return

        avg_cpu = sum(s['cpu'] for s in self.samples) / len(self.samples)
        max_cpu = max(s['cpu'] for s in self.samples)

        print(f"\n\n{'=' * 60}")
        print("CPU 监控报告")
        print(f"{'=' * 60}")
        print(f"采样数: {len(self.samples)}")
        print(f"平均 CPU: {avg_cpu:.1f}%")
        print(f"最高 CPU: {max_cpu:.1f}%")


class TimingContext:
    """计时上下文管理器"""
    def __init__(self, name, timings):
        self.name = name
        self.timings = timings
        self.start = None

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *args):
        elapsed = (time.perf_counter() - self.start) * 1000
        self.timings[self.name].append(elapsed)


def main():
    print("=" * 60)
    print("内置 CPU 监控测试")
    print("运行 20 秒后自动停止")
    print("=" * 60)

    # 启动 CPU 监控
    monitor = CPUMonitor()
    monitor.start()

    # 计时数据
    timings = defaultdict(list)

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

        print(f"[INFO] Device IP: {args.device_ip}")

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

        print(f"[INFO] 已连接，运行 20 秒...")
        print(f"[INFO] 请确保窗口在前台！")
        print()

        # 主循环
        start_time = time.time()
        loop_count = 0

        while time.time() - start_time < 20:
            with TimingContext('qt_processEvents', timings):
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance()
                if app:
                    app.processEvents()

            loop_count += 1

            # 每 5 秒输出详细计时
            if loop_count % 500 == 0:
                print(f"\n[详细计时] 循环 #{loop_count}:")
                for name, times in timings.items():
                    if times:
                        avg = sum(times[-100:]) / min(len(times), 100)
                        print(f"  {name}: avg={avg:.3f}ms, samples={len(times)}")

        # 停止监控
        monitor.stop()
        monitor.report()

        # 输出计时统计
        print(f"\n{'=' * 60}")
        print("操作耗时统计")
        print(f"{'=' * 60}")
        for name, times in timings.items():
            if times:
                avg = sum(times) / len(times)
                max_t = max(times)
                min_t = min(times)
                print(f"{name}:")
                print(f"  平均: {avg:.3f}ms, 最小: {min_t:.3f}ms, 最大: {max_t:.3f}ms")
                print(f"  样本数: {len(times)}")

        client.disconnect()

    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        monitor.stop()


if __name__ == "__main__":
    main()
