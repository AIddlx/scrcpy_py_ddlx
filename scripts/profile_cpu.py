"""
CPU 性能分析脚本

使用方法：
1. 安装 py-spy: pip install py-spy
2. 运行目标程序
3. 在另一个终端运行此脚本

或者直接使用 cProfile：
    python -m cProfile -o profile.stats tests_gui/test_network_direct.py --bitrate 2500000 --max-fps 30 --codec h265
    python scripts/profile_cpu.py analyze profile.stats
"""

import sys
import subprocess
import os
from pathlib import Path

def profile_with_pyspy(pid=None, duration=10):
    """
    使用 py-spy 分析正在运行的进程。

    py-spy 是采样分析器，开销极低，不需要修改代码。
    """
    print("=" * 60)
    print("py-spy CPU 分析")
    print("=" * 60)

    if pid:
        # 分析指定进程
        cmd = ["py-spy", "top", "--pid", str(pid), "--duration", str(duration)]
    else:
        # 分析所有 Python 进程
        cmd = ["py-spy", "dump", "--pid", str(pid)] if pid else ["py-spy", "top", "--python"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 5)
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
    except FileNotFoundError:
        print("py-spy 未安装。请运行: pip install py-spy")
    except Exception as e:
        print(f"错误: {e}")


def analyze_cprofile_stats(stats_file):
    """
    分析 cProfile 生成的 .stats 文件。
    """
    import pstats

    print("=" * 60)
    print("cProfile 分析结果")
    print("=" * 60)

    if not os.path.exists(stats_file):
        print(f"文件不存在: {stats_file}")
        return

    stats = pstats.Stats(stats_file)

    # 按累计时间排序（找出哪些函数占用最多 CPU）
    print("\n按累计时间排序 (cumulative time) - 找出热点函数:")
    print("-" * 60)
    stats.sort_stats('cumulative')
    stats.print_stats(30)  # 显示前 30 个

    # 按自身时间排序（找出哪些函数本身消耗最多 CPU，不包括调用的函数）
    print("\n按自身时间排序 (self time) - 找出真正的热点:")
    print("-" * 60)
    stats.sort_stats('time')
    stats.print_stats(30)

    # 按调用次数排序
    print("\n按调用次数排序 (call count):")
    print("-" * 60)
    stats.sort_stats('calls')
    stats.print_stats(20)


def profile_with_yappi(script_path, *args):
    """
    使用 yappi 进行线程感知的性能分析。

    yappi 支持：
    - 多线程分析
    - CPU 时间 vs 实际时间
    - 按线程分组
    """
    print("=" * 60)
    print("yappi CPU 分析")
    print("=" * 60)

    try:
        import yappi
    except ImportError:
        print("yappi 未安装。请运行: pip install yappi")
        return

    # 创建包装脚本
    wrapper = f'''
import yappi
import sys

# 添加项目路径
sys.path.insert(0, r"{Path(__file__).parent.parent}")

# 开始分析
yappi.set_clock_type("cpu")  # 使用 CPU 时间
yappi.start()

# 运行目标脚本
exec(open(r"{script_path}").read())

# 停止并输出结果
yappi.stop()
print()
print("=" * 60)
print("yappi 分析结果 (按总时间排序)")
print("=" * 60)
yappi.get_func_stats().print_all()
'''

    wrapper_path = Path(__file__).parent / "_yappi_wrapper.py"
    with open(wrapper_path, 'w', encoding='utf-8') as f:
        f.write(wrapper)

    try:
        subprocess.run([sys.executable, str(wrapper_path)] + list(args))
    finally:
        if wrapper_path.exists():
            wrapper_path.unlink()


def quick_profile():
    """
    快速性能检查 - 找出当前项目中最可能的 CPU 热点。
    """
    project_root = Path(__file__).parent.parent

    print("=" * 60)
    print("项目 CPU 热点扫描")
    print("=" * 60)

    # 扫描可能的高 CPU 模式
    patterns = {
        "高频定时器": (r"\.start\(\d+\)", lambda m: int(m.group(1)) < 10 if m.group(1).isdigit() else False),
        "忙等待循环": (r"while\s+True:", None),
        "频繁 tobytes": (r"\.tobytes\(\)", None),
        "频繁 copy": (r"\.copy\(\)", None),
        "频繁日志": (r"logger\.(info|debug|warning)\(", None),
    }

    import re

    for name, (pattern, extra_check) in patterns.items():
        print(f"\n{name}:")
        print("-" * 40)

        for py_file in project_root.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue

            try:
                content = py_file.read_text(encoding='utf-8')
                matches = list(re.finditer(pattern, content))

                if matches:
                    rel_path = py_file.relative_to(project_root)
                    count = len(matches)

                    # 提取一些示例行
                    examples = []
                    for m in matches[:3]:
                        line_num = content[:m.start()].count('\n') + 1
                        line = content.split('\n')[line_num - 1].strip()
                        examples.append(f"  L{line_num}: {line[:60]}...")

                    print(f"  {rel_path}: {count} 处")
                    for ex in examples:
                        print(ex)
            except Exception:
                pass


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n可用命令:")
        print("  profile_cpu.py pyspy <pid> [duration]  - 使用 py-spy 分析进程")
        print("  profile_cpu.py analyze <stats_file>     - 分析 cProfile 输出")
        print("  profile_cpu.py scan                     - 扫描项目中的潜在热点")
        return

    command = sys.argv[1]

    if command == "pyspy":
        if len(sys.argv) < 3:
            print("用法: profile_cpu.py pyspy <pid> [duration]")
            return
        pid = int(sys.argv[2])
        duration = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        profile_with_pyspy(pid, duration)

    elif command == "analyze":
        if len(sys.argv) < 3:
            print("用法: profile_cpu.py analyze <stats_file>")
            return
        analyze_cprofile_stats(sys.argv[2])

    elif command == "scan":
        quick_profile()

    else:
        print(f"未知命令: {command}")
        print("可用命令: pyspy, analyze, scan")


if __name__ == "__main__":
    main()
