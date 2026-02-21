package com.genymobile.scrcpy.companion;

import android.graphics.drawable.Icon;
import android.os.Handler;
import android.os.Looper;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;
import android.util.Log;
import android.widget.Toast;

import java.io.BufferedReader;
import java.io.InputStreamReader;

/**
 * Quick Settings Tile for scrcpy server management.
 */
public class ScrcpyTileService extends TileService {

    private static final String TAG = "ScrcpyTile";

    @Override
    public void onStartListening() {
        super.onStartListening();
        // Update tile in background thread
        new Thread(new Runnable() {
            @Override
            public void run() {
                updateTileAsync();
            }
        }).start();
    }

    @Override
    public void onClick() {
        // Always try to terminate, show result
        new Thread(new Runnable() {
            @Override
            public void run() {
                boolean wasRunning = UdpClient.isServerRunning();

                if (wasRunning) {
                    boolean success = UdpClient.sendTerminateRequest();

                    // Wait for server to actually stop
                    try { Thread.sleep(500); } catch (Exception e) {}

                    final boolean stillRunning = UdpClient.isServerRunning();
                    final String message = stillRunning ? "终止失败" : "已终止服务器";

                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            Toast.makeText(getApplicationContext(), message, Toast.LENGTH_SHORT).show();
                        }
                    });
                } else {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            Toast.makeText(getApplicationContext(), "服务器未运行", Toast.LENGTH_SHORT).show();
                        }
                    });
                }

                // Update tile state
                updateTileAsync();
            }
        }).start();
    }

    private void updateTile() {
        // Check server status in background, then update UI on main thread
        new Thread(new Runnable() {
            @Override
            public void run() {
                final boolean running = UdpClient.isServerRunning();

                new Handler(Looper.getMainLooper()).post(new Runnable() {
                    @Override
                    public void run() {
                        Tile tile = getQsTile();
                        if (tile == null) return;

                        if (running) {
                            tile.setState(Tile.STATE_ACTIVE);
                            tile.setLabel("Scrcpy");
                            tile.setSubtitle("运行中 · 点击终止");
                        } else {
                            tile.setState(Tile.STATE_INACTIVE);
                            tile.setLabel("Scrcpy");
                            tile.setSubtitle("未运行");
                        }

                        tile.updateTile();
                        Log.d(TAG, "Tile updated: running=" + running);
                    }
                });
            }
        }).start();
    }

    private void updateTileAsync() {
        updateTile();  // Now this is safe - it does network in background
    }

    private void showToast(final String message) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                Toast.makeText(getApplicationContext(), message, Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void runOnUiThread(Runnable action) {
        new Handler(Looper.getMainLooper()).post(action);
    }
}
