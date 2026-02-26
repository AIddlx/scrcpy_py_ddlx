@echo off
REM 文件传输功能测试脚本 - 支持网络模式
REM 使用方法: test_file_transfer_network.bat

setlocal enabledelayedexpansion
set MCP_URL=http://127.0.0.1:3359/mcp

echo ================================================================
echo   文件传输功能测试 - ADB 模式 + 网络模式
echo ================================================================
echo.

REM 切换到 UTF-8 编码
chcp 65001 >nul 2>&1

REM 测试计数
set PASS=0
set FAIL=0

echo ================================================================
echo   第一部分: 测试当前模式的文件传输
echo ================================================================
echo.

echo ========== 1. 测试 list_dir ==========
echo [测试] 列出 /sdcard 目录...
curl -s -X POST %MCP_URL% -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"list_dir\",\"arguments\":{\"path\":\"/sdcard\"}}}" > test_result.tmp
type test_result.tmp
findstr "success\": true" test_result.tmp >nul
if !errorlevel! equ 0 (
    echo [通过] list_dir 测试成功
    set /a PASS+=1
    REM 检测模式
    findstr "mode\": \"network\"" test_result.tmp >nul
    if !errorlevel! equ 0 (
        echo [信息] 当前为网络模式
        set MODE=network
    ) else (
        echo [信息] 当前为 ADB 模式
        set MODE=adb
    )
) else (
    echo [失败] list_dir 测试失败
    set /a FAIL+=1
)
echo.

echo ========== 2. 测试 file_stat ==========
echo [测试] 获取 /sdcard/test_adb.png 文件信息...
curl -s -X POST %MCP_URL% -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"file_stat\",\"arguments\":{\"device_path\":\"/sdcard/test_adb.png\"}}}" > test_result.tmp
type test_result.tmp
findstr "success\": true" test_result.tmp >nul
if !errorlevel! equ 0 (
    echo [通过] file_stat 测试成功
    set /a PASS+=1
) else (
    echo [失败] file_stat 测试失败
    set /a FAIL+=1
)
echo.

echo ========== 3. 测试 make_dir ==========
echo [测试] 创建目录 /sdcard/test_scrcpy_dir...
curl -s -X POST %MCP_URL% -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"make_dir\",\"arguments\":{\"device_path\":\"/sdcard/test_scrcpy_dir\"}}}" > test_result.tmp
type test_result.tmp
findstr "success\": true" test_result.tmp >nul
if !errorlevel! equ 0 (
    echo [通过] make_dir 测试成功
    set /a PASS+=1
) else (
    echo [失败] make_dir 测试失败
    set /a FAIL+=1
)
echo.

echo ========== 4. 测试 push_file ==========
echo [测试] 创建本地测试文件...
echo This is a test file from scrcpy-py-ddlx network mode test. > test_upload.txt
echo [测试] 上传文件到 /sdcard/test_scrcpy_dir/test_upload.txt...
curl -s -X POST %MCP_URL% -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"push_file\",\"arguments\":{\"local_path\":\"test_upload.txt\",\"device_path\":\"/sdcard/test_scrcpy_dir/test_upload.txt\"}}}" > test_result.tmp
type test_result.tmp
findstr "success\": true" test_result.tmp >nul
if !errorlevel! equ 0 (
    echo [通过] push_file 测试成功
    set /a PASS+=1
) else (
    echo [失败] push_file 测试失败
    set /a FAIL+=1
)
echo.

echo ========== 5. 测试 pull_file ==========
echo [测试] 下载文件到 test_download.txt...
curl -s -X POST %MCP_URL% -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":5,\"method\":\"tools/call\",\"params\":{\"name\":\"pull_file\",\"arguments\":{\"device_path\":\"/sdcard/test_scrcpy_dir/test_upload.txt\",\"local_path\":\"test_download.txt\"}}}" > test_result.tmp
type test_result.tmp
findstr "success\": true" test_result.tmp >nul
if !errorlevel! equ 0 (
    echo [通过] pull_file 测试成功
    set /a PASS+=1
    echo [验证] 下载的文件内容:
    type test_download.txt
) else (
    echo [失败] pull_file 测试失败
    set /a FAIL+=1
)
echo.

echo ========== 6. 测试 delete_file (文件) ==========
echo [测试] 删除文件 /sdcard/test_scrcpy_dir/test_upload.txt...
curl -s -X POST %MCP_URL% -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":6,\"method\":\"tools/call\",\"params\":{\"name\":\"delete_file\",\"arguments\":{\"device_path\":\"/sdcard/test_scrcpy_dir/test_upload.txt\"}}}" > test_result.tmp
type test_result.tmp
findstr "success\": true" test_result.tmp >nul
if !errorlevel! equ 0 (
    echo [通过] delete_file 测试成功
    set /a PASS+=1
) else (
    echo [失败] delete_file 测试失败
    set /a FAIL+=1
)
echo.

echo ========== 7. 测试 delete_file (目录) ==========
echo [测试] 删除目录 /sdcard/test_scrcpy_dir...
curl -s -X POST %MCP_URL% -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":7,\"method\":\"tools/call\",\"params\":{\"name\":\"delete_file\",\"arguments\":{\"device_path\":\"/sdcard/test_scrcpy_dir\"}}}" > test_result.tmp
type test_result.tmp
findstr "success\": true" test_result.tmp >nul
if !errorlevel! equ 0 (
    echo [通过] delete_file (目录) 测试成功
    set /a PASS+=1
) else (
    echo [失败] delete_file (目录) 测试失败
    set /a FAIL+=1
)
echo.

REM 清理临时文件
del /q test_result.tmp 2>nul
del /q test_upload.txt 2>nul
del /q test_download.txt 2>nul

echo ================================================================
echo   测试结果: 通过 %PASS% 项, 失败 %FAIL% 项
echo   测试模式: %MODE%
echo ================================================================

if %FAIL% equ 0 (
    echo [SUCCESS] All file transfer tests passed!
) else (
    echo [FAILED] Some tests failed, please check.
)

echo.
echo ================================================================
echo   使用说明
echo ================================================================
echo.
echo ADB 模式测试:
echo   python scrcpy_http_mcp_server.py --connect --audio --audio-dup --preview
echo   然后运行此脚本
echo.
echo 网络模式测试:
echo   1. 先用 USB 连接设备
echo   2. python scrcpy_http_mcp_server.py --push-server --stay-alive
echo   3. 断开 USB, 使用网络模式连接
echo   4. python scrcpy_http_mcp_server.py --connect --audio --audio-dup --preview --connection-mode network --host ^<设备IP^>
echo   5. 运行此脚本
echo.

endlocal
pause
