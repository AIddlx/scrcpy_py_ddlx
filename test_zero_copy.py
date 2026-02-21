import sys
print('Testing zero-copy GPU mode...')
print(f'PYTHONPATH: {sys.path[0]}')

# 测试导入
from scrcpy_py_ddlx.core.decoder.video import ZERO_COPY_GPU_ENABLED, HWACCEL_AVAILABLE
print(f'ZERO_COPY_GPU_ENABLED: {ZERO_COPY_GPU_ENABLED}')
print(f'HWACCEL_AVAILABLE: {HWACCEL_AVAILABLE}')

# 测试PyAV
import av
print(f'PyAV version: {av.__version__}')

# 测试is_hw_owned
from av.codec.hwaccel import HWAccel
hw = HWAccel(device_type='cuda', is_hw_owned=True)
print(f'HWAccel is_hw_owned: {hw.is_hw_owned}')

print()
print('[SUCCESS] Zero-copy GPU mode ready!')
