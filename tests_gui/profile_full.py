"""
全面 CPU 性能分析

运行方式：
    python tests_gui/profile_full.py
"""

import sys
import time
import threading
import psutil
from pathlib import Path
from collections import defaultdict

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class FullProfiler:
    """全面的性能分析器"""

    def __init__(self):
        self.process = psutil.Process()
        self.samples = []
        self.running = True
        self.start_time = None

    def get_thread_info(self):
        """获取所有线程信息"""
        try:
            threads = self.process.threads()
            return {t.id: {'user': t.user_time, 'system': t.system_time} for t in threads}
        except:
            return {}

    def monitor_loop(self):
        """监控循环"""
        last_threads = self.get_thread_info()
        last_time = time.time()
        self.start_time = last_time

        while self.running:
            time.sleep(0.5)

            now = time.time()
            current_threads = self.get_thread_info()
            cpu_percent = self.process.cpu_percent()

            # 计算每个线程的 CPU 使用
            thread_cpu = {}
            for tid, tinfo in current_threads.items():
                if tid in last_threads:
                    elapsed = now - last_time
                    user_delta = tinfo['user'] - last_threads[tid]['user']
                    sys_delta = tinfo['system'] - last_threads[tid]['system']
                    total = (user_delta + sys_delta) / elapsed * 100
                    thread_cpu[tid] = total

            self.samples.append({
                'time': now,
                'cpu': cpu_percent,
                'threads': thread_cpu
            })

            last_threads = current_threads
            last_time = now

            # 输出
            total_thread_cpu = sum(thread_cpu.values())
            print(f"\r[CPU] 进程: {cpu_percent:.1f}% | 线程总计: {total_thread_cpu:.1f}% | "
                  f"线程数: {len(thread_cpu)}", end='')

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

        # 汇总所有线程的 CPU 使用
        thread_totals = defaultdict(float)
        thread_counts = defaultdict(int)
        for s in self.samples:
            for tid, cpu in s['threads'].items():
                thread_totals[tid] += cpu
                thread_counts[tid] += 1

        print(f"\n\n{'=' * 60}")
        print("全面 CPU 分析报告")
        print(f"{'=' * 60}")
        print(f"采样数: {len(self.samples)}")
        print(f"运行时间: {self.samples[-1]['time'] - self.start_time:.1f}s")
        print(f"进程平均 CPU: {avg_cpu:.1f}%")
        print(f"进程最高 CPU: {max_cpu:.1f}%")

        # 按平均 CPU 排序线程
        thread_avgs = [(tid, thread_totals[tid] / thread_counts[tid])
                       for tid in thread_totals]
        thread_avgs.sort(key=lambda x: x[1], reverse=True)

        print(f"\n线程 CPU 排名（前 10）:")
        print("-" * 40)
        for i, (tid, avg) in enumerate(thread_avgs[:10]):
            print(f"  #{i+1} 线程 {tid}: {avg:.2f}%")


def main():
    print("=" * 60)
    print("全面 CPU 性能分析")
    print("运行 20 秒后自动停止")
    print("=" * 60)

    profiler = FullProfiler()
    profiler.start()

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

        # 记录启动前的线程数
        initial_threads = threading.active_count()
        print(f"[INFO] 初始线程数: {initial_threads}")

        client = ScrcpyClient(config)
        client.connect()

        after_connect_threads = threading.active_count()
        print(f"[INFO] 连接后线程数: {after_connect_threads}")
        print(f"[INFO] 新增线程: {after_connect_threads - initial_threads}")
        print(f"[INFO] 请确保窗口在前台！")
        print()

        # 主循环
        start_time = time.time()
        loop_count = 0

        while time.time() - start_time < 20:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.processEvents()
            loop_count += 1
            time.sleep(0.001)

        profiler.stop()
        profiler.report()

        # 额外信息
        print(f"\n{'=' * 60}")
        print("线程信息")
        print(f"{'=' * 60}")
        print(f"当前活跃线程: {threading.active_count()}")

        for t in threading.enumerate():
            print(f"  - {t.name} (daemon={t.daemon})")

        client.disconnect()

    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        profiler.stop()


if __name__ == "__main__":
    main()
