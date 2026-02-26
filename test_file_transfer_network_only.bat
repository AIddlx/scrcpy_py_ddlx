@echo off
REM 网络模式文件传输专用测试脚本 - 仅使用第4条TCP文件通道
REM
REM 前置条件:
REM   python scrcpy_http_mcp_server.py --network-push 192.168.5.4 --audio --audio-dup
REM
REM 网络模式架构:
REM   - TCP 控制通道 (27184)
REM   - UDP 视频流 (27185)
REM   - UDP 音频流 (27186)
REM   - TCP 文件通道 (27187) <-- 本测试验证此通道

setlocal enabledelayedexpansion
set MCP_URL=http://127.0.0.1:3359/mcp

echo ================================================================
echo   网络模式文件传输测试 (TCP File Channel: 27187)
echo ================================================================
echo.
echo 架构: TCP控制 + UDP媒体 + TCP文件通道
echo 文件操作通过第4条TCP连接直接与设备通信
echo.

chcp 65001 >nul 2>&1

set PASS=0
set FAIL=0

echo ========== 1. list_dir ==========
curl -s -X POST %MCP_URL% -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"list_dir\",\"arguments\":{\"path\":\"/sdcard\"}}}" > test_result.tmp
type test_result.tmp
echo.
findstr /C:"success" test_result.tmp | findstr /C:"true" >nul
if !errorlevel! equ 0 (
    findstr /C:"mode" test_result.tmp | findstr /C:"network" >nul
    if !errorlevel! equ 0 (
        echo [PASS] list_dir - 网络模式
        set /a PASS+=1
    ) else (
        echo [FAIL] list_dir - 非网络模式!
        set /a FAIL+=1
    )
) else (
    echo [FAIL] list_dir - 请求失败
    set /a FAIL+=1
)
echo.

echo ========== 2. file_stat ==========
curl -s -X POST %MCP_URL% -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"file_stat\",\"arguments\":{\"device_path\":\"/sdcard/test_adb.png\"}}}" > test_result.tmp
type test_result.tmp
echo.
findstr /C:"success" test_result.tmp | findstr /C:"true" >nul
if !errorlevel! equ 0 (
    findstr /C:"mode" test_result.tmp | findstr /C:"network" >nul
    if !errorlevel! equ 0 (
        echo [PASS] file_stat - 网络模式
        set /a PASS+=1
    ) else (
        echo [FAIL] file_stat - 非网络模式!
        set /a FAIL+=1
    )
) else (
    echo [FAIL] file_stat - 请求失败
    set /a FAIL+=1
)
echo.

echo ========== 3. make_dir ==========
curl -s -X POST %MCP_URL% -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"make_dir\",\"arguments\":{\"device_path\":\"/sdcard/test_net_dir\"}}}" > test_result.tmp
type test_result.tmp
echo.
findstr /C:"success" test_result.tmp | findstr /C:"true" >nul
if !errorlevel! equ 0 (
    findstr /C:"mode" test_result.tmp | findstr /C:"network" >nul
    if !errorlevel! equ 0 (
        echo [PASS] make_dir - 网络模式
        set /a PASS+=1
    ) else (
        echo [FAIL] make_dir - 非网络模式!
        set /a FAIL+=1
    )
) else (
    echo [FAIL] make_dir - 请求失败
    set /a FAIL+=1
)
echo.

echo ========== 4. push_file ==========
echo This is NETWORK mode test file. > test_upload_net.txt
curl -s -X POST %MCP_URL% -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"push_file\",\"arguments\":{\"local_path\":\"test_upload_net.txt\",\"device_path\":\"/sdcard/test_net_dir/test.txt\"}}}" > test_result.tmp
type test_result.tmp
echo.
findstr /C:"success" test_result.tmp | findstr /C:"true" >nul
if !errorlevel! equ 0 (
    findstr /C:"mode" test_result.tmp | findstr /C:"network" >nul
    if !errorlevel! equ 0 (
        echo [PASS] push_file - 网络模式
        set /a PASS+=1
    ) else (
        echo [FAIL] push_file - 非网络模式!
        set /a FAIL+=1
    )
) else (
    echo [FAIL] push_file - 请求失败
    set /a FAIL+=1
)
echo.

echo ========== 5. pull_file ==========
curl -s -X POST %MCP_URL% -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":5,\"method\":\"tools/call\",\"params\":{\"name\":\"pull_file\",\"arguments\":{\"device_path\":\"/sdcard/test_net_dir/test.txt\",\"local_path\":\"test_download_net.txt\"}}}" > test_result.tmp
type test_result.tmp
echo.
findstr /C:"success" test_result.tmp | findstr /C:"true" >nul
if !errorlevel! equ 0 (
    findstr /C:"mode" test_result.tmp | findstr /C:"network" >nul
    if !errorlevel! equ 0 (
        echo [PASS] pull_file - 网络模式
        echo 内容:
        type test_download_net.txt
        set /a PASS+=1
    ) else (
        echo [FAIL] pull_file - 非网络模式!
        set /a FAIL+=1
    )
) else (
    echo [FAIL] pull_file - 请求失败
    set /a FAIL+=1
)
echo.

echo ========== 6. delete_file (文件) ==========
curl -s -X POST %MCP_URL% -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":6,\"method\":\"tools/call\",\"params\":{\"name\":\"delete_file\",\"arguments\":{\"device_path\":\"/sdcard/test_net_dir/test.txt\"}}}" > test_result.tmp
type test_result.tmp
echo.
findstr /C:"success" test_result.tmp | findstr /C:"true" >nul
if !errorlevel! equ 0 (
    findstr /C:"mode" test_result.tmp | findstr /C:"network" >nul
    if !errorlevel! equ 0 (
        echo [PASS] delete_file - 网络模式
        set /a PASS+=1
    ) else (
        echo [FAIL] delete_file - 非网络模式!
        set /a FAIL+=1
    )
) else (
    echo [FAIL] delete_file - 请求失败
    set /a FAIL+=1
)
echo.

echo ========== 7. delete_file (目录) ==========
curl -s -X POST %MCP_URL% -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":7,\"method\":\"tools/call\",\"params\":{\"name\":\"delete_file\",\"arguments\":{\"device_path\":\"/sdcard/test_net_dir\"}}}" > test_result.tmp
type test_result.tmp
echo.
findstr /C:"success" test_result.tmp | findstr /C:"true" >nul
if !errorlevel! equ 0 (
    findstr /C:"mode" test_result.tmp | findstr /C:"network" >nul
    if !errorlevel! equ 0 (
        echo [PASS] delete_dir - 网络模式
        set /a PASS+=1
    ) else (
        echo [FAIL] delete_dir - 非网络模式!
        set /a FAIL+=1
    )
) else (
    echo [FAIL] delete_dir - 请求失败
    set /a FAIL+=1
)
echo.

del /q test_result.tmp test_upload_net.txt test_download_net.txt 2>nul

echo ================================================================
echo   结果: %PASS%/7 通过 (必须全部显示 "网络模式")
echo ================================================================
if %FAIL% equ 0 (
    if %PASS% equ 7 (
        echo SUCCESS - 网络模式文件通道工作正常!
    )
) else (
    echo FAILED - 部分测试失败或使用了非网络模式
)

endlocal
