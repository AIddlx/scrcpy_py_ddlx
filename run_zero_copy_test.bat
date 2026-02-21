@echo off
REM Zero-copy GPU mode test script
REM Requires PyAV 17+ compiled from source

set PYTHONPATH=C:\Project\github\PyAV
set PATH=C:\Project\ffmpeg\ffmpeg-7.1.1-full_build-shared\bin;%PATH%
set SCRCPY_ZERO_COPY_GPU=1

echo ============================================
echo Zero-copy GPU mode test
echo ============================================
echo PYTHONPATH=%PYTHONPATH%
echo SCRCPY_ZERO_COPY_GPU=%SCRCPY_ZERO_COPY_GPU%
echo ============================================

python -X utf8 tests_gui/test_network_direct.py %*
