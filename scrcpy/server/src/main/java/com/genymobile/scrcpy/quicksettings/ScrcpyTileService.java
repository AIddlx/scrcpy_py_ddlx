package com.genymobile.scrcpy.quicksettings;

import android.graphics.drawable.Icon;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;

import com.genymobile.scrcpy.R;
import com.genymobile.scrcpy.util.Ln;

import java.io.BufferedReader;
import java.io.InputStreamReader;

/**
 * Quick Settings Tile for controlling scrcpy server.
 *
 * Provides a toggle in the notification shade to:
 * - Start/stop the scrcpy server
 * - Show current server status
 *
 * Usage:
 * 1. Add tile from Quick Settings edit menu
 * 2. Tap to toggle server on/off
 */
public class ScrcpyTileService extends TileService {

    private static final String TAG = "ScrcpyTile";

    // Server state
    private static boolean sServerRunning = false;
    private static boolean sStayAliveMode = false;

    @Override
    public void onCreate() {
        super.onCreate();
        Ln.d(TAG + ": TileService created");
    }

    @Override
    public void onStartListening() {
        super.onStartListening();
        Ln.d(TAG + ": Tile started listening");
        updateTile();
    }

    @Override
    public void onStopListening() {
        super.onStopListening();
        Ln.d(TAG + ": Tile stopped listening");
    }

    @Override
    public void onClick() {
        super.onClick();
        Ln.i(TAG + ": Tile clicked");

        // Check current state and toggle
        boolean isRunning = isServerRunning();

        if (isRunning) {
            // Stop server
            Ln.i(TAG + ": Stopping server...");
            stopServer();
        } else {
            // Start server in stay-alive mode
            Ln.i(TAG + ": Starting server...");
            startServer(true);
        }

        // Update tile state
        updateTile();
    }

    /**
     * Update the tile appearance based on current server state.
     */
    private void updateTile() {
        Tile tile = getQsTile();
        if (tile == null) {
            return;
        }

        boolean isRunning = isServerRunning();
        sServerRunning = isRunning;

        if (isRunning) {
            // Server is running - show active state
            tile.setState(Tile.STATE_ACTIVE);
            tile.setLabel("Scrcpy 运行中");
            tile.setSubtitle("点击停止");
        } else {
            // Server is stopped - show inactive state
            tile.setState(Tile.STATE_INACTIVE);
            tile.setLabel("Scrcpy 已停止");
            tile.setSubtitle("点击启动");
        }

        tile.updateTile();
        Ln.d(TAG + ": Tile updated, running=" + isRunning);
    }

    /**
     * Check if scrcpy server is running.
     */
    private boolean isServerRunning() {
        try {
            Process process = Runtime.getRuntime().exec("ps -A");
            BufferedReader reader = new BufferedReader(
                new InputStreamReader(process.getInputStream()));
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.contains("app_process") && line.contains("scrcpy")) {
                    reader.close();
                    return true;
                }
            }
            reader.close();
            return false;
        } catch (Exception e) {
            Ln.e(TAG + ": Failed to check server status: " + e.getMessage());
            return false;
        }
    }

    /**
     * Start the scrcpy server.
     *
     * @param stayAlive Enable hot-connection mode
     */
    private void startServer(boolean stayAlive) {
        new Thread(() -> {
            try {
                String stayAliveStr = stayAlive ? "true" : "false";

                // Build server command
                String cmd = String.format(
                    "CLASSPATH=/data/local/tmp/scrcpy-server.apk " +
                    "app_process / com.genymobile.scrcpy.Server 3.3.4 " +
                    "log_level=info " +
                    "control_port=27184 video_port=27185 audio_port=27186 " +
                    "stay_alive=%s " +
                    "video=true audio=false control=true " +
                    "send_device_meta=true send_dummy_byte=true cleanup=false " +
                    "> /data/local/tmp/scrcpy_server.log 2>&1 &",
                    stayAliveStr
                );

                // Execute in shell
                Runtime.getRuntime().exec(new String[]{"sh", "-c", cmd});

                sStayAliveMode = stayAlive;

                // Wait a moment for server to start
                Thread.sleep(1000);

                // Update tile on main thread
                if (isServerRunning()) {
                    Ln.i(TAG + ": Server started successfully");
                } else {
                    Ln.e(TAG + ": Server failed to start");
                }

            } catch (Exception e) {
                Ln.e(TAG + ": Failed to start server: " + e.getMessage());
            }
        }).start();
    }

    /**
     * Stop the scrcpy server.
     */
    private void stopServer() {
        new Thread(() -> {
            try {
                // Kill app_process running scrcpy
                Runtime.getRuntime().exec("pkill -f app_process");

                // Wait for process to die
                Thread.sleep(500);

                Ln.i(TAG + ": Server stopped");

            } catch (Exception e) {
                Ln.e(TAG + ": Failed to stop server: " + e.getMessage());
            }
        }).start();
    }
}
