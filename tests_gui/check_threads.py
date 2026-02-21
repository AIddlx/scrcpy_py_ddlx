"""
检查运行时的线程数
在测试脚本运行时，在另一个终端执行：
    python tests_gui/check_threads.py
"""
import subprocess
import time

result = subprocess.run(
    ['tasklist', '/fi', 'imagename eq python.exe', '/fo', 'csv'],
    capture_output=True, text=True
)
print("Python 进程:")
print(result.stdout)

print("\n请在测试脚本运行时执行以下命令查看线程数:")
print("  wmic process where name='python.exe' get processid,threadcount")
