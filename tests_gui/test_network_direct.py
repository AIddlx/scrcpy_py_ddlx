"""
Network mode test script - Pure TCP/UDP (NO ADB tunnel for data)

This script uses pure network mode:
- TCP for control channel
- UDP for video/audio streams
- Server runs independently (nohup) - survives USB disconnection

Usage:
    cd C:\\Project\\IDEA\\2\\new\\scrcpy-py-ddlx
    python -X utf8 tests_gui/test_network_direct.py [options]

Examples:
    # Auto-detect device IP (recommended)
    python -X utf8 tests_gui/test_network_direct.py

    # Custom device IP
    python -X utf8 tests_gui/test_network_direct.py --ip 192.168.1.100

    # Fast reconnect mode (reuse server, no push)
    python -X utf8 tests_gui/test_network_direct.py --reuse --no-push

    # Enable frame-level FEC (K frames per group)
    python -X utf8 tests_gui/test_network_direct.py --fec frame

    # Enable fragment-level FEC (K fragments per group)
    python -X utf8 tests_gui/test_network_direct.py --fec fragment

    # Full custom config with FEC
    python -X utf8 tests_gui/test_network_direct.py --ip 192.168.5.4 --fec frame --fec-k 8 --fec-m 2

NOTE: ADB is only used to START the server. After server is running, you can unplug USB
      and the connection will continue via WiFi (TCP control + UDP video).
"""

import sys
import logging
import subprocess
import time
import argparse
import re
from pathlib import Path
from datetime import datetime

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def get_device_ip_via_adb():
    """
    Try to auto-detect device IP address via ADB.

    Returns:
        str or None: Device IP address, or None if detection fails
    """
    try:
        # Method 1: Try to get WiFi IP from ip addr show
        result = subprocess.run(
            ['adb', 'shell', 'ip addr show wlan0'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # Look for inet address (e.g., "inet 192.168.1.100/24")
            match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', result.stdout)
            if match:
                return match.group(1)

        # Method 2: Try ifconfig
        result = subprocess.run(
            ['adb', 'shell', 'ifconfig wlan0'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # Look for inet addr (e.g., "inet addr:192.168.1.100")
            match = re.search(r'inet\s+addr:\s*(\d+\.\d+\.\d+\.\d+)', result.stdout)
            if match:
                return match.group(1)
            # Alternative format
            match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', result.stdout)
            if match:
                return match.group(1)

        # Method 3: Try getprop for network info
        result = subprocess.run(
            ['adb', 'shell', 'getprop dhcp.wlan0.ipaddress'],
            capture_output=True, text=True, timeout=5
        )
        ip = result.stdout.strip()
        if ip and re.match(r'\d+\.\d+\.\d+\.\d+', ip):
            return ip

    except Exception as e:
        print(f"[WARN] Auto-detect IP failed: {e}")

    return None


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='scrcpy-py-ddlx Network Mode Test',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Server Lifecycle Modes:
  --reuse --no-push    Fast reconnect (reuse server, no APK push)
  --no-reuse --push    Full restart (kill old server, push APK, start fresh) [default]
  --reuse --push       First deploy (push new APK to existing server)
  --no-reuse --no-push Hot connect (server must be running, just connect)
        """
    )

    # Network settings
    net_group = parser.add_argument_group('Network Settings')
    net_group.add_argument('--ip', dest='device_ip', default=None,
                           help='Device IP address (default: auto-detect via ADB)')
    net_group.add_argument('--control-port', type=int, default=27184,
                           help='TCP control port (default: 27184)')
    net_group.add_argument('--video-port', type=int, default=27185,
                           help='UDP video port (default: 27185)')
    net_group.add_argument('--audio-port', type=int, default=27186,
                           help='UDP audio port (default: 27186)')

    # FEC settings
    fec_group = parser.add_argument_group('FEC Settings')
    fec_group.add_argument('--fec', dest='fec_mode', choices=['frame', 'fragment'],
                           default=None,
                           help='Enable FEC with mode: "frame" (K frames per group) or "fragment" (K fragments per group)')
    fec_group.add_argument('--video-fec', dest='video_fec_mode', choices=['frame', 'fragment'],
                           default=None,
                           help='Enable FEC for video only with specified mode')
    fec_group.add_argument('--audio-fec', dest='audio_fec_mode', choices=['frame', 'fragment'],
                           default=None,
                           help='Enable FEC for audio only with specified mode')
    fec_group.add_argument('--fec-k', dest='fec_group_size', type=int, default=4,
                           help='FEC data packets per group (default: 4)')
    fec_group.add_argument('--fec-m', dest='fec_parity_count', type=int, default=1,
                           help='FEC parity packets per group (default: 1)')

    # Content detection settings (EXPERIMENTAL - disabled by default)
    content_group = parser.add_argument_group('Content Detection (EXPERIMENTAL)')
    content_group.add_argument('--content-check', dest='content_check_enabled',
                               action='store_true', default=False,
                               help='Enable visual corruption detection (EXPERIMENTAL, default: OFF)')
    content_group.add_argument('--content-interval', dest='content_check_interval', type=int, default=5,
                               help='Check every N frames (default: 5)')
    content_group.add_argument('--content-extreme', dest='content_extreme_threshold', type=float, default=0.15,
                               help='Extreme value ratio threshold 0.0-1.0 (default: 0.15 = 15%%)')
    content_group.add_argument('--content-shift', dest='content_shift_threshold', type=int, default=30,
                               help='UV mean shift threshold (default: 30)')
    content_group.add_argument('--content-variance', dest='content_variance_min', type=int, default=50,
                               help='Minimum UV variance (default: 50)')

    # Server lifecycle
    srv_group = parser.add_argument_group('Server Lifecycle')
    srv_group.add_argument('--reuse', dest='reuse_server', action='store_true',
                           help='Reuse existing server (default: restart)')
    srv_group.add_argument('--no-reuse', dest='reuse_server', action='store_false',
                           help='Kill and restart server [default]')
    srv_group.add_argument('--push', dest='push_server', action='store_true',
                           help='Push server APK [default]')
    srv_group.add_argument('--no-push', dest='push_server', action='store_false',
                           help='Skip APK push (server already on device)')
    srv_group.add_argument('--wake', dest='wake_server', action='store_true', default=True,
                           help='Use UDP wake to connect (default: True)')
    srv_group.add_argument('--no-wake', dest='wake_server', action='store_false',
                           help='Disable UDP wake')
    srv_group.add_argument('--stay-alive', dest='stay_alive', action='store_true',
                           help='Start server in stay-alive mode (hot-connection)')
    srv_group.add_argument('--max-connections', dest='max_connections', type=int, default=-1,
                           help='Max connections in stay-alive mode (-1 = unlimited)')

    # Video settings
    video_group = parser.add_argument_group('Video Settings')
    video_group.add_argument('--codec', dest='video_codec', default='auto',
                           choices=['auto', 'h264', 'h265', 'av1'],
                           help='Video codec: auto (default, select best available), h264, h265, av1')
    video_group.add_argument('--list-encoders', dest='list_encoders', action='store_true',
                           help='List device encoders and exit')
    video_group.add_argument('--bitrate', dest='video_bitrate', type=int, default=2500000,
                           help='Video bitrate in bps (default: 2.5 Mbps)')
    video_group.add_argument('--max-fps', dest='max_fps', type=int, default=60,
                           help='Max frame rate (default: 60)')
    video_group.add_argument('--cbr', dest='bitrate_mode', action='store_const', const='cbr',
                           help='Use CBR (constant bitrate) mode for strict bandwidth control')
    video_group.add_argument('--vbr', dest='bitrate_mode', action='store_const', const='vbr',
                           help='Use VBR (variable bitrate) mode for variable quality (default)')
    video_group.add_argument('--i-frame-interval', dest='i_frame_interval', type=float, default=10.0,
                           help='I-frame (keyframe) interval in seconds (default: 10). '
                                'Supports decimals, e.g., 0.2 for 200ms. '
                                'Lower values = faster quality recovery but more bandwidth')

    # Low latency optimization settings
    latency_group = parser.add_argument_group('Low Latency Optimization')
    latency_group.add_argument('--low-latency', dest='low_latency', action='store_true',
                               help='Enable MediaCodec low latency mode (Android 11+). '
                                    'May not work on all devices.')
    latency_group.add_argument('--no-low-latency', dest='low_latency', action='store_false',
                               help='Disable low latency mode (default)')
    latency_group.add_argument('--encoder-priority', dest='encoder_priority', type=int, default=1,
                               choices=[0, 1, 2],
                               help='Encoder thread priority: 0=normal, 1=urgent (default), 2=realtime')
    latency_group.add_argument('--encoder-buffer', dest='encoder_buffer', type=int, default=0,
                               choices=[0, 1],
                               help='Encoder buffer: 0=auto (default), 1=disable B-frames')
    latency_group.add_argument('--skip-frames', dest='skip_frames', action='store_true',
                               help='Skip buffered frames to reduce latency (default: enabled)')
    latency_group.add_argument('--no-skip-frames', dest='skip_frames', action='store_false',
                               help='Disable frame skipping (send all frames)')

    # Multi-process mode for GIL avoidance
    latency_group.add_argument('--multiprocess', dest='multiprocess', action='store_true',
                               help='Use multi-process decoder to avoid GIL contention. '
                                    'Runs decoder in separate process, can reduce latency from ~330ms to ~150ms.')
    latency_group.add_argument('--no-multiprocess', dest='multiprocess', action='store_false',
                               help='Use single-process decoder (default)')

    # Debug settings
    dbg_group = parser.add_argument_group('Debug Settings')
    dbg_group.add_argument('-v', '--verbose', action='store_true',
                           help='Enable verbose output')
    dbg_group.add_argument('-q', '--quiet', action='store_true',
                           help='Quiet mode (warnings only)')
    dbg_group.add_argument('--show-details', dest='show_details', action='store_true',
                           help='Show detailed encoder info after connection')
    dbg_group.add_argument('--drop-rate', dest='drop_rate', type=float, default=0.0,
                           help='Simulate packet loss (0.0-1.0, e.g., 0.05 for 5%% drop rate)')
    dbg_group.add_argument('--queue-size', dest='packet_queue_size', type=int, default=3,
                           help='Packet queue size (1=lowest latency, 3=most stable, default: 3)')

    # Audio settings
    audio_group = parser.add_argument_group('Audio Settings')
    audio_group.add_argument('--audio', dest='audio_enabled', action='store_true',
                           help='Enable audio (default: disabled)')
    audio_group.add_argument('--no-audio', dest='audio_enabled', action='store_false',
                           help='Disable audio')

    parser.set_defaults(
        reuse_server=False,
        push_server=True,
        wake_server=True,
        stay_alive=False,
        max_connections=-1,
        audio_enabled=False,
        bitrate_mode='vbr',
        show_details=False,
        low_latency=False,
        encoder_priority=1,
        encoder_buffer=0,
        skip_frames=True,
        multiprocess=False
    )

    return parser.parse_args()


def query_device_encoders():
    """
    Query device for supported video encoders by querying MediaCodec info.

    Returns:
        dict: {'h264': bool, 'h265': bool, 'av1': bool}
    """
    encoders = {'h264': True, 'h265': False, 'av1': False}

    try:
        # Query MediaCodec encoder capabilities directly
        # This is more reliable than platform guessing
        # Note: H264 encoders may be named with .avc (Advanced Video Coding)
        #       H265 encoders may be named with .hevc
        result = subprocess.run(
            ['cmd', '/c', 'adb', 'shell',
             'dumpsys media.codec | grep -A 100 "Video encoders:" | grep -E "OMX\\..*\\.(h264|avc|h265|hevc|av1)"'],
            capture_output=True, text=True, timeout=10,
            errors='ignore'  # Ignore non-UTF8 characters
        )
        output = result.stdout.lower()

        # Check for H264 (always available on Android 5+)
        # May be named as h264 or avc (Advanced Video Coding)
        if 'h264' in output or 'avc' in output:
            encoders['h264'] = True

        # Check for H265/HEVC
        if 'h265' in output or 'hevc' in output:
            encoders['h265'] = True

        # Check for AV1
        if 'av1' in output:
            encoders['av1'] = True

        # If no encoders found via dumpsys, fall back to platform heuristics
        if not any([encoders['h265'], encoders['av1']]):
            result = subprocess.run(
                ['cmd', '/c', 'adb', 'shell', 'getprop ro.board.platform'],
                capture_output=True, text=True, timeout=5
            )
            platform = result.stdout.strip().lower()

            # Qualcomm platforms
            if 'qcom' in platform or 'sm' in platform or 'msm' in platform:
                encoders['h265'] = True

            # Qualcomm codenames (pineapple = Snapdragon 8 Gen 3)
            if platform in ['pineapple', 'lanai']:
                encoders['h265'] = True

            # MediaTek platforms (mt, apollo, etc.)
            if 'mt' in platform or platform in ['apollo', 'cebus', 'k6833']:
                encoders['h265'] = True

            # Samsung Exynos
            if 'exynos' in platform or 'universal' in platform:
                encoders['h265'] = True

            # HiSilicon
            if 'kirin' in platform or 'hi' in platform:
                encoders['h265'] = True

        return encoders
    except Exception:
        return encoders


def select_best_codec(encoders, preferred='auto'):
    """
    Select the best available codec.

    Priority: av1 > h265 > h264

    Args:
        encoders: dict of available encoders
        preferred: 'auto' or specific codec

    Returns:
        str: selected codec name
    """
    if preferred != 'auto':
        # User specified a codec
        if encoders.get(preferred, False):
            return preferred
        else:
            print(f"[WARN] Requested codec '{preferred}' not available, falling back to auto")
            # Fall through to auto selection

    # Auto selection: priority av1 > h265 > h264
    if encoders.get('av1', False):
        return 'av1'
    elif encoders.get('h265', False):
        return 'h265'
    else:
        return 'h264'


def print_detailed_encoder_info(capabilities, selected_video_codec=None, selected_audio_codec=None):
    """
    Print detailed encoder information from device capabilities.

    Args:
        capabilities: DeviceCapabilities object
        selected_video_codec: Currently selected video codec ID
        selected_audio_codec: Currently selected audio codec ID
    """
    from scrcpy_py_ddlx.core.negotiation import VideoCodecId, AudioCodecId, EncoderFlags

    print("\n" + "=" * 70)
    print("DEVICE CAPABILITIES")
    print("=" * 70)

    # Screen info
    print(f"\n📱 Screen: {capabilities.screen_width} x {capabilities.screen_height}")

    # Video encoders
    print(f"\n🎬 Video Encoders ({len(capabilities.video_encoders)}):")
    print("-" * 70)
    print(f"{'#':<3} {'Codec':<8} {'Type':<10} {'Priority':<10} {'Selected':<10}")
    print("-" * 70)

    for i, enc in enumerate(capabilities.video_encoders):
        codec_name = VideoCodecId.to_string(enc.codec_id)
        enc_type = "HW" if enc.is_hardware() else "SW" if enc.is_software() else "Unknown"
        is_selected = "✓ YES" if enc.codec_id == selected_video_codec else ""
        print(f"{i:<3} {codec_name.upper():<8} {enc_type:<10} {enc.priority:<10} {is_selected:<10}")

    # Count HW/SW
    hw_count = sum(1 for e in capabilities.video_encoders if e.is_hardware())
    sw_count = sum(1 for e in capabilities.video_encoders if e.is_software())
    print(f"\n  Hardware encoders: {hw_count}, Software encoders: {sw_count}")

    # Audio encoders
    print(f"\n🔊 Audio Encoders ({len(capabilities.audio_encoders)}):")
    print("-" * 70)
    print(f"{'#':<3} {'Codec':<8} {'Type':<10} {'Priority':<10} {'Selected':<10}")
    print("-" * 70)

    for i, enc in enumerate(capabilities.audio_encoders):
        codec_name = AudioCodecId.to_string(enc.codec_id)
        enc_type = "HW" if enc.is_hardware() else "SW" if enc.is_software() else "Unknown"
        is_selected = "✓ YES" if enc.codec_id == selected_audio_codec else ""
        print(f"{i:<3} {codec_name.upper():<8} {enc_type:<10} {enc.priority:<10} {is_selected:<10}")

    # Count HW/SW
    hw_count = sum(1 for e in capabilities.audio_encoders if e.is_hardware())
    sw_count = sum(1 for e in capabilities.audio_encoders if e.is_software())
    print(f"\n  Hardware encoders: {hw_count}, Software encoders: {sw_count}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    if selected_video_codec:
        print(f"  Selected Video: {VideoCodecId.to_string(selected_video_codec).upper()}")
    if selected_audio_codec:
        print(f"  Selected Audio: {AudioCodecId.to_string(selected_audio_codec).upper()}")
    print("=" * 70 + "\n")


def check_server_running():
    """Check if server is already running on device."""
    try:
        result = subprocess.run(
            ["adb", "shell", "ps -A | grep app_process"],
            capture_output=True, text=True, timeout=5
        )
        return "app_process" in result.stdout
    except Exception:
        return False


def start_server(args):
    """
    Start server on device.

    Modes:
    - args.reuse_server=False: Always kill old server and start fresh (default)
    - args.reuse_server=True: Reuse existing server if running

    When args.reuse_server=True:
    - If server is already running, skip push/start and let client use UDP wake
    - If server is not running, push and start as normal
    """
    server_running = check_server_running()

    if args.reuse_server and server_running:
        print("[INFO] REUSE_SERVER=True: Found running server, will reuse it")
        print("[INFO] Client will use UDP wake packet to connect")
        return True

    # Kill old server if running (needed for clean restart or port conflicts)
    if server_running:
        print("[INFO] Found running server - killing it...")
        subprocess.run(["adb", "shell", "pkill -9 -f app_process"],
                      capture_output=True, timeout=5)
        time.sleep(1)

    # Push server APK if enabled
    if args.push_server:
        server_apk = project_root / "scrcpy-server"
        if server_apk.exists():
            print(f"[INFO] Pushing server...")
            result = subprocess.run(
                ["adb", "push", str(server_apk), "/data/local/tmp/scrcpy-server.apk"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                print(f"[FAIL] Push failed: {result.stderr}")
                return False
        else:
            print(f"[WARN] Server APK not found: {server_apk}")
    else:
        print("[INFO] PUSH_SERVER=False: Skipping server push")

    # Start server
    print("[INFO] Starting server...")

    # Determine FEC enabled status from new mode parameters
    fec_enabled = args.fec_mode is not None
    video_fec_enabled = args.video_fec_mode is not None
    audio_fec_enabled = args.audio_fec_mode is not None

    # Use the first specified mode as the default, or 'frame' as fallback
    effective_fec_mode = args.fec_mode or args.video_fec_mode or args.audio_fec_mode or 'frame'

    # Build FEC parameters
    fec_params = ""
    if fec_enabled:
        # Enable FEC for both
        fec_params = f"fec_enabled=true fec_group_size={args.fec_group_size} fec_parity_count={args.fec_parity_count} fec_mode={effective_fec_mode}"
        print(f"[INFO] FEC enabled for both: K={args.fec_group_size}, M={args.fec_parity_count}, mode={effective_fec_mode}")
    else:
        # Independent video/audio FEC control
        if video_fec_enabled:
            fec_params += f" video_fec_enabled=true"
            print(f"[INFO] Video FEC enabled: K={args.fec_group_size}, M={args.fec_parity_count}, mode={args.video_fec_mode}")
        if audio_fec_enabled:
            fec_params += f" audio_fec_enabled=true"
            print(f"[INFO] Audio FEC enabled: K={args.fec_group_size}, M={args.fec_parity_count}, mode={args.audio_fec_mode}")
        if video_fec_enabled or audio_fec_enabled:
            fec_params += f" fec_group_size={args.fec_group_size} fec_parity_count={args.fec_parity_count} fec_mode={effective_fec_mode}"

    # Build server command with nohup to survive ADB disconnection
    # Use 'sh -c' to properly handle environment variable and output redirection
    # This is critical for network mode - server must continue running after USB is unplugged
    audio_flag = "true" if args.audio_enabled else "false"
    stay_alive_flag = "true" if args.stay_alive else "false"
    low_latency_flag = "true" if args.low_latency else "false"
    skip_frames_flag = "true" if args.skip_frames else "false"

    server_cmd = (
        "CLASSPATH=/data/local/tmp/scrcpy-server.apk app_process / "
        f"com.genymobile.scrcpy.Server 3.3.4 log_level=debug "
        f"control_port={args.control_port} video_port={args.video_port} audio_port={args.audio_port} "
        f"video_codec={args.video_codec} video_bit_rate={args.video_bitrate} max_fps={args.max_fps} "
        f"bitrate_mode={args.bitrate_mode} i_frame_interval={args.i_frame_interval} "
        f"low_latency={low_latency_flag} encoder_priority={args.encoder_priority} encoder_buffer={args.encoder_buffer} "
        f"skip_frames={skip_frames_flag} "
        f"{fec_params} "
        f"stay_alive={stay_alive_flag} "
    )

    if args.stay_alive and args.max_connections > 0:
        server_cmd += f"max_connections={args.max_connections} "

    server_cmd += (
        f"video=true audio={audio_flag} control=true send_device_meta=true send_dummy_byte=true cleanup=false"
    )

    # Wrap with nohup and sh -c for proper background execution
    cmd = f"nohup sh -c '{server_cmd}' > /data/local/tmp/scrcpy_server.log 2>&1 &"

    # Start server with nohup so it survives ADB disconnection
    result = subprocess.run(
        ["adb", "shell", cmd],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        print(f"[WARN] Server start command returned: {result.stderr}")

    # Wait for server to start
    for i in range(10):
        time.sleep(0.5)
        if check_server_running():
            print("[INFO] Server started successfully")
            return True

    print("[FAIL] Server failed to start")
    return False


def setup_logging(args):
    """Configure logging based on arguments."""
    if args.quiet:
        level = logging.WARNING
    elif args.verbose:
        level = logging.DEBUG
    else:
        level = logging.DEBUG  # Default to debug for file, will adjust console

    # Put logs in test_logs directory
    log_dir = project_root / "test_logs"
    log_dir.mkdir(exist_ok=True)
    log_filename = str(log_dir / f"scrcpy_network_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

    handlers = [
        logging.FileHandler(log_filename, encoding='utf-8'),
    ]

    # Console handler with appropriate level
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    handlers.append(console_handler)

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )

    return log_filename


def main():
    """Test network mode connection"""
    args = parse_args()

    # Auto-detect device IP if not specified
    if args.device_ip is None:
        print("[INFO] Auto-detecting device IP via ADB...")
        args.device_ip = get_device_ip_via_adb()
        if args.device_ip:
            print(f"[INFO] Detected device IP: {args.device_ip}")
        else:
            print("[ERROR] Could not auto-detect device IP!")
            print("[ERROR] Please specify IP with --ip option, e.g.:")
            print("[ERROR]   python tests_gui/test_network_direct.py --ip 192.168.1.100")
            return

    # Handle --list-encoders
    if args.list_encoders:
        print("[INFO] Querying device encoders...")
        encoders = query_device_encoders()
        print("\n[INFO] Device video encoder support:")
        for codec, available in encoders.items():
            status = "✓" if available else "✗"
            print(f"  {status} {codec.upper()}")
        return

    # Query device encoders and select best codec
    if args.video_codec == 'auto':
        print("[INFO] Querying device encoders for best codec...")
        encoders = query_device_encoders()
        selected_codec = select_best_codec(encoders, 'auto')
        print(f"[INFO] Available encoders: h264={encoders['h264']}, h265={encoders['h265']}, av1={encoders['av1']}")
        print(f"[INFO] Selected codec: {selected_codec.upper()}")
        args.video_codec = selected_codec
    else:
        # User specified a codec, verify it's available
        encoders = query_device_encoders()
        if not encoders.get(args.video_codec, False):
            print(f"[WARN] Requested codec {args.video_codec} not available, selecting best alternative...")
            selected_codec = select_best_codec(encoders, 'auto')
            print(f"[INFO] Using: {selected_codec.upper()}")
            args.video_codec = selected_codec

    # Setup logging
    log_filename = setup_logging(args)
    logger = logging.getLogger(__name__)
    print(f"[INFO] Log file: {log_filename}")

    print("=" * 60)
    print("scrcpy-py-ddlx Network Mode Test")
    print("PURE NETWORK MODE (TCP control + UDP video)")
    print("=" * 60)
    print(f"[INFO] Device IP: {args.device_ip}")
    print(f"[INFO] Control port (TCP): {args.control_port}")
    print(f"[INFO] Video port (UDP): {args.video_port}")
    print(f"[INFO] Audio port (UDP): {args.audio_port}")

    # Calculate FEC status from mode parameters
    fec_enabled = args.fec_mode is not None
    video_fec_enabled = args.video_fec_mode is not None
    audio_fec_enabled = args.audio_fec_mode is not None
    effective_fec_mode = args.fec_mode or args.video_fec_mode or args.audio_fec_mode or 'frame'

    # Show FEC configuration
    if fec_enabled:
        print(f"[INFO] FEC: enabled (both video and audio), mode={effective_fec_mode}")
    else:
        video_fec_str = f"enabled (mode={args.video_fec_mode})" if video_fec_enabled else "disabled"
        audio_fec_str = f"enabled (mode={args.audio_fec_mode})" if audio_fec_enabled else "disabled"
        print(f"[INFO] Video FEC: {video_fec_str}, Audio FEC: {audio_fec_str}")

    # Show video settings
    print(f"[INFO] Video codec: {args.video_codec.upper()}, Bitrate: {args.video_bitrate // 1000} Kbps, Max FPS: {args.max_fps}")
    print(f"[INFO] Audio: {'enabled' if args.audio_enabled else 'disabled'}")

    # Show low latency settings
    if args.low_latency or args.encoder_priority != 1 or args.encoder_buffer != 0 or not args.skip_frames:
        print(f"[INFO] Low Latency: low_latency={args.low_latency}, priority={args.encoder_priority}, "
              f"buffer={args.encoder_buffer}, skip_frames={args.skip_frames}")

    print()
    print("[NOTE] ADB is only used to START the server.")
    print("[NOTE] After connection, you can UNPLUG USB - connection continues via WiFi!")
    print()

    # Check dependencies
    try:
        import numpy as np
        print(f"[PASS] numpy: {np.__version__}")
    except ImportError:
        print("[FAIL] numpy not installed")
        return

    try:
        from PySide6.QtWidgets import QApplication
        print("[PASS] PySide6 installed")
    except ImportError:
        print("[FAIL] PySide6 not installed")
        return

    try:
        import av
        print(f"[PASS] PyAV: {av.__version__}")
    except ImportError:
        print("[FAIL] PyAV not installed")
        return

    # Import source modules
    try:
        from scrcpy_py_ddlx.client import ScrcpyClient, ClientConfig
        from scrcpy_py_ddlx.client.config import ConnectionMode
        print("[PASS] Source modules imported")
    except ImportError as e:
        print(f"[FAIL] Import failed: {e}")
        return

    # Check/start server
    print()
    print(f"[INFO] Server lifecycle: REUSE_SERVER={args.reuse_server}, PUSH_SERVER={args.push_server}, WAKE_SERVER={args.wake_server}")

    server_running = check_server_running()
    if server_running:
        if args.reuse_server:
            print("[INFO] Found running server (will reuse)")
        else:
            print("[INFO] Found running server (will restart)")

    if not start_server(args):
        return

    # Create client config for network mode
    print("\nCreating network mode client...")
    config = ClientConfig(
        # Network mode settings
        connection_mode=ConnectionMode.NETWORK,  # Use network mode
        host=args.device_ip,
        control_port=args.control_port,
        video_port=args.video_port,
        audio_port=args.audio_port,

        # FEC settings (independent video/audio control)
        fec_enabled=fec_enabled,
        video_fec_enabled=video_fec_enabled,
        audio_fec_enabled=audio_fec_enabled,
        fec_group_size=args.fec_group_size,
        fec_parity_count=args.fec_parity_count,
        fec_mode=effective_fec_mode,

        # Video settings
        video=True,
        codec=args.video_codec,
        bitrate=args.video_bitrate,
        max_fps=args.max_fps,
        bitrate_mode=args.bitrate_mode,
        i_frame_interval=args.i_frame_interval,

        # Low latency optimization
        low_latency=args.low_latency,
        encoder_priority=args.encoder_priority,
        encoder_buffer=args.encoder_buffer,
        skip_frames=args.skip_frames,

        # Multi-process decoder (GIL avoidance)
        multiprocess=args.multiprocess,

        # Simulated packet loss for testing
        drop_rate=args.drop_rate,

        # Packet queue size
        packet_queue_size=args.packet_queue_size,

        # Content detection (visual corruption detection)
        content_check_enabled=args.content_check_enabled,
        content_check_interval=args.content_check_interval,
        content_extreme_threshold=args.content_extreme_threshold,
        content_shift_threshold=args.content_shift_threshold,
        content_variance_min=args.content_variance_min,

        # Audio settings
        audio=args.audio_enabled,  # Enable audio if requested

        # Display settings
        show_window=True,
        control=True,
        clipboard_autosync=False,

        # Server jar (not used in network mode, but required)
        server_jar=str(project_root / "scrcpy-server"),
    )

    print(f"[INFO] Connection mode: {config.connection_mode}")
    print(f"[INFO] Host: {config.host}")
    print(f"[INFO] Control port: {config.control_port}")
    print(f"[INFO] Video port: {config.video_port}")
    print(f"[INFO] Packet queue size: {config.packet_queue_size}")
    if config.content_check_enabled:
        print(f"[INFO] Content detection: interval={config.content_check_interval}, "
              f"extreme={config.content_extreme_threshold:.0%}, shift={config.content_shift_threshold}, "
              f"variance={config.content_variance_min}")
    else:
        print("[INFO] Content detection: DISABLED")
    if config.drop_rate > 0:
        print(f"[INFO] Simulated packet loss: {config.drop_rate:.1%}")

    # Create client
    client = ScrcpyClient(config)

    print("\nConnecting to device...")

    try:
        client.connect()

        print(f"\n{'=' * 50}")
        print(f"[SUCCESS] Connected via NETWORK MODE!")
        print(f"  Device: {client.state.device_name}")
        print(f"  Resolution: {client.state.device_size[0]}x{client.state.device_size[1]}")
        print(f"{'=' * 50}")

        # Show detailed encoder info if requested
        if args.show_details and client.state.capabilities:
            print_detailed_encoder_info(
                client.state.capabilities,
                client.state.selected_video_codec,
                client.state.selected_audio_codec
            )

        print()
        print("[IMPORTANT] You can now UNPLUG USB cable!")
        print("[IMPORTANT] Connection continues via WiFi (TCP+UDP).")
        print()
        print("Video window displayed.")
        print("Close window or press Ctrl+C to disconnect...")

        # Run with Qt event loop
        client.run_with_qt()

    except KeyboardInterrupt:
        print("\n\nUser interrupted")
    except Exception as e:
        print(f"\n[ERROR] Connection failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\nCleaning up...")
        try:
            client.disconnect()
        except:
            pass

        # Save server log (from device) - useful for debugging
        try:
            server_log_filename = log_filename.replace('.log', '_server.log')
            print(f"[INFO] Saving server log to: {server_log_filename}")
            result = subprocess.run(
                ['adb', 'logcat', '-d', '-s', 'scrcpy:*'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                with open(server_log_filename, 'w', encoding='utf-8') as f:
                    f.write(f"# Server log captured at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"# Test log: {log_filename}\n\n")
                    f.write(result.stdout)
                print(f"[INFO] Server log saved ({len(result.stdout)} bytes)")
            else:
                print("[INFO] No server log available")
        except Exception as e:
            print(f"[WARN] Could not save server log: {e}")

        print("Test completed")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[FATAL] {e}")
        import traceback
        traceback.print_exc()
