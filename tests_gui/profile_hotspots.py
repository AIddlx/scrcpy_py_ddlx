"""
精确定位 CPU 热点

运行测试程序后，在另一个终端执行：
    python tests_gui/profile_hotspots.py <pid>
"""

import sys
import os
import time

def profile_with_cprofile_injection(pid: int, duration: int = 15):
    """
    通过注入代码到目标进程来分析。
    这需要在启动目标程序时启用。
    """
    print("=" * 60)
    print(f"建议：使用 cProfile 启动目标程序")
    print("=" * 60)
    print()
    print("方法 1：使用内置 profile 测试")
    print("-" * 40)
    print("  python tests_gui/profile_test.py")
    print()
    print("方法 2：使用 yappi（需要安装：pip install yappi）")
    print("-" * 40)
    print("  import yappi")
    print("  yappi.set_clock_type('cpu')")
    print("  yappi.start()")
    print("  # ... 运行代码 ...")
    print("  yappi.stop()")
    print("  yappi.get_func_stats().print_all()")


def analyze_hotspot_patterns():
    """
    扫描代码中可能导致高 CPU 的模式。
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print("=" * 60)
    print("代码热点模式扫描")
    print("=" * 60)

    import re

    patterns = {
        "ascontiguousarray (数组复制)": r"np\.ascontiguousarray|numpy\.ascontiguousarray",
        "tobytes (序列化)": r"\.tobytes\(\)",
        "copy() (复制)": r"\.copy\(\)",
        "高频定时器 (<10ms)": r"\.start\(\s*[1-9]\s*\)",
        "while True 循环": r"while\s+True\s*:",
        "日志调用": r"logger\.(info|debug|warning)\([^)]*\)",
    }

    # 关键目录
    dirs_to_scan = [
        os.path.join(project_root, "scrcpy_py_ddlx", "core", "decoder"),
        os.path.join(project_root, "scrcpy_py_ddlx", "core", "player", "video"),
        os.path.join(project_root, "scrcpy_py_ddlx"),
    ]

    results = {}

    for pattern_name, pattern in patterns.items():
        results[pattern_name] = []

        for scan_dir in dirs_to_scan:
            if not os.path.exists(scan_dir):
                continue

            for root, dirs, files in os.walk(scan_dir):
                # 跳过 __pycache__
                dirs[:] = [d for d in dirs if d != "__pycache__"]

                for file in files:
                    if not file.endswith(".py"):
                        continue

                    filepath = os.path.join(root, file)
                    relpath = os.path.relpath(filepath, project_root)

                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            content = f.read()
                            lines = content.split('\n')

                        matches = list(re.finditer(pattern, content))
                        if matches:
                            for m in matches[:5]:  # 每个文件最多5个
                                line_num = content[:m.start()].count('\n') + 1
                                line = lines[line_num - 1].strip()
                                results[pattern_name].append({
                                    'file': relpath,
                                    'line': line_num,
                                    'code': line[:80]
                                })
                    except Exception:
                        pass

    # 输出结果
    for pattern_name, matches in results.items():
        if matches:
            print(f"\n{pattern_name}: {len(matches)} 处")
            print("-" * 40)
            for m in matches[:10]:  # 每类最多显示10个
                print(f"  {m['file']}:{m['line']}")
                print(f"    {m['code']}")


def check_buffer_contiguity():
    """
    检查预分配缓冲区的连续性。
    """
    print("\n" + "=" * 60)
    print("验证预分配缓冲区的连续性")
    print("=" * 60)

    import numpy as np

    # 模拟 video.py 中的预分配
    w, h = 2800, 1264
    y_buffer = np.empty((h, w), dtype=np.uint8)
    u_buffer = np.empty((h // 2, w // 2), dtype=np.uint8)
    v_buffer = np.empty((h // 2, w // 2), dtype=np.uint8)

    print(f"Y buffer: shape={y_buffer.shape}, C_CONTIGUOUS={y_buffer.flags['C_CONTIGUOUS']}")
    print(f"U buffer: shape={u_buffer.shape}, C_CONTIGUOUS={u_buffer.flags['C_CONTIGUOUS']}")
    print(f"V buffer: shape={v_buffer.shape}, C_CONTIGUOUS={v_buffer.flags['C_CONTIGUOUS']}")

    # 模拟 opengl_widget.py 中的步长切片
    uv_plane = np.empty((h // 2, w), dtype=np.uint8)
    u_slice = uv_plane[::2, :]  # 步长切片
    v_slice = uv_plane[1::2, :]

    print(f"\n步长切片后:")
    print(f"U slice: C_CONTIGUOUS={u_slice.flags['C_CONTIGUOUS']}")
    print(f"V slice: C_CONTIGUOUS={v_slice.flags['C_CONTIGUOUS']}")

    print("\n结论：")
    print("- np.empty() 创建的缓冲区是 C_CONTIGUOUS ✓")
    print("- 步长切片 [::2, :] 创建的视图是 非 C_CONTIGUOUS ✗")
    print("- 这就是 opengl_widget.py 中 ascontiguousarray 被调用的原因！")


def main():
    print(__doc__)

    if len(sys.argv) < 2:
        analyze_hotspot_patterns()
        check_buffer_contiguity()
    else:
        try:
            pid = int(sys.argv[1])
            duration = int(sys.argv[2]) if len(sys.argv) > 2 else 15
            profile_with_cprofile_injection(pid, duration)
        except ValueError:
            print("用法: python profile_hotspots.py <pid> [duration]")


if __name__ == "__main__":
    main()
