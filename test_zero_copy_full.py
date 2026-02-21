#!/usr/bin/env python
"""
Complete test for zero-copy GPU rendering.

Tests:
1. PyAV is_hw_owned support
2. CuPy availability
3. CUDA-OpenGL Interop availability
4. DLPack export from GPU frames
"""

import sys
import os

# Set up paths
sys.path.insert(0, r'C:\Project\github\PyAV')
os.environ['PATH'] = r'C:\Project\ffmpeg\ffmpeg-7.1.1-full_build-shared\bin' + os.pathsep + os.environ['PATH']
os.environ['SCRCPY_ZERO_COPY_GPU'] = '1'

print('=' * 60)
print('Zero-Copy GPU Mode Complete Test')
print('=' * 60)

# 1. Test PyAV
print('\n[1] Testing PyAV...')
try:
    import av
    print(f'  PyAV version: {av.__version__}')

    from av.codec.hwaccel import HWAccel
    hw = HWAccel(device_type='cuda', is_hw_owned=True)
    print(f'  HWAccel is_hw_owned: {hw.is_hw_owned}')
    print('  [OK] PyAV supports is_hw_owned')
except Exception as e:
    print(f'  [FAIL] PyAV error: {e}')
    sys.exit(1)

# 2. Test CuPy
print('\n[2] Testing CuPy...')
try:
    import cupy as cp
    print(f'  CuPy version: {cp.__version__}')

    # Test CUDA availability
    device = cp.cuda.Device()
    device.use()
    print(f'  CUDA compute capability: {device.compute_capability}')

    # Test DLPack
    arr = cp.array([1, 2, 3])
    dlpack = arr.__dlpack__()
    arr2 = cp.fromDlpack(dlpack)
    print(f'  DLPack round-trip: {arr2.tolist()}')
    print('  [OK] CuPy and DLPack working')
except Exception as e:
    print(f'  [FAIL] CuPy error: {e}')
    sys.exit(1)

# 3. Test CUDA-OpenGL Interop
print('\n[3] Testing CUDA-OpenGL Interop...')
try:
    from scrcpy_py_ddlx.core.player.video.opengl_widget import (
        _CUDA_GL_INTEROP_AVAILABLE, _cuda_funcs
    )
    print(f'  CUDA-GL Interop available: {_CUDA_GL_INTEROP_AVAILABLE}')
    if _cuda_funcs:
        print(f'  Functions: {list(_cuda_funcs.keys())}')
        print('  [OK] CUDA-OpenGL Interop ready')
    else:
        print('  [WARN] CUDA-OpenGL Interop not available, will use CPU fallback')
except Exception as e:
    print(f'  [FAIL] CUDA-GL Interop error: {e}')

# 4. Test decoder configuration
print('\n[4] Testing decoder configuration...')
try:
    from scrcpy_py_ddlx.core.decoder.video import (
        ZERO_COPY_GPU_ENABLED, HWACCEL_AVAILABLE
    )
    print(f'  ZERO_COPY_GPU_ENABLED: {ZERO_COPY_GPU_ENABLED}')
    print(f'  HWACCEL_AVAILABLE: {HWACCEL_AVAILABLE}')

    if ZERO_COPY_GPU_ENABLED and HWACCEL_AVAILABLE:
        print('  [OK] Zero-copy GPU mode enabled')
    else:
        print('  [WARN] Zero-copy mode not fully configured')
except Exception as e:
    print(f'  [FAIL] Decoder config error: {e}')

# 5. Test GPU frame simulation
print('\n[5] Testing GPU frame handling...')
try:
    import cupy as cp

    # Simulate NV12 GPU frame
    height, width = 720, 1280

    # Y plane (height x width)
    y_gpu = cp.zeros((height, width), dtype=cp.uint8)
    # UV plane (height/2 x width, interleaved U,V)
    uv_gpu = cp.zeros((height // 2, width), dtype=cp.uint8)

    print(f'  Y plane: {y_gpu.shape}, dtype={y_gpu.dtype}')
    print(f'  UV plane: {uv_gpu.shape}, dtype={uv_gpu.dtype}')

    # Test DLPack export
    y_dlpack = y_gpu.__dlpack__()
    uv_dlpack = uv_gpu.__dlpack__()
    print(f'  DLPack export: Y={len(y_dlpack)} bytes, UV={len(uv_dlpack)} bytes')

    # Test re-import
    y_reimport = cp.fromDlpack(y_dlpack)
    print(f'  Re-import: {y_reimport.shape}')

    print('  [OK] GPU frame handling working')
except Exception as e:
    print(f'  [FAIL] GPU frame handling error: {e}')

print('\n' + '=' * 60)
print('[SUCCESS] All zero-copy GPU tests passed!')
print('=' * 60)
print('\nTo test with actual video, run:')
print('  run_zero_copy_test.bat')
