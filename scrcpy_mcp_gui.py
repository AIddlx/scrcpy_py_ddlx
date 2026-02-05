#!/usr/bin/env python3
"""
Scrcpy MCP Server GUI - A visual interface for configuring and running the MCP server

Features:
- Visual configuration of connection parameters
- Embedded real-time video display
- Audio streaming control and playback
- MCP server startup (stdio mode for Claude Code)
- Device status monitoring
- Tool testing interface
"""

import sys
import logging
import subprocess
import json
import time
from pathlib import Path
from typing import Optional
from datetime import datetime

# Qt imports
try:
    from PySide6.QtWidgets import (
        QApplication,
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QPushButton,
        QLabel,
        QLineEdit,
        QTextEdit,
        QCheckBox,
        QComboBox,
        QSpinBox,
        QGroupBox,
        QTabWidget,
        QStatusBar,
        QFileDialog,
        QSplitter,
    )
    from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer, QProcess
    from PySide6.QtGui import QTextCursor

    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False
    print("Error: PySide6 is required. Install with: pip install PySide6")
    sys.exit(1)

# scrcpy imports
try:
    from scrcpy_py_ddlx import ScrcpyClient, ClientConfig
    from scrcpy_py_ddlx.core.player.video.video_window import VideoWindow
except ImportError as e:
    print(f"Error: {e}")
    sys.exit(1)


class MCPServerManager(QObject):
    """Manages MCP server process"""

    log_received = Signal(str)
    server_started = Signal()
    server_stopped = Signal()
    error_occurred = Signal(str)

    def __init__(self):
        super().__init__()
        self.process: Optional[subprocess.Popen] = None
        self.mcp_script_path = Path(__file__).parent / "mcp_stdio.py"

    def start(self, config: dict):
        """Start MCP server process"""
        if self.process and self.process.poll() is None:
            self.error_occurred.emit("MCP Server already running")
            return False

        try:
            # Start MCP server in background (stdio mode)
            # The server will wait for Claude Code to connect via stdin/stdout
            self.process = subprocess.Popen(
                [sys.executable, str(self.mcp_script_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            self.log_received.emit(f"MCP Server started (PID: {self.process.pid})")
            self.log_received.emit("Waiting for Claude Code to connect...")
            self.server_started.emit()
            return True
        except Exception as e:
            self.error_occurred.emit(f"Failed to start MCP server: {e}")
            return False

    def stop(self):
        """Stop MCP server process"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except:
                self.process.kill()
            self.process = None
            self.log_received.emit("MCP Server stopped")
            self.server_stopped.emit()

    def is_running(self) -> bool:
        """Check if server is running"""
        return self.process is not None and self.process.poll() is None


class StreamingClientManager(QObject):
    """Manages streaming client with video and audio"""

    connected = Signal(bool)
    disconnected = Signal()
    error_occurred = Signal(str)
    frame_ready = Signal()  # Signal when new frame is available
    audio_started = Signal()
    audio_stopped = Signal()

    def __init__(self, video_window: Optional["VideoWindow"] = None):
        super().__init__()
        self.client: Optional[ScrcpyClient] = None
        self._thread: Optional[QThread] = None
        self._video_window = video_window
        self._audio_enabled = False
        self._video_enabled = False

    def connect_async(self, config: ClientConfig):
        """Connect in background thread"""
        if self._thread and self._thread.isRunning():
            return False

        self._thread = StreamingConnectThread(self, config, self._video_window)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()
        return True

    def disconnect_now(self):
        """Disconnect immediately"""
        if self.client:
            try:
                self.client.disconnect()
            except:
                pass
            self.client = None
        # Hide video window if visible
        if self._video_window:
            self._video_window.hide()

    def _on_thread_finished(self):
        """Handle thread completion"""
        self._thread = None

    def is_connected(self) -> bool:
        """Check if connected"""
        return self.client is not None and self.client.state.connected

    def toggle_video(self, enabled: bool):
        """Toggle video display"""
        self._video_enabled = enabled
        if self._video_window:
            if enabled:
                self._video_window.show()
            else:
                self._video_window.hide()

    def toggle_audio(self, enabled: bool):
        """Toggle audio playback"""
        self._audio_enabled = enabled
        # Audio is controlled via client config, requires reconnect


class ScrcpyDeviceManager(QObject):
    """Manages device connection in background thread (simplified, no video/audio)"""

    connected = Signal(bool)
    disconnected = Signal()
    error_occurred = Signal(str)
    state_changed = Signal(dict)

    def __init__(self):
        super().__init__()
        self.client: Optional[ScrcpyClient] = None
        self._thread: Optional[QThread] = None

    def connect_async(self, config: ClientConfig):
        """Connect in background thread"""
        if self._thread and self._thread.isRunning():
            return False

        self._thread = ConnectThread(self, config)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()
        return True

    def disconnect_now(self):
        """Disconnect immediately"""
        if self.client:
            try:
                self.client.disconnect()
            except:
                pass
            self.client = None

    def _on_thread_finished(self):
        """Handle thread completion"""
        self._thread = None

    def is_connected(self) -> bool:
        """Check if connected"""
        return self.client is not None and self.client.state.connected


class ConnectThread(QThread):
    """Background thread for connecting"""

    finished = Signal()

    def __init__(self, manager: ScrcpyDeviceManager, config: ClientConfig):
        super().__init__()
        self.manager = manager
        self.config = config

    def run(self):
        """Connect in background"""
        try:
            client = ScrcpyClient(self.config)
            success = client.connect()
            if success:
                self.manager.client = client
                self.manager.connected.emit(True)
            else:
                self.manager.error_occurred.emit("Connection failed")
                self.manager.connected.emit(False)
        except Exception as e:
            self.manager.error_occurred.emit(str(e))
            self.manager.connected.emit(False)
        finally:
            self.finished.emit()


class StreamingConnectThread(QThread):
    """Background thread for streaming connection with video/audio"""

    finished = Signal()

    def __init__(
        self, manager: StreamingClientManager, config: ClientConfig, video_window=None
    ):
        super().__init__()
        self.manager = manager
        self.config = config
        self.video_window = video_window

    def run(self):
        """Connect in background with video window"""
        try:
            # Don't modify frame_callback - let the client handle video/audio normally
            # We just need to connect the video window after connection
            client = ScrcpyClient(self.config)
            success = client.connect()
            if success:
                self.manager.client = client

                # Connect video window to client's decoder and control queue
                if self.video_window and hasattr(client, "_video_decoder"):
                    # Connect delay buffer for video display
                    frame_buffer = client._video_decoder._frame_buffer
                    self.video_window.set_delay_buffer(frame_buffer)

                    # Connect control queue for input handling
                    if hasattr(client, "_control_queue"):
                        self.video_window.set_control_queue(client._control_queue)

                self.manager.connected.emit(True)
            else:
                self.manager.error_occurred.emit("Connection failed")
                self.manager.connected.emit(False)
        except Exception as e:
            self.manager.error_occurred.emit(str(e))
            self.manager.connected.emit(False)
        finally:
            self.finished.emit()


class ScrcpyMCPConfigDialog(QWidget):
    """Configuration dialog for scrcpy MCP server"""

    def __init__(
        self, mcp_manager: MCPServerManager, device_manager: ScrcpyDeviceManager
    ):
        super().__init__()
        self.mcp_manager = mcp_manager
        self.device_manager = device_manager
        self.config = {}
        self.setup_ui()

        # Connect signals
        self.mcp_manager.log_received.connect(self.on_log_received)
        self.mcp_manager.error_occurred.connect(self.on_error)

    def setup_ui(self):
        """Setup configuration UI"""
        layout = QVBoxLayout()

        # Connection settings
        conn_group = QGroupBox("连接设置")
        conn_layout = QVBoxLayout()

        # Show window
        self.show_window_cb = QCheckBox("显示实时画面窗口")
        self.show_window_cb.setChecked(True)

        # Control enabled
        self.control_cb = QCheckBox("启用控制功能")
        self.control_cb.setChecked(True)

        # Audio enabled
        self.audio_cb = QCheckBox("启用音频流")
        self.audio_cb.setChecked(True)

        # TCP/IP wireless mode
        self.tcpip_cb = QCheckBox("TCP/IP 无线模式 (推荐)")
        self.tcpip_cb.setChecked(True)

        # Stay awake
        self.stay_awake_cb = QCheckBox("保持设备唤醒")
        self.stay_awake_cb.setChecked(True)

        # Server mode
        server_mode_layout = QHBoxLayout()
        server_mode_label = QLabel("MCP 服务器模式:")
        self.server_mode_combo = QComboBox()
        self.server_mode_combo.addItems(
            ["stdio (Claude Code 集成)", "HTTP (独立服务器)"]
        )
        self.server_mode_combo.setCurrentIndex(0)

        server_mode_layout.addWidget(server_mode_label)
        server_mode_layout.addWidget(self.server_mode_combo)
        server_mode_layout.addWidget(QLabel("推荐用于 Claude Code 集成"))

        conn_layout.addWidget(self.show_window_cb)
        conn_layout.addWidget(self.control_cb)
        conn_layout.addWidget(self.audio_cb)
        conn_layout.addWidget(self.tcpip_cb)
        conn_layout.addWidget(self.stay_awake_cb)
        conn_layout.addLayout(server_mode_layout)
        conn_group.setLayout(conn_layout)

        # Server settings
        server_group = QGroupBox("服务器设置")
        server_layout = QVBoxLayout()

        # Port (for HTTP mode)
        port_layout = QHBoxLayout()
        port_label = QLabel("HTTP 端口:")
        self.port_input = QLineEdit("3359")
        port_layout.addWidget(port_label)
        port_layout.addWidget(self.port_input)
        port_layout.addWidget(QLabel("(仅 HTTP 模式)"))

        # Log file
        log_layout = QHBoxLayout()
        log_label = QLabel("日志文件:")
        # Generate log filename with timestamp
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        log_filename = (
            log_dir / f"mcp_gui_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        self.log_input = QLineEdit(str(log_filename))
        log_layout.addWidget(log_label)
        log_layout.addWidget(self.log_input)
        log_layout.addWidget(QLabel("(默认)"))

        server_layout.addLayout(port_layout)
        server_layout.addLayout(log_layout)
        server_group.setLayout(server_layout)

        # Action buttons
        action_layout = QHBoxLayout()
        self.start_btn = QPushButton("启动 MCP 服务器")
        self.stop_btn = QPushButton("停止服务器")
        self.test_btn = QPushButton("测试连接")

        self.start_btn.clicked.connect(self.start_server)
        self.stop_btn.clicked.connect(self.stop_server)
        self.test_btn.clicked.connect(self.test_connection)

        # Initial state
        self.stop_btn.setEnabled(False)

        action_layout.addWidget(self.start_btn)
        action_layout.addWidget(self.stop_btn)
        action_layout.addWidget(self.test_btn)
        action_layout.addStretch()

        # Add to main layout
        layout.addWidget(conn_group)
        layout.addWidget(server_group)
        layout.addLayout(action_layout)
        self.setLayout(layout)

    def get_config(self) -> dict:
        """Get current configuration"""
        return {
            "show_window": self.show_window_cb.isChecked(),
            "control": self.control_cb.isChecked(),
            "audio": self.audio_cb.isChecked(),
            "tcpip": self.tcpip_cb.isChecked(),
            "stay_awake": self.stay_awake_cb.isChecked(),
            "server_mode": "stdio"
            if self.server_mode_combo.currentIndex() == 0
            else "http",
            "port": int(self.port_input.text()),
            "log_file": self.log_input.text() if self.log_input.text() else None,
        }

    def start_server(self):
        """Start MCP server with current config"""
        config = self.get_config()
        if self.mcp_manager.start(config):
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)

    def stop_server(self):
        """Stop MCP server"""
        self.mcp_manager.stop()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def test_connection(self):
        """Test device connection"""
        config = self.get_config()
        # Create ClientConfig and test connection
        client_config = ClientConfig(
            max_fps=60,  # 高帧率
            bitrate=8000000,  # 8 Mbps 高码率
            show_window=config["show_window"],
            control=config["control"],
            audio=config["audio"],
            tcpip=config["tcpip"],
            stay_awake=config["stay_awake"],
        )
        self.device_manager.connect_async(client_config)

    def on_log_received(self, message: str):
        """Handle log message from MCP server"""
        # Forward to parent window's log
        if self.parent():
            parent = self.window()
            if hasattr(parent, "log"):
                parent.log(f"[MCP] {message}")

    def on_error(self, error: str):
        """Handle error from MCP server"""
        if self.parent():
            parent = self.window()
            if hasattr(parent, "log"):
                parent.log(f"[MCP Error] {error}")


class ScrcpyMCPMainWindow(QMainWindow):
    """Main window for Scrcpy MCP Server GUI with video and audio controls"""

    def __init__(self):
        super().__init__()

        # Create independent video window (separate window, can be resized/moved)
        self.video_window = VideoWindow()
        self.video_window.hide()  # Hidden until connected

        # Timer for video updates
        self._video_timer = QTimer(self)
        self._video_timer.timeout.connect(self._update_video)
        self._video_timer.start(16)  # ~60 FPS

        # Managers
        self.mcp_manager = MCPServerManager()
        self.streaming_client = StreamingClientManager(self.video_window)
        self.device_manager = ScrcpyDeviceManager()  # For non-streaming operations

        self.setup_ui()
        self.setup_status_bar()

        # Connect MCP server signals
        self.mcp_manager.log_received.connect(self.log)
        self.mcp_manager.error_occurred.connect(self.on_error)
        self.mcp_manager.server_started.connect(self.on_server_started)
        self.mcp_manager.server_stopped.connect(self.on_server_stopped)

        # Connect streaming client signals
        self.streaming_client.connected.connect(self.on_streaming_connected)
        self.streaming_client.disconnected.connect(self.on_disconnected)
        self.streaming_client.error_occurred.connect(self.on_error)

        # Connect device manager signals
        self.device_manager.connected.connect(self.on_connected)
        self.device_manager.disconnected.connect(self.on_disconnected)
        self.device_manager.error_occurred.connect(self.on_error)

    def _update_video(self):
        """Update video window (called by QTimer)"""
        # Use client's video window (not GUI's own)
        client = self.streaming_client.client
        if client and hasattr(client, "_video_window") and client._video_window:
            video_window = client._video_window
            if video_window.isVisible():
                # CRITICAL: Trigger frame update by calling VideoWidget's update_frame
                # This marks _has_new_frame=True, causing paintEvent to consume from DelayBuffer
                video_widget = video_window._video_widget
                video_widget.update_frame(
                    None
                )  # Indicate new frame available in DelayBuffer

    def setup_ui(self):
        """Setup main UI with controls"""
        self.setWindowTitle("Scrcpy MCP Server 配置工具")
        self.setMinimumSize(900, 500)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)

        # Left panel - Configuration
        config_widget = ScrcpyMCPConfigDialog(self.mcp_manager, self.device_manager)
        config_widget.setMaximumWidth(350)
        main_layout.addWidget(config_widget)

        # Right panel - Controls and logs
        right_panel = QWidget()
        right_layout = QVBoxLayout()

        # Status row
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("<b>MCP 服务器:</b>"))
        self.mcp_status_label = QLabel("未启动")
        status_row.addWidget(self.mcp_status_label)
        status_row.addWidget(QLabel("  PID:"))
        self.mcp_pid_label = QLabel("")
        status_row.addWidget(self.mcp_pid_label)
        status_row.addStretch()
        status_row.addWidget(QLabel("<b>设备:</b>"))
        self.status_label = QLabel("未连接")
        status_row.addWidget(self.status_label)
        right_layout.addLayout(status_row)

        # Video/Audio controls
        media_controls = QHBoxLayout()
        self.video_toggle = QPushButton("显示画面")
        self.video_toggle.setCheckable(True)
        self.video_toggle.setChecked(True)
        self.video_toggle.clicked.connect(self.toggle_video)

        self.audio_toggle = QPushButton("启用音频")
        self.audio_toggle.setCheckable(True)
        self.audio_toggle.setChecked(True)
        self.audio_toggle.clicked.connect(self.toggle_audio)

        self.connect_stream_btn = QPushButton("连接 (画面+音频)")
        self.connect_stream_btn.clicked.connect(self.connect_streaming)

        media_controls.addWidget(self.video_toggle)
        media_controls.addWidget(self.audio_toggle)
        media_controls.addWidget(self.connect_stream_btn)
        media_controls.addStretch()
        right_layout.addLayout(media_controls)

        # Device info
        self.device_info_label = QLabel("")
        self.device_info_label.setStyleSheet(
            "QLabel { background-color: #f0f0f0; padding: 5px; }"
        )
        self.device_info_label.setWordWrap(True)
        right_layout.addWidget(self.device_info_label)

        # Quick actions
        actions_layout = QHBoxLayout()
        action_buttons = [
            ("断开连接", self.disconnect_device),
            ("获取状态", self.get_device_state),
            ("截图", self.take_screenshot),
            ("列出应用", self.list_apps),
        ]
        for text, callback in action_buttons:
            btn = QPushButton(text)
            btn.clicked.connect(callback)
            actions_layout.addWidget(btn)
        actions_layout.addStretch()
        right_layout.addLayout(actions_layout)

        # Log output
        log_group = QGroupBox("日志输出")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)

        log_controls = QHBoxLayout()
        clear_btn = QPushButton("清空日志")
        clear_btn.clicked.connect(self.log_text.clear)
        log_controls.addWidget(clear_btn)
        log_controls.addStretch()
        log_layout.addLayout(log_controls)
        log_group.setLayout(log_layout)
        right_layout.addWidget(log_group)

        right_panel.setLayout(right_layout)
        main_layout.addWidget(right_panel, 2)

    def setup_status_bar(self):
        """Setup status bar"""
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("Ready")

    def toggle_video(self):
        """Toggle video display"""
        client = self.streaming_client.client
        video_window = (
            client._video_window
            if (client and hasattr(client, "_video_window"))
            else self.video_window
        )

        if self.video_toggle.isChecked():
            video_window.show()
            self.video_toggle.setText("隐藏画面")
            if self.streaming_client.is_connected():
                self.streaming_client.toggle_video(True)
        else:
            video_window.hide()
            self.video_toggle.setText("显示画面")
            if self.streaming_client.is_connected():
                self.streaming_client.toggle_video(False)

    def toggle_audio(self):
        """Toggle audio playback"""
        if self.audio_toggle.isChecked():
            self.audio_toggle.setText("禁用音频")
            self.streaming_client.toggle_audio(True)
            self.log("音频已启用")
        else:
            self.audio_toggle.setText("启用音频")
            self.streaming_client.toggle_audio(False)
            self.log("音频已禁用")

    def connect_streaming(self):
        """Connect with video and audio streaming"""
        config = {
            "show_window": False,  # We use our own VideoWindow
            "control": True,
            "audio": self.audio_toggle.isChecked(),
            "tcpip": True,
            "stay_awake": True,
        }

        client_config = ClientConfig(
            max_fps=60,  # 高帧率保证流畅度
            bitrate=8000000,  # 8 Mbps 高码率保证画质
            show_window=True,  # ✅ 让 client 创建自己的 window（保证回调链完整）
            control=config["control"],
            audio=config["audio"],
            tcpip=config["tcpip"],
            stay_awake=config["stay_awake"],
        )

        self.log("正在连接设备 (带画面和音频)...")
        # Use synchronous connection for audio to work properly
        # (Qt audio player needs event loop in main thread)
        try:
            client = ScrcpyClient(client_config)
            success = client.connect()
            if success:
                self.streaming_client.client = client

                # Use client's video window (not our own) - ensures proper callback chain
                if (
                    hasattr(client, "_video_window")
                    and client._video_window is not None
                ):
                    # Replace GUI's video_window with client's video_window
                    self.video_window = client._video_window

                self.streaming_client.connected.emit(True)

                if self.video_toggle.isChecked():
                    self.video_window.show()
            else:
                self.streaming_client.error_occurred.emit("Connection failed")
        except Exception as e:
            self.streaming_client.error_occurred.emit(str(e))

    def on_streaming_connected(self):
        """Handle streaming connection"""
        self.status_label.setText("已连接 (流媒体) ✓")
        self.log("✓ 设备已连接 (画面+音频)")

        # Update device info and video window
        if self.streaming_client.client:
            client = self.streaming_client.client
            info = (
                f"设备: {client.state.device_name} | "
                f"分辨率: {client.state.device_size[0]}x{client.state.device_size[1]} | "
                f"编解码器: {hex(client.state.codec_id)}"
            )
            self.device_info_label.setText(info)

            # Use client's video window (not GUI's own)
            video_window = (
                client._video_window
                if hasattr(client, "_video_window")
                else self.video_window
            )

            # Update video window with device info
            video_window.set_device_info(
                client.state.device_name,
                client.state.device_size[0],
                client.state.device_size[1],
            )

    def connect_device(self):
        """Connect to device (simple mode, no video/audio)"""
        self.log("正在连接设备...")
        # Use default config for quick connect
        client_config = ClientConfig(
            max_fps=60,  # 高帧率
            bitrate=8000000,  # 8 Mbps 高码率
            show_window=False,
            control=True,
            audio=False,
            tcpip=True,
            stay_awake=True,
        )
        self.device_manager.connect_async(client_config)

    def disconnect_device(self):
        """Disconnect from device"""
        self.log("正在断开连接...")
        # Disconnect both managers
        self.streaming_client.disconnect_now()
        self.device_manager.disconnect_now()
        self.status_label.setText("未连接")
        self.device_info_label.setText("")
        # Use client's video window for hiding
        client = self.streaming_client.client
        video_window = (
            client._video_window
            if (client and hasattr(client, "_video_window"))
            else self.video_window
        )
        video_window.hide()

    def get_device_state(self):
        """Get and display device state"""
        # Check streaming client first, then device manager
        client = None
        if self.streaming_client.is_connected():
            client = self.streaming_client.client
        elif self.device_manager.is_connected():
            client = self.device_manager.client

        if not client:
            self.log("错误: 未连接")
            return

        state = {
            "name": client.state.device_name,
            "size": f"{client.state.device_size[0]}x{client.state.device_size[1]}",
            "codec": hex(client.state.codec_id),
            "connected": client.state.connected,
            "tcpip": client.state.tcpip_connected,
        }
        self.device_info_label.setText(
            f"设备: {state['name']} | "
            f"分辨率: {state['size']} | "
            f"编解码器: {state['codec']} | "
            f"TCP/IP: {state['tcpip']}"
        )

    def take_screenshot(self):
        """Take screenshot"""
        # Check streaming client first, then device manager
        client = None
        if self.streaming_client.is_connected():
            client = self.streaming_client.client
        elif self.device_manager.is_connected():
            client = self.device_manager.client

        if not client:
            self.log("错误: 未连接")
            return

        import time

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}.png"

        self.log(f"正在截图到 {filename}...")
        client.screenshot(filename)
        self.log(f"截图已保存: {filename}")

    def list_apps(self):
        """List installed apps"""
        # Check streaming client first, then device manager
        client = None
        if self.streaming_client.is_connected():
            client = self.streaming_client.client
        elif self.device_manager.is_connected():
            client = self.device_manager.client

        if not client:
            self.log("错误: 未连接")
            return

        self.log("正在获取应用列表...")
        apps = client.list_apps()
        user_apps = [app for app in apps if not app["system"]]

        self.log(f"找到 {len(user_apps)} 个用户应用:")
        for i, app in enumerate(user_apps[:10], 1):
            self.log(f"  {i}. {app['name']} ({app['package']})")

    def on_connected(self):
        """Handle successful connection (simple mode)"""
        self.status_label.setText("已连接 ✓")
        self.log("✓ 设备已连接")

    def on_disconnected(self):
        """Handle disconnection"""
        self.status_label.setText("未连接")
        self.device_info_label.setText("")
        # Use client's video window for hiding
        client = self.streaming_client.client
        video_window = (
            client._video_window
            if (client and hasattr(client, "_video_window"))
            else self.video_window
        )
        video_window.hide()
        self.log("⚠ 设备已断开")

    def on_error(self, error: str):
        """Handle error"""
        self.status_label.setText(f"错误: {error}")
        self.log(f"✗ 错误: {error}")

    def on_server_started(self):
        """Handle MCP server started"""
        self.mcp_status_label.setText("运行中 ✓")
        if self.mcp_manager.process:
            self.mcp_pid_label.setText(f"PID: {self.mcp_manager.process.pid}")
        self.log("✓ MCP 服务器已启动")

    def on_server_stopped(self):
        """Handle MCP server stopped"""
        self.mcp_status_label.setText("未启动")
        self.mcp_pid_label.setText("")
        self.log("⚠ MCP 服务器已停止")

    def log(self, message: str):
        """Add message to log"""
        self.log_text.append(message + "\n")
        self.log_text.moveCursor(QTextCursor.End)
        self.statusBar.showMessage(message, 3000)


def main():
    """Main entry point"""
    app = QApplication(sys.argv)
    window = ScrcpyMCPMainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
