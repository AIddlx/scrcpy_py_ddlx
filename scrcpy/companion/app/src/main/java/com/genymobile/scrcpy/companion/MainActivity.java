package com.genymobile.scrcpy.companion;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.BufferedReader;
import java.io.InputStreamReader;

/**
 * Main GUI activity for scrcpy server management.
 */
public class MainActivity extends Activity {

    private static final String TAG = "ScrcpyManager";

    private static final int FILTER_ALL = 0;
    private static final int FILTER_INFO = 1;
    private static final int FILTER_WARN = 2;
    private static final int FILTER_ERROR = 3;

    private TextView tvStatusSummary;
    private TextView tvServerStatus;
    private TextView tvServerPid;
    private TextView tvServerPorts;
    private TextView tvServerMode;
    private TextView tvDeviceName;
    private TextView tvDeviceIp;
    private TextView tvLog;
    private Button btnRefresh;
    private Button btnTerminate;

    private TextView btnFilterAll;
    private TextView btnFilterInfo;
    private TextView btnFilterWarn;
    private TextView btnFilterError;
    private TextView btnToggleLog;
    private LinearLayout logSection;
    private ScrollView logScroll;

    private int currentFilter = FILTER_ALL;
    private boolean logVisible = true;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        tvStatusSummary = findViewById(R.id.tvStatusSummary);
        tvServerStatus = findViewById(R.id.tvServerStatus);
        tvServerPid = findViewById(R.id.tvServerPid);
        tvServerPorts = findViewById(R.id.tvServerPorts);
        tvServerMode = findViewById(R.id.tvServerMode);
        tvDeviceName = findViewById(R.id.tvDeviceName);
        tvDeviceIp = findViewById(R.id.tvDeviceIp);
        tvLog = findViewById(R.id.tvLog);
        btnRefresh = findViewById(R.id.btnRefresh);
        btnTerminate = findViewById(R.id.btnTerminate);

        btnFilterAll = findViewById(R.id.btnFilterAll);
        btnFilterInfo = findViewById(R.id.btnFilterInfo);
        btnFilterWarn = findViewById(R.id.btnFilterWarn);
        btnFilterError = findViewById(R.id.btnFilterError);
        btnToggleLog = findViewById(R.id.btnToggleLog);
        logSection = findViewById(R.id.logSection);
        logScroll = findViewById(R.id.logScroll);

        btnRefresh.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                refreshStatus();
            }
        });

        btnTerminate.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                terminateServer();
            }
        });

        // Log filter buttons
        btnFilterAll.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                setFilter(FILTER_ALL);
            }
        });

        btnFilterInfo.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                setFilter(FILTER_INFO);
            }
        });

        btnFilterWarn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                setFilter(FILTER_WARN);
            }
        });

        btnFilterError.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                setFilter(FILTER_ERROR);
            }
        });

        btnToggleLog.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                toggleLogVisibility();
            }
        });

        // Initial status check
        refreshStatus();
    }

    private void setFilter(int filter) {
        currentFilter = filter;
        updateFilterButtons();
        refreshLogOnly();
    }

    private void updateFilterButtons() {
        // Reset all buttons
        resetFilterButton(btnFilterAll);
        resetFilterButton(btnFilterInfo);
        resetFilterButton(btnFilterWarn);
        resetFilterButton(btnFilterError);

        // Highlight selected
        TextView selected = null;
        switch (currentFilter) {
            case FILTER_ALL: selected = btnFilterAll; break;
            case FILTER_INFO: selected = btnFilterInfo; break;
            case FILTER_WARN: selected = btnFilterWarn; break;
            case FILTER_ERROR: selected = btnFilterError; break;
        }
        if (selected != null) {
            selected.setBackgroundColor(0xFF2196F3);
            selected.setTextColor(0xFFFFFFFF);
        }
    }

    private void resetFilterButton(TextView btn) {
        btn.setBackgroundColor(0xFFE0E0E0);
        btn.setTextColor(0xFF666666);
    }

    private void toggleLogVisibility() {
        logVisible = !logVisible;
        logScroll.setVisibility(logVisible ? View.VISIBLE : View.GONE);
        btnToggleLog.setText(logVisible ? "▲" : "▼");
    }

    private void refreshStatus() {
        setButtonsEnabled(false);
        tvServerStatus.setText("状态：检测中...");
        tvStatusSummary.setText("状态：检测中...");

        new Thread(new Runnable() {
            @Override
            public void run() {
                // Get discovery info
                String discoveryInfo = UdpClient.discoverServer();
                final boolean running = discoveryInfo != null;

                // Get server log
                final String log = getServerLog(20);

                // Parse discovery info using UdpClient helpers
                String deviceName = UdpClient.parseDeviceName(discoveryInfo);
                String deviceIp = UdpClient.parseIp(discoveryInfo);
                String mode = UdpClient.parseMode(discoveryInfo);

                final String finalDeviceName = deviceName;
                final String finalDeviceIp = deviceIp;
                final String finalMode = mode;
                final String statusText = running ? "运行中" : "未运行";
                final String statusSummary = "状态：" + statusText;

                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        tvStatusSummary.setText(statusSummary);
                        tvServerStatus.setText("状态：" + statusText);
                        tvDeviceName.setText("设备：" + finalDeviceName);
                        tvDeviceIp.setText("IP：" + finalDeviceIp);

                        if (running) {
                            String modeDisplay = "stay-alive".equals(finalMode) ? "热连接 (Stay-Alive)" :
                                                 "single".equals(finalMode) ? "一次性 (Single)" :
                                                 "网络直连";
                            tvServerMode.setText("模式：" + modeDisplay);
                            tvServerPorts.setText("端口：控制 27184 / 视频 27185 / 音频 27186");
                            tvServerPid.setText("PID：可通过 ADB 查看");
                        } else {
                            tvServerMode.setText("模式：--");
                            tvServerPid.setText("PID：--");
                        }

                        tvLog.setText(log.isEmpty() ? "暂无日志" : log);

                        setButtonsEnabled(true);
                    }
                });
            }
        }).start();
    }

    private void terminateServer() {
        setButtonsEnabled(false);
        tvServerStatus.setText("状态：正在终止...");

        new Thread(new Runnable() {
            @Override
            public void run() {
                boolean wasRunning = UdpClient.isServerRunning();

                if (wasRunning) {
                    boolean success = UdpClient.sendTerminateRequest();

                    // Wait for server to stop
                    try { Thread.sleep(500); } catch (Exception e) {}

                    final boolean stillRunning = UdpClient.isServerRunning();
                    final String message = stillRunning ? "终止失败" : "服务器已终止";

                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            Toast.makeText(MainActivity.this, message, Toast.LENGTH_SHORT).show();
                        }
                    });
                } else {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            Toast.makeText(MainActivity.this, "服务器未运行", Toast.LENGTH_SHORT).show();
                        }
                    });
                }

                // Refresh status
                try { Thread.sleep(300); } catch (Exception e) {}
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        refreshStatus();
                    }
                });
            }
        }).start();
    }

    private void setButtonsEnabled(boolean enabled) {
        btnRefresh.setEnabled(enabled);
        btnTerminate.setEnabled(enabled);
    }

    private void refreshLogOnly() {
        tvLog.setText("刷新中...");

        new Thread(new Runnable() {
            @Override
            public void run() {
                final String log = getServerLog(20);

                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        tvLog.setText(log.isEmpty() ? "暂无日志" : log);
                    }
                });
            }
        }).start();
    }

    private String getServerLog(int lines) {
        try {
            Process process = Runtime.getRuntime().exec("tail -200 /data/local/tmp/scrcpy_server.log");
            BufferedReader reader = new BufferedReader(
                    new InputStreamReader(process.getInputStream()));
            StringBuilder sb = new StringBuilder();
            String line;
            int count = 0;
            while ((line = reader.readLine()) != null && count < lines) {
                // Apply filter
                if (!shouldShowLine(line)) {
                    continue;
                }
                // Truncate long lines
                if (line.length() > 80) {
                    line = line.substring(0, 77) + "...";
                }
                sb.append(line).append("\n");
                count++;
            }
            reader.close();
            String result = sb.toString().trim();
            return result.isEmpty() ? "暂无匹配日志" : result;
        } catch (Exception e) {
            return "无法读取日志";
        }
    }

    private boolean shouldShowLine(String line) {
        // Always filter out verbose UDP and DEBUG
        if (line.contains("UDP sent") || line.contains("UDP send") || line.contains("UDP packet")) {
            return false;
        }

        switch (currentFilter) {
            case FILTER_ALL:
                return true;
            case FILTER_INFO:
                return line.contains("INFO") || line.contains("I scrcpy") || line.contains("I ]");
            case FILTER_WARN:
                return line.contains("WARN") || line.contains("W scrcpy") || line.contains("W ]");
            case FILTER_ERROR:
                return line.contains("ERROR") || line.contains("E scrcpy") || line.contains("E ]");
            default:
                return true;
        }
    }
}
