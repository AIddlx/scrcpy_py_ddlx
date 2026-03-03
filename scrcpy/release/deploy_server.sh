#!/bin/bash
# Build and deploy scrcpy-server to device
# Usage: ./deploy_server.sh [OPTIONS]
#
# Options:
#   --push-only    Only push server, don't start
#   --no-auth      Disable authentication
#   --verbose      Enable verbose logging on server
#   --help         Show this help
#
# IMPORTANT: MSYS_NO_PATHCONV=1 prevents Git Bash from converting
# /data/local/tmp to C:/Program Files/Git/data/local/tmp

set -e  # Exit on error

# Parse arguments
PUSH_ONLY=false
AUTH_ENABLED=true
VERBOSE_LOG=false

for arg in "$@"; do
    case $arg in
        --push-only) PUSH_ONLY=true ;;
        --no-auth) AUTH_ENABLED=false ;;
        --verbose) VERBOSE_LOG=true ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --push-only    Only push server, don't start"
            echo "  --no-auth     Disable authentication"
            echo "  --verbose     Enable verbose logging on server"
            echo "  --help        Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg"
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# release/ -> scrcpy/ -> scrcpy-py-ddlx/
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

echo "=== Building server ==="
cd "$SCRIPT_DIR"
bash build_server.sh

echo ""
echo "=== Pushing to device ==="

# Navigate to project root and use relative path
cd "$PROJECT_ROOT"
export MSYS_NO_PATHCONV=1

# Push server
echo "Pushing scrcpy-server..."
adb push scrcpy-server /data/local/tmp/scrcpy-server.apk

# Handle authentication key
if [ "$AUTH_ENABLED" = true ]; then
    echo ""
    echo "=== Setting up authentication ==="

    # Use user's config directory (consistent with Python auth.py)
    AUTH_DIR="$HOME/.config/scrcpy-py-ddlx/auth_keys"
    AUTH_KEY_FILE="$AUTH_DIR/scrcpy-auth.key"

    if [ ! -f "$AUTH_KEY_FILE" ]; then
        echo "Generating new auth key..."
        mkdir -p "$AUTH_DIR"
        # Generate 32 bytes random key (hex encoded)
        openssl rand -hex 32 > "$AUTH_KEY_FILE" 2>/dev/null || \
            python3 -c "import secrets; print(secrets.token_hex(32))" > "$AUTH_KEY_FILE"
        chmod 600 "$AUTH_KEY_FILE" 2>/dev/null || true
    fi

    AUTH_KEY=$(cat "$AUTH_KEY_FILE")
    echo "Auth key: ${AUTH_KEY:0:16}..."

    # Push auth key to device
    echo "Pushing auth key..."
    adb push "$AUTH_KEY_FILE" /data/local/tmp/scrcpy-auth.key

    # Save auth key for client (by device serial)
    DEVICE_SERIAL=$(adb get-serialno 2>/dev/null || echo "unknown")
    if [ "$DEVICE_SERIAL" != "unknown" ]; then
        DEVICE_KEY_FILE="$AUTH_DIR/$DEVICE_SERIAL.key"
        cp "$AUTH_KEY_FILE" "$DEVICE_KEY_FILE"
        chmod 600 "$DEVICE_KEY_FILE" 2>/dev/null || true
        echo "Auth key saved for client: $DEVICE_KEY_FILE"
    fi
else
    echo ""
    echo "=== Authentication disabled ==="
fi

if [ "$PUSH_ONLY" = true ]; then
    echo ""
    echo "=== Done (push only) ==="
    echo "Server pushed. Start manually or use test_network_direct.py"
    exit 0
fi

echo ""
echo "=== Starting server ==="

# Kill existing server
echo "Killing existing server..."
adb shell "pkill -f 'com.genymobile.scrcpy.Server'" 2>/dev/null || true
sleep 1

# Build server command
LOG_LEVEL="info"
if [ "$VERBOSE_LOG" = true ]; then
    LOG_LEVEL="verbose"
fi

AUTH_PARAM=""
if [ "$AUTH_ENABLED" = true ]; then
    AUTH_PARAM="auth_key_file=/data/local/tmp/scrcpy-auth.key"
fi

SERVER_CMD="CLASSPATH=/data/local/tmp/scrcpy-server.apk app_process / com.genymobile.scrcpy.Server 3.3.4 log_level=$LOG_LEVEL control_port=27184 video_port=27185 audio_port=27186 video_codec=h264 video_bit_rate=3000000 max_fps=60 audio=true audio_source=output $AUTH_PARAM"

# Start with nohup to survive ADB disconnect
echo "Starting server..."
adb shell "nohup setsid sh -c '$SERVER_CMD' > /data/local/tmp/scrcpy_server.log 2>&1 &"

sleep 2

# Verify server is running
if adb shell "pgrep -f 'com.genymobile.scrcpy.Server'" | grep -q .; then
    echo ""
    echo "=== Server started successfully ==="
    echo "Control port: 27184"
    echo "Video port: 27185"
    echo "Audio port: 27186"
    echo ""
    echo "Connect with: python tests_gui/test_network_direct.py --ip <device_ip>"
else
    echo ""
    echo "=== Warning: Server may not have started ==="
    echo "Check log: adb shell cat /data/local/tmp/scrcpy_server.log"
fi
