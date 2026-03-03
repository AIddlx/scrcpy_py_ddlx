#!/bin/bash
# Push scrcpy-server to device
# Usage: ./push_server.sh [--verbose]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SERVER_FILE="$PROJECT_ROOT/scrcpy-server"
DEVICE_PATH="/data/local/tmp/scrcpy-server.apk"

# Check if server file exists
if [ ! -f "$SERVER_FILE" ]; then
    echo "Building server first..."
    cd "$SCRIPT_DIR"
    bash build_server.sh
fi

# Push to device
echo "Pushing $SERVER_FILE to $DEVICE_PATH..."
# Use double-slash to prevent Git Bash path conversion
adb push "$SERVER_FILE" //data/local/tmp/scrcpy-server.apk 2>/dev/null || adb push "$SERVER_FILE" /data/local/tmp/scrcpy-server.apk

# Optional: enable verbose logging
if [ "$1" == "--verbose" ] || [ "$1" == "-v" ]; then
    echo ""
    echo "To enable VERBOSE logging on server, add --log-level=VERBOSE to client args"
    echo "Or set LOG_LEVEL=VERBOSE environment variable"
fi

echo "Done."
