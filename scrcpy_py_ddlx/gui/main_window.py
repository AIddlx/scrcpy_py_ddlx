"""
Main window for scrcpy-py-ddlx GUI Control Console.
"""

import sys
import logging
import threading
import time
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QMenuBar, QMenu, QToolBar, QStatusBar,
    QPushButton, QMessageBox, QApplication, QSplitter
)
from PySide6.QtCore import Qt, Slot, QTimer, QThread, Signal
from PySide6.QtGui import QAction, QIcon

from scrcpy_py_ddlx.gui.panels.connection_panel import ConnectionPanel
from scrcpy_py_ddlx.gui.panels.media_panel import MediaPanel
from scrcpy_py_ddlx.gui.panels.device_panel import DevicePanel
from scrcpy_py_ddlx.gui.panels.log_panel import LogPanel
from scrcpy_py_ddlx.gui.preview_window import PreviewWindow
from scrcpy_py_ddlx.gui.config_manager import ConfigManager, DeviceConfig, get_config_manager
from scrcpy_py_ddlx.gui.mcp_manager import MCPManager, MCPServerStatus, get_mcp_manager
from scrcpy_py_ddlx.client import ScrcpyClient, ClientConfig
from scrcpy_py_ddlx.client.config import ConnectionMode

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Main window for scrcpy-py-ddlx control console."""

    def __init__(self):
        super().__init__()

        # Initialize managers
        self._config_manager = get_config_manager()
        self._mcp_manager = get_mcp_manager(
            on_status_change=self._on_mcp_status_changed
        )

        # Client state
        self._client: Optional[ScrcpyClient] = None
        self._preview_window: Optional[PreviewWindow] = None

        # Setup UI
        self._setup_ui()
        self._setup_menu()
        self._setup_toolbar()
        self._setup_statusbar()
        self._setup_connections()

        # Load initial config
        self._load_config_list()

        # Window setup
        self.setWindowTitle("scrcpy-py-ddlx 控制台")
        self.resize(900, 700)

    def _setup_ui(self):
        """Setup UI components."""
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)

        # Main layout
        main_layout = QHBoxLayout(central)

        # Left side: Tabs
        self._tab_widget = QTabWidget()

        # Create panels
        self._connection_panel = ConnectionPanel()
        self._media_panel = MediaPanel()
        self._device_panel = DevicePanel()
        self._log_panel = LogPanel()

        # Add panels to tabs
        self._tab_widget.addTab(self._device_panel, "设备")
        self._tab_widget.addTab(self._connection_panel, "连接")
        self._tab_widget.addTab(self._media_panel, "媒体")
        self._tab_widget.addTab(self._log_panel, "日志")

        main_layout.addWidget(self._tab_widget, stretch=2)

        # Right side: MCP status and controls
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(10, 10, 10, 10)

        # MCP status group
        from PySide6.QtWidgets import QGroupBox, QLabel
        mcp_group = QGroupBox("MCP 服务器")
        mcp_layout = QVBoxLayout(mcp_group)

        self._mcp_status_label = QLabel("状态: 已停止")
        mcp_layout.addWidget(self._mcp_status_label)

        self._mcp_url_label = QLabel("地址: -")
        mcp_layout.addWidget(self._mcp_url_label)

        mcp_btn_layout = QHBoxLayout()
        self._mcp_start_btn = QPushButton("启动 MCP")
        self._mcp_start_btn.clicked.connect(self._on_start_mcp)
        mcp_btn_layout.addWidget(self._mcp_start_btn)

        self._mcp_stop_btn = QPushButton("停止 MCP")
        self._mcp_stop_btn.setEnabled(False)
        self._mcp_stop_btn.clicked.connect(self._on_stop_mcp)
        mcp_btn_layout.addWidget(self._mcp_stop_btn)

        mcp_layout.addLayout(mcp_btn_layout)
        right_layout.addWidget(mcp_group)

        # Connection buttons
        conn_group = QGroupBox("连接")
        conn_layout = QVBoxLayout(conn_group)

        self._connect_btn = QPushButton("连接")
        self._connect_btn.clicked.connect(self._on_connect)
        conn_layout.addWidget(self._connect_btn)

        self._disconnect_btn = QPushButton("断开")
        self._disconnect_btn.setEnabled(False)
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        conn_layout.addWidget(self._disconnect_btn)

        right_layout.addWidget(conn_group)
        right_layout.addStretch()

        main_layout.addWidget(right_widget, stretch=1)

    def _setup_menu(self):
        """Setup menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("文件(&F)")

        new_config_action = QAction("新建配置(&N)", self)
        new_config_action.triggered.connect(self._device_panel._on_new_config)
        file_menu.addAction(new_config_action)

        save_config_action = QAction("保存配置(&S)", self)
        save_config_action.triggered.connect(self._device_panel._on_save_config)
        file_menu.addAction(save_config_action)

        file_menu.addSeparator()

        exit_action = QAction("退出(&X)", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Device menu
        device_menu = menubar.addMenu("设备(&D)")

        discover_action = QAction("发现设备(&D)", self)
        discover_action.triggered.connect(self._on_discover_devices)
        device_menu.addAction(discover_action)

        query_action = QAction("查询服务端状态(&Q)", self)
        query_action.triggered.connect(self._device_panel._on_query_server)
        device_menu.addAction(query_action)

        terminate_action = QAction("终止服务端(&T)", self)
        terminate_action.triggered.connect(self._device_panel._on_terminate_server)
        device_menu.addAction(terminate_action)

        # View menu
        view_menu = menubar.addMenu("视图(&V)")

        preview_action = QAction("显示预览窗口(&P)", self)
        preview_action.triggered.connect(self._show_preview)
        view_menu.addAction(preview_action)

        # Help menu
        help_menu = menubar.addMenu("帮助(&H)")

        about_action = QAction("关于(&A)", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _setup_toolbar(self):
        """Setup toolbar."""
        toolbar = QToolBar("主工具栏")
        self.addToolBar(toolbar)

        toolbar.addAction("连接", self._on_connect)
        toolbar.addAction("断开", self._on_disconnect)
        toolbar.addSeparator()
        toolbar.addAction("预览", self._show_preview)

    def _setup_statusbar(self):
        """Setup status bar."""
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("就绪")

    def _setup_connections(self):
        """Setup signal/slot connections."""
        # Connection panel
        self._connection_panel.config_changed.connect(self._on_config_changed)
        self._connection_panel.discover_requested.connect(self._on_discover_devices)

        # Media panel
        self._media_panel.config_changed.connect(self._on_config_changed)

        # Device panel
        self._device_panel.config_selected.connect(self._on_config_selected)
        self._device_panel.config_created.connect(self._on_config_created)
        self._device_panel.config_deleted.connect(self._on_config_deleted)
        self._device_panel.config_saved.connect(self._on_config_saved)
        self._device_panel.query_server_requested.connect(self._on_query_server)
        self._device_panel.terminate_server_requested.connect(self._on_terminate_server)
        self._device_panel.discover_devices_requested.connect(self._on_discover_devices)
        self._device_panel.device_selected.connect(self._on_device_selected)
        self._device_panel._show_preview_btn.clicked.connect(self._show_preview)
        self._device_panel._hide_preview_btn.clicked.connect(self._hide_preview)

    def _load_config_list(self):
        """Load configuration list."""
        names = self._config_manager.get_config_names()
        current = names[0] if names else ""
        self._device_panel.set_configs(names, current)
        if current:
            self._on_config_selected(current)

    def _on_config_selected(self, name: str):
        """Handle config selection."""
        config = self._config_manager.get_config(name)
        if config:
            self._connection_panel.load_config(config)
            self._media_panel.load_config(config)
            logger.info(f"Loaded config: {name}")

    def _on_config_created(self, name: str):
        """Handle config creation."""
        config = self._config_manager.create_config(name)
        self._load_config_list()
        self._device_panel.set_configs(
            self._config_manager.get_config_names(), name
        )
        logger.info(f"Created config: {name}")

    def _on_config_deleted(self, name: str):
        """Handle config deletion."""
        self._config_manager.delete_config(name)
        self._load_config_list()
        logger.info(f"Deleted config: {name}")

    def _on_config_saved(self, name: str):
        """Handle config save."""
        config = self._config_manager.get_config(name)
        if config:
            self._connection_panel.save_config(config)
            self._media_panel.save_config(config)
            self._config_manager.save_config(name)
            logger.info(f"Saved config: {name}")

    def _on_config_changed(self):
        """Handle config change in UI."""
        pass  # Changes are saved when connecting or explicit save

    def _on_device_selected(self, device_name: str, device_ip: str):
        """Handle device selection from discovery list - auto-fill IP and switch to network mode."""
        # 切换到连接设置标签页
        self._tab_widget.setCurrentWidget(self._connection_panel)

        # 设置为网络模式
        self._connection_panel._network_radio.setChecked(True)

        # 填充 IP 地址
        self._connection_panel.set_device_ip(device_ip)

        self._statusbar.showMessage(f"已选择设备: {device_name} ({device_ip})")

    def _on_connect(self):
        """Handle connect button."""
        if self._client and self._client.is_connected:
            QMessageBox.information(self, "提示", "已连接")
            return

        # Get current config
        config_name = self._device_panel.get_current_config()
        config = self._config_manager.get_config(config_name)
        if not config:
            QMessageBox.warning(self, "错误", "未选择配置")
            return

        # Save current UI state to config
        self._connection_panel.save_config(config)
        self._media_panel.save_config(config)

        # Create client config
        client_config = self._create_client_config(config)

        # Connect in background thread
        self._statusbar.showMessage("正在连接...")
        self._connect_btn.setEnabled(False)

        def do_connect():
            try:
                self._client = ScrcpyClient(client_config)
                self._client.connect()

                # Update UI on success
                QTimer.singleShot(0, self._on_connect_success)

            except Exception as e:
                logger.exception("Connection failed")
                QTimer.singleShot(0, lambda: self._on_connect_failed(str(e)))

        threading.Thread(target=do_connect, daemon=True).start()

    def _create_client_config(self, device_config: DeviceConfig) -> ClientConfig:
        """Create ClientConfig from DeviceConfig."""
        config = ClientConfig(
            # Connection
            connection_mode=ConnectionMode.NETWORK if device_config.connection_mode == "network" else ConnectionMode.ADB_TUNNEL,
            host=device_config.host,
            control_port=device_config.control_port,
            video_port=device_config.video_port,
            audio_port=device_config.audio_port,

            # Video
            video=device_config.video_enabled,
            codec=device_config.video_codec,
            bitrate=device_config.video_bitrate,
            max_fps=device_config.max_fps,
            bitrate_mode=device_config.bitrate_mode,
            i_frame_interval=device_config.i_frame_interval,

            # Audio
            audio=device_config.audio_enabled,
            audio_codec=device_config.audio_codec,

            # FEC
            video_fec_enabled=device_config.video_fec_enabled,
            audio_fec_enabled=device_config.audio_fec_enabled,
            fec_group_size=device_config.fec_group_size,
            fec_parity_count=device_config.fec_parity_count,

            # Window
            show_window=False,  # We handle preview ourselves
            control=True,
        )

        return config

    def _on_connect_success(self):
        """Handle successful connection."""
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(True)

        # Update device panel
        if self._client:
            device_name = self._client.state.device_name
            device_size = self._client.state.device_size
            mode = "Network" if self._connection_panel.is_network_mode() else "ADB Tunnel"
            ip = self._connection_panel.get_device_ip()

            self._device_panel.set_server_status(
                connected=True,
                device_name=device_name,
                mode=mode,
                ip=ip
            )

            # Setup preview window
            self._setup_preview_window()

            # Start frame consumer
            self._start_frame_consumer()

            self._statusbar.showMessage(f"已连接到 {device_name}")

    def _on_connect_failed(self, error: str):
        """Handle failed connection."""
        self._connect_btn.setEnabled(True)
        self._statusbar.showMessage(f"连接失败: {error}")
        QMessageBox.critical(self, "连接错误", f"连接失败:\n{error}")

    def _setup_preview_window(self):
        """Setup preview window with client."""
        if not self._client:
            return

        # Create preview window
        self._preview_window = PreviewWindow()

        # Set device info
        device_name = self._client.state.device_name
        device_size = self._client.state.device_size
        self._preview_window.set_device_info(
            device_name, device_size[0], device_size[1]
        )

        # Set control queue
        if self._client._control_queue:
            self._preview_window.set_control_queue(self._client._control_queue)

        # Set delay buffer for frame consumption
        if hasattr(self._client, '_video_decoder') and self._client._video_decoder:
            self._preview_window.set_delay_buffer(self._client._video_decoder.delay_buffer)

        # Auto-show preview
        self._preview_window.show()

    def _start_frame_consumer(self):
        """Start consuming frames for preview."""
        # The video decoder already runs in its own thread
        # We just need to ensure preview window is connected
        pass

    def _on_disconnect(self):
        """Handle disconnect button."""
        if self._client:
            try:
                self._client.disconnect()
            except Exception as e:
                logger.error(f"Disconnect error: {e}")
            finally:
                self._client = None

        # Hide preview
        self._hide_preview()

        # Update UI
        self._disconnect_btn.setEnabled(False)
        self._device_panel.set_server_status(connected=False)
        self._statusbar.showMessage("已断开")

    def _show_preview(self):
        """Show preview window."""
        if self._preview_window:
            self._preview_window.show()
            self._preview_window.raise_()
            self._preview_window.activateWindow()
        elif self._client and self._client.is_connected:
            self._setup_preview_window()

    def _hide_preview(self):
        """Hide preview window."""
        if self._preview_window:
            self._preview_window.hide()

    def _on_discover_devices(self):
        """Handle device discovery - scan ADB devices and UDP servers."""
        all_devices = []

        # 1. 扫描 ADB 设备
        try:
            from scrcpy_py_ddlx.core.adb import ADBManager
            adb = ADBManager()
            adb_devices = adb.list_devices(long_format=True)

            for d in adb_devices:
                if d.is_ready():
                    ip = None
                    try:
                        ip = adb.get_device_ip(d.serial, timeout=2.0)
                    except Exception:
                        pass

                    device_info = (f"{d.model or d.serial}", ip or d.serial)
                    all_devices.append(device_info)
                    logger.info(f"[ADB] 发现设备: {d.model or d.serial} ({ip or d.serial})")

        except Exception as e:
            logger.warning(f"[ADB] 扫描失败: {e}")

        # 2. UDP 广播发现 scrcpy 服务端（快速，约 2 秒）
        try:
            from scrcpy_py_ddlx.client.udp_wake import discover_devices as udp_discover
            logger.info("[UDP] 广播发现...")
            udp_devices = udp_discover(timeout=2.0)

            for dev in udp_devices:
                # 避免重复
                if not any(d[1] == dev['ip'] for d in all_devices):
                    all_devices.append((dev['name'], dev['ip']))
                    logger.info(f"[UDP] 发现服务端: {dev['name']} ({dev['ip']})")

        except Exception as e:
            logger.warning(f"[UDP] 发现失败: {e}")

        # 更新 UI
        self._device_panel.set_discovered_devices(all_devices)

    def _on_query_server(self):
        """Handle server status query."""
        # TODO: Implement UDP query
        self._statusbar.showMessage("查询功能尚未实现")

    def _on_terminate_server(self):
        """Handle server termination."""
        # TODO: Implement UDP terminate
        self._statusbar.showMessage("终止功能尚未实现")

    def _on_start_mcp(self):
        """Start MCP server."""
        if self._mcp_manager.start():
            self._mcp_start_btn.setEnabled(False)
            self._mcp_stop_btn.setEnabled(True)
            self._statusbar.showMessage("MCP 服务器已启动")
        else:
            error = self._mcp_manager.status.error or "未知错误"
            QMessageBox.critical(self, "MCP 错误", f"启动 MCP 服务器失败:\n{error}")

    def _on_stop_mcp(self):
        """Stop MCP server."""
        self._mcp_manager.stop()
        self._mcp_start_btn.setEnabled(True)
        self._mcp_stop_btn.setEnabled(False)
        self._statusbar.showMessage("MCP 服务器已停止")

    def _on_mcp_status_changed(self, status: MCPServerStatus):
        """Handle MCP status change."""
        if status.running:
            self._mcp_status_label.setText("状态: 运行中")
            self._mcp_url_label.setText(f"地址: {self._mcp_manager.get_url()}")
            self._mcp_start_btn.setEnabled(False)
            self._mcp_stop_btn.setEnabled(True)
        else:
            self._mcp_status_label.setText("状态: 已停止")
            if status.error:
                self._mcp_url_label.setText(f"错误: {status.error}")
            else:
                self._mcp_url_label.setText("地址: -")
            self._mcp_start_btn.setEnabled(True)
            self._mcp_stop_btn.setEnabled(False)

    def _on_about(self):
        """Show about dialog."""
        QMessageBox.about(
            self,
            "关于 scrcpy-py-ddlx",
            "scrcpy-py-ddlx 控制台\n\n"
            "用于管理 scrcpy 客户端连接的 GUI 应用程序，"
            "支持 MCP 服务器集成。\n\n"
            "版本: 1.0.0"
        )

    def closeEvent(self, event):
        """Handle window close."""
        # Disconnect client
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass

        # Stop MCP server
        if self._mcp_manager.is_running():
            self._mcp_manager.stop()

        # Cleanup log panel
        self._log_panel.cleanup()

        event.accept()
