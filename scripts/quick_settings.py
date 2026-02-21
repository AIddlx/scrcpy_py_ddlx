"""
Quick Settings - Server management tool for scrcpy-py-ddlx

Provides easy control over server lifecycle:
- Start/Stop/Restart server
- Check server status
- Wake/Sleep server

Usage:
    python scripts/quick_settings.py status
    python scripts/quick_settings.py start [--stay-alive]
    python scripts/quick_settings.py stop
    python scripts/quick_settings.py restart
    python scripts/quick_settings.py wake
"""

import sys
import subprocess
import time
import argparse
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Default settings
DEFAULT_DEVICE_IP = "192.168.5.4"
DEFAULT_DISCOVERY_PORT = 27183
DEFAULT_CONTROL_PORT = 27184
DEFAULT_VIDEO_PORT = 27185
DEFAULT_AUDIO_PORT = 27186
SERVER_APK = project_root / "scrcpy-server"


def run_adb(cmd: str, timeout: int = 10) -> tuple:
    """Run adb command and return (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["adb", "shell", cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Timeout"
    except Exception as e:
        return False, "", str(e)


def push_server():
    """Push server APK to device."""
    if not SERVER_APK.exists():
        print(f"[ERROR] Server APK not found: {SERVER_APK}")
        return False

    print(f"[INFO] Pushing server APK...")
    try:
        result = subprocess.run(
            ["adb", "push", str(SERVER_APK), "/data/local/tmp/scrcpy-server.apk"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            print("[OK] Server APK pushed successfully")
            return True
        else:
            print(f"[ERROR] Push failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"[ERROR] Push failed: {e}")
        return False


def check_server_running() -> dict:
    """
    Check if server is running and return status info.

    Returns:
        dict with keys: running, pid, stay_alive, listening_udp
    """
    result = {
        'running': False,
        'pid': None,
        'stay_alive': False,
        'listening_udp': False
    }

    # Check for app_process process
    success, stdout, _ = run_adb("ps -A | grep app_process")
    if success and "app_process" in stdout:
        result['running'] = True
        # Extract PID
        parts = stdout.split()
        if len(parts) >= 2:
            try:
                result['pid'] = int(parts[1])
            except ValueError:
                pass

    # Check if listening on UDP discovery port
    success, stdout, _ = run_adb(f"netstat -uln | grep {DEFAULT_DISCOVERY_PORT}")
    if success and str(DEFAULT_DISCOVERY_PORT) in stdout:
        result['listening_udp'] = True
        result['stay_alive'] = True

    return result


def get_server_log(lines: int = 20) -> str:
    """Get last N lines of server log."""
    success, stdout, _ = run_adb(f"tail -{lines} /data/local/tmp/scrcpy_server.log")
    return stdout if success else ""


def start_server(stay_alive: bool = True, push: bool = False,
                 max_connections: int = -1) -> bool:
    """
    Start server on device.

    Args:
        stay_alive: Enable hot-connection mode
        push: Push server APK first
        max_connections: Max connections in stay-alive mode (-1 = unlimited)

    Returns:
        True if started successfully
    """
    # Check if already running
    status = check_server_running()
    if status['running']:
        if status['stay_alive']:
            print(f"[INFO] Server already running in stay-alive mode (PID: {status['pid']})")
            print("[INFO] Use 'wake' command to connect, or 'restart' to restart")
            return True
        else:
            print(f"[INFO] Server already running (PID: {status['pid']})")
            print("[INFO] Use 'restart' to restart with new settings")
            return True

    # Push if requested
    if push:
        if not push_server():
            return False

    # Build server command
    stay_alive_str = "true" if stay_alive else "false"
    server_cmd = (
        "CLASSPATH=/data/local/tmp/scrcpy-server.apk app_process / "
        f"com.genymobile.scrcpy.Server 3.3.4 log_level=info "
        f"control_port={DEFAULT_CONTROL_PORT} video_port={DEFAULT_VIDEO_PORT} audio_port={DEFAULT_AUDIO_PORT} "
        f"stay_alive={stay_alive_str} "
    )

    if stay_alive and max_connections > 0:
        server_cmd += f"max_connections={max_connections} "

    server_cmd += "video=true audio=false control=true send_device_meta=true send_dummy_byte=true cleanup=false"

    # Start with nohup
    cmd = f"nohup sh -c '{server_cmd}' > /data/local/tmp/scrcpy_server.log 2>&1 &"

    print(f"[INFO] Starting server (stay_alive={stay_alive})...")
    success, _, stderr = run_adb(cmd)

    if not success:
        print(f"[ERROR] Failed to start server: {stderr}")
        return False

    # Wait and verify
    time.sleep(1)
    status = check_server_running()

    if status['running']:
        print(f"[OK] Server started successfully (PID: {status['pid']})")
        if status['stay_alive']:
            print(f"[INFO] Stay-alive mode enabled, listening on UDP port {DEFAULT_DISCOVERY_PORT}")
        return True
    else:
        print("[ERROR] Server failed to start")
        print("\nServer log:")
        print(get_server_log(10))
        return False


def stop_server() -> bool:
    """Stop server on device."""
    status = check_server_running()

    if not status['running']:
        print("[INFO] Server is not running")
        return True

    print(f"[INFO] Stopping server (PID: {status['pid']})...")
    success, _, _ = run_adb("pkill -9 -f app_process")

    if success:
        time.sleep(0.5)
        status = check_server_running()
        if not status['running']:
            print("[OK] Server stopped")
            return True
        else:
            print("[WARN] Server may still be running")
            return False
    else:
        print("[ERROR] Failed to stop server")
        return False


def restart_server(stay_alive: bool = True, push: bool = False) -> bool:
    """Restart server."""
    print("[INFO] Restarting server...")
    if not stop_server():
        return False
    time.sleep(1)
    return start_server(stay_alive=stay_alive, push=push)


def wake_server(device_ip: str = DEFAULT_DEVICE_IP,
                port: int = DEFAULT_DISCOVERY_PORT) -> bool:
    """
    Send wake packet to server.

    Args:
        device_ip: Device IP address
        port: UDP discovery port

    Returns:
        True if wake successful
    """
    import socket

    WAKE_REQUEST = b"WAKE_UP"
    WAKE_RESPONSE = b"WAKE_ACK"
    TIMEOUT = 3.0

    print(f"[INFO] Sending wake packet to {device_ip}:{port}...")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(TIMEOUT)

    try:
        sock.sendto(WAKE_REQUEST, (device_ip, port))
        print(f"[DEBUG] Sent WAKE_UP to {device_ip}:{port}")

        response, addr = sock.recvfrom(1024)

        if response == WAKE_RESPONSE:
            print(f"[OK] Server woke up successfully")
            return True
        else:
            print(f"[WARN] Unexpected response: {response}")
            return False

    except socket.timeout:
        print(f"[WARN] Wake timeout - server may not be in stay-alive mode")
        return False
    except Exception as e:
        print(f"[ERROR] Wake failed: {e}")
        return False
    finally:
        sock.close()


def print_status():
    """Print detailed server status."""
    print("\n" + "=" * 60)
    print("SCRCPY SERVER STATUS")
    print("=" * 60)

    status = check_server_running()

    if status['running']:
        print(f"\n  Status: RUNNING")
        print(f"  PID: {status['pid']}")

        if status['stay_alive']:
            print(f"  Mode: STAY-ALIVE (hot-connection)")
            print(f"  UDP Discovery: Listening on port {DEFAULT_DISCOVERY_PORT}")
        else:
            print(f"  Mode: STANDARD (single session)")

        print(f"\n  Ports:")
        print(f"    Control (TCP): {DEFAULT_CONTROL_PORT}")
        print(f"    Video (UDP): {DEFAULT_VIDEO_PORT}")
        print(f"    Audio (UDP): {DEFAULT_AUDIO_PORT}")

        # Check if ports are actually listening
        success, stdout, _ = run_adb(f"netstat -tln | grep {DEFAULT_CONTROL_PORT}")
        if success and str(DEFAULT_CONTROL_PORT) in stdout:
            print(f"    Control port: ACTIVE")
        else:
            print(f"    Control port: INACTIVE (waiting for connection)")

    else:
        print(f"\n  Status: NOT RUNNING")

    print("\n" + "=" * 60)

    # Show last few log lines
    if status['running']:
        print("\nRecent log:")
        print("-" * 40)
        log = get_server_log(10)
        for line in log.strip().split('\n'):
            if line:
                # Truncate long lines
                if len(line) > 80:
                    line = line[:77] + "..."
                print(f"  {line}")
        print("-" * 40)


def main():
    parser = argparse.ArgumentParser(
        description='Quick Settings - Server management for scrcpy-py-ddlx',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/quick_settings.py status
    python scripts/quick_settings.py start --stay-alive
    python scripts/quick_settings.py start --push
    python scripts/quick_settings.py stop
    python scripts/quick_settings.py restart
    python scripts/quick_settings.py wake --ip 192.168.5.4
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # status command
    subparsers.add_parser('status', help='Show server status')

    # start command
    start_parser = subparsers.add_parser('start', help='Start server')
    start_parser.add_argument('--stay-alive', action='store_true', default=True,
                              help='Start in stay-alive mode (default: True)')
    start_parser.add_argument('--no-stay-alive', action='store_false', dest='stay_alive',
                              help='Start in standard mode')
    start_parser.add_argument('--push', action='store_true',
                              help='Push server APK before starting')
    start_parser.add_argument('--max-connections', type=int, default=-1,
                              help='Max connections in stay-alive mode (-1 = unlimited)')

    # stop command
    subparsers.add_parser('stop', help='Stop server')

    # restart command
    restart_parser = subparsers.add_parser('restart', help='Restart server')
    restart_parser.add_argument('--stay-alive', action='store_true', default=True,
                                help='Restart in stay-alive mode (default: True)')
    restart_parser.add_argument('--push', action='store_true',
                                help='Push server APK before restarting')

    # wake command
    wake_parser = subparsers.add_parser('wake', help='Send wake packet to server')
    wake_parser.add_argument('--ip', default=DEFAULT_DEVICE_IP,
                             help=f'Device IP address (default: {DEFAULT_DEVICE_IP})')
    wake_parser.add_argument('--port', type=int, default=DEFAULT_DISCOVERY_PORT,
                             help=f'UDP discovery port (default: {DEFAULT_DISCOVERY_PORT})')

    # log command
    log_parser = subparsers.add_parser('log', help='Show server log')
    log_parser.add_argument('--lines', '-n', type=int, default=30,
                            help='Number of lines to show (default: 30)')
    log_parser.add_argument('--follow', '-f', action='store_true',
                            help='Follow log output (Ctrl+C to stop)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == 'status':
        print_status()

    elif args.command == 'start':
        start_server(
            stay_alive=args.stay_alive,
            push=args.push,
            max_connections=args.max_connections
        )

    elif args.command == 'stop':
        stop_server()

    elif args.command == 'restart':
        restart_server(
            stay_alive=args.stay_alive,
            push=args.push
        )

    elif args.command == 'wake':
        wake_server(device_ip=args.ip, port=args.port)

    elif args.command == 'log':
        if args.follow:
            print("[INFO] Following server log (Ctrl+C to stop)...")
            try:
                subprocess.run(
                    ["adb", "shell", f"tail -f /data/local/tmp/scrcpy_server.log"],
                    timeout=None
                )
            except KeyboardInterrupt:
                print("\n[INFO] Stopped following log")
        else:
            print(get_server_log(args.lines))


if __name__ == "__main__":
    main()
