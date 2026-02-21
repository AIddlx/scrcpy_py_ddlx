#!/bin/bash
set -ex

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname ${BASH_SOURCE[0]})" && pwd)"
RELEASE_DIR="$SCRIPT_DIR"
SCRCPY_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$SCRCPY_DIR")"

# Source build_common from release directory
cd "$RELEASE_DIR"
. build_common

# Now work from project root
cd "$PROJECT_ROOT"

GRADLE="${GRADLE:-./scrcpy/gradlew}"
SERVER_BUILD_DIR="$RELEASE_DIR/work/build-server"

rm -rf "$SERVER_BUILD_DIR"
"$GRADLE" -p scrcpy/server assembleRelease
mkdir -p "$SERVER_BUILD_DIR/server"
cp scrcpy/server/build/outputs/apk/release/server-release-unsigned.apk \
    "$SERVER_BUILD_DIR/server/scrcpy-server"

# Copy to project root for test scripts
cp "$SERVER_BUILD_DIR/server/scrcpy-server" ./scrcpy-server
echo "Server copied to project root: $(pwd)/scrcpy-server"
