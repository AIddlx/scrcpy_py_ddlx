#!/usr/bin/env bash
#
# Build scrcpy companion app (Quick Settings Tile)
# Minimal build script without Gradle
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR/app"
BUILD_DIR="$SCRIPT_DIR/build"

# Android SDK paths
ANDROID_HOME="${ANDROID_HOME:-${LOCALAPPDATA:-$HOME}/Android/Sdk}"
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    # Windows
    ANDROID_HOME="${ANDROID_HOME:-C:/Users/$USER/AppData/Local/Android/Sdk}"
fi

PLATFORM="${ANDROID_PLATFORM:-34}"
BUILD_TOOLS="${ANDROID_BUILD_TOOLS:-34.0.0}"
ANDROID_JAR="$ANDROID_HOME/platforms/android-$PLATFORM/android.jar"
BUILD_TOOLS_DIR="$ANDROID_HOME/build-tools/$BUILD_TOOLS"
AAPT2="$BUILD_TOOLS_DIR/aapt2"
D8="$BUILD_TOOLS_DIR/d8"
ZIPALIGN="$BUILD_TOOLS_DIR/zipalign"
APKSIGNER="$BUILD_TOOLS_DIR/apksigner"

echo "=== Building Scrcpy Companion ==="
echo "ANDROID_HOME: $ANDROID_HOME"
echo "Platform: android-$PLATFORM"
echo "Build-tools: $BUILD_TOOLS"

# Check tools exist
if [[ ! -f "$ANDROID_JAR" ]]; then
    echo "ERROR: android.jar not found at $ANDROID_JAR"
    exit 1
fi

# Clean and create build dirs
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"/{gen,classes,dex,compiled_res,apk}

# Step 1: Compile resources with aapt2
echo ""
echo "[1/6] Compiling resources..."
mkdir -p "$BUILD_DIR/compiled_res"

# Create minimal resources
mkdir -p "$BUILD_DIR/res/values"
mkdir -p "$BUILD_DIR/res/drawable"

# strings.xml
cat > "$BUILD_DIR/res/values/strings.xml" << 'EOF'
<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">Scrcpy Companion</string>
    <string name="tile_label">Scrcpy</string>
</resources>
EOF

# ic_tile.xml (simple phone icon)
cat > "$BUILD_DIR/res/drawable/ic_tile.xml" << 'EOF'
<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="24dp" android:height="24dp"
    android:viewportWidth="24" android:viewportHeight="24">
    <path android:fillColor="#FFFFFFFF"
        android:pathData="M17,1.01L7,1C5.9,1 5,1.9 5,3v18c0,1.1 0.9,2 2,2h10c1.1,0 2,-0.9 2,-2V3C19,1.9 18.1,1.01 17,1.01zM17,19H7V5h10V19z"/>
</vector>
EOF

# Use Unix-style paths for aapt2
RES_DIR_UNIX=$(cygpath -u "$BUILD_DIR/res" 2>/dev/null || echo "$BUILD_DIR/res")
COMPILED_RES_UNIX=$(cygpath -u "$BUILD_DIR/compiled_res" 2>/dev/null || echo "$BUILD_DIR/compiled_res")

$AAPT2 compile -o "$COMPILED_RES_UNIX" \
    "$RES_DIR_UNIX/values/strings.xml" \
    "$RES_DIR_UNIX/drawable/ic_tile.xml"

# Step 2: Link resources and generate R.java
echo "[2/6] Linking resources..."

# Create AndroidManifest.xml
cat > "$BUILD_DIR/AndroidManifest.xml" << 'EOF'
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.genymobile.scrcpy.companion">
    <uses-permission android:name="android.permission.BIND_QUICK_SETTINGS_TILE" />
    <application android:label="@string/app_name">
        <service
            android:name=".ScrcpyTileService"
            android:label="@string/tile_label"
            android:icon="@drawable/ic_tile"
            android:permission="android.permission.BIND_QUICK_SETTINGS_TILE"
            android:exported="true">
            <intent-filter>
                <action android:name="android.service.quicksettings.action.QS_TILE" />
            </intent-filter>
        </service>
    </application>
</manifest>
EOF

$AAPT2 link -o "$BUILD_DIR/apk/base.apk" \
    --manifest "$BUILD_DIR/AndroidManifest.xml" \
    -I "$ANDROID_JAR" \
    --java "$BUILD_DIR/gen" \
    --auto-add-overlay \
    "$BUILD_DIR/compiled_res"/*

# Step 3: Compile Java sources
echo "[3/6] Compiling Java sources..."

# Create TileService source
mkdir -p "$BUILD_DIR/src/com/genymobile/scrcpy/companion"
cat > "$BUILD_DIR/src/com/genymobile/scrcpy/companion/ScrcpyTileService.java" << 'JAVASRC'
package com.genymobile.scrcpy.companion;

import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;
import java.io.*;

public class ScrcpyTileService extends TileService {
    private static final String SERVER_APK = "/data/local/tmp/scrcpy-server.apk";

    @Override
    public void onStartListening() {
        updateTile();
    }

    @Override
    public void onClick() {
        if (isServerRunning()) stopServer();
        else startServer();
        try { Thread.sleep(500); } catch (Exception e) {}
        updateTile();
    }

    private void updateTile() {
        Tile tile = getQsTile();
        if (tile == null) return;
        boolean running = isServerRunning();
        tile.setState(running ? Tile.STATE_ACTIVE : Tile.STATE_INACTIVE);
        tile.setLabel(running ? "Scrcpy 运行中" : "Scrcpy");
        tile.setSubtitle(running ? "点击停止" : "点击启动");
        tile.updateTile();
    }

    private boolean isServerRunning() {
        try {
            Process p = Runtime.getRuntime().exec("ps -A");
            BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()));
            String l; while ((l = r.readLine()) != null) if (l.contains("app_process")) { r.close(); return true; }
            r.close();
        } catch (Exception e) {}
        return false;
    }

    private void startServer() {
        new Thread(() -> {
            try {
                String cmd = "CLASSPATH=" + SERVER_APK + " nohup app_process / " +
                    "com.genymobile.scrcpy.Server 3.3.4 log_level=info " +
                    "control_port=27184 video_port=27185 audio_port=27186 stay_alive=true " +
                    "video=true audio=false control=true send_device_meta=true " +
                    "send_dummy_byte=true cleanup=false > /data/local/tmp/scrcpy_server.log 2>&1 &";
                Runtime.getRuntime().exec(new String[]{"sh", "-c", cmd});
            } catch (Exception e) {}
        }).start();
    }

    private void stopServer() {
        new Thread(() -> {
            try { Runtime.getRuntime().exec("pkill -f app_process"); }
            catch (Exception e) {}
        }).start();
    }
}
JAVASRC

# Find all java files
SOURCES=$(find "$BUILD_DIR/src" -name "*.java")
R_JAVA=$(find "$BUILD_DIR/gen" -name "*.java")

javac -encoding UTF-8 \
    -bootclasspath "$ANDROID_JAR" \
    -source 1.8 -target 1.8 \
    -d "$BUILD_DIR/classes" \
    $SOURCES $R_JAVA 2>/dev/null || {
    # Fallback for Windows javac
    javac -encoding UTF-8 \
        -bootclasspath "$ANDROID_JAR" \
        -source 1.8 -target 1.8 \
        -d "$BUILD_DIR/classes" \
        "$BUILD_DIR/src/com/genymobile/scrcpy/companion/ScrcpyTileService.java" \
        "$BUILD_DIR/gen/com/genymobile/scrcpy/companion/R.java"
}

# Step 4: Convert to DEX
echo "[4/6] Converting to DEX..."
# Create jar from all class files (including inner classes)
cd "$BUILD_DIR/classes"
jar cf "$BUILD_DIR/classes.jar" com
cd "$SCRIPT_DIR"
$D8 --output "$BUILD_DIR/dex" \
    --lib "$ANDROID_JAR" \
    "$BUILD_DIR/classes.jar"

# Step 5: Create final APK
echo "[5/6] Creating APK..."
cd "$BUILD_DIR/apk"
cp ../dex/classes.dex .
zip -r -q "../scrcpy-companion-unsigned.apk" classes.dex AndroidManifest.xml resources.arsc res/ 2>/dev/null || \
    zip -r -q "../scrcpy-companion-unsigned.apk" classes.dex

# Step 6: Align and sign
echo "[6/6] Signing APK..."
$ZIPALIGN -f 4 "$BUILD_DIR/scrcpy-companion-unsigned.apk" "$BUILD_DIR/scrcpy-companion-aligned.apk"

# Try to sign with debug keystore
DEBUG_KEYSTORE="$HOME/.android/debug.keystore"
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    DEBUG_KEYSTORE="$USERPROFILE/.android/debug.keystore"
fi

if [[ -f "$DEBUG_KEYSTORE" ]]; then
    $APKSIGNER sign --ks "$DEBUG_KEYSTORE" --ks-pass pass:android \
        --out "$SCRIPT_DIR/scrcpy-companion.apk" \
        "$BUILD_DIR/scrcpy-companion-aligned.apk" 2>/dev/null && echo "[OK] Signed with debug key"
else
    # Create debug keystore if needed
    keytool -genkey -v -keystore "$BUILD_DIR/debug.keystore" \
        -alias androiddebugkey -storepass android -keypass android \
        -keyalg RSA -keysize 2048 -validity 10000 \
        -dname "CN=Android Debug,O=Android,C=US" 2>/dev/null

    $APKSIGNER sign --ks "$BUILD_DIR/debug.keystore" \
        --ks-pass pass:android --key-pass pass:android \
        --out "$SCRIPT_DIR/scrcpy-companion.apk" \
        "$BUILD_DIR/scrcpy-companion-aligned.apk" 2>/dev/null && echo "[OK] Signed with new debug key"
fi

# Clean up
rm -rf "$BUILD_DIR"

echo ""
echo "=== Build Complete ==="
echo "Output: $SCRIPT_DIR/scrcpy-companion.apk"
echo ""
echo "Installation:"
echo "  1. adb install scrcpy-companion.apk"
echo "  2. Pull down notification shade"
echo "  3. Edit Quick Settings (pencil icon)"
echo "  4. Find 'Scrcpy' and drag to active tiles"
echo "  5. Tap tile to start/stop server"
