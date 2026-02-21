"""
Connection configuration panel for scrcpy-py-ddlx GUI.
"""

import logging
from typing import Optional, TYPE_CHECKING

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QRadioButton, QCheckBox, QLineEdit,
    QPushButton, QLabel, QSpinBox, QMessageBox
)
from PySide6.QtCore import Qt, Signal

if TYPE_CHECKING:
    from scrcpy_py_ddlx.gui.config_manager import DeviceConfig

logger = logging.getLogger(__name__)


class ConnectionPanel(QWidget):
    """Panel for connection configuration."""

    # Signals
    config_changed = Signal()
    discover_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        """Setup UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Connection mode group
        mode_group = QGroupBox("连接模式")
        mode_layout = QVBoxLayout(mode_group)

        # ADB Tunnel mode
        self._adb_radio = QRadioButton("ADB 隧道 (USB/WiFi 通过 ADB)")
        self._adb_radio.setChecked(True)
        self._adb_radio.toggled.connect(self._on_mode_changed)
        mode_layout.addWidget(self._adb_radio)

        # Network mode
        self._network_radio = QRadioButton("网络直连 (TCP 控制 + UDP 媒体)")
        self._network_radio.toggled.connect(self._on_mode_changed)
        mode_layout.addWidget(self._network_radio)

        layout.addWidget(mode_group)

        # Network settings group
        self._network_group = QGroupBox("网络设置")
        network_layout = QGridLayout(self._network_group)

        # Device IP
        network_layout.addWidget(QLabel("设备 IP:"), 0, 0)
        self._ip_edit = QLineEdit()
        self._ip_edit.setPlaceholderText("例如: 192.168.1.100")
        self._ip_edit.textChanged.connect(self._on_config_changed)
        network_layout.addWidget(self._ip_edit, 0, 1)

        # Discover button
        self._discover_btn = QPushButton("发现设备")
        self._discover_btn.setMaximumWidth(100)
        self._discover_btn.clicked.connect(self._on_discover)
        network_layout.addWidget(self._discover_btn, 0, 2)

        # Control port
        network_layout.addWidget(QLabel("控制端口:"), 1, 0)
        self._control_port_spin = QSpinBox()
        self._control_port_spin.setRange(1, 65535)
        self._control_port_spin.setValue(27184)
        self._control_port_spin.valueChanged.connect(self._on_config_changed)
        network_layout.addWidget(self._control_port_spin, 1, 1)

        # Video port
        network_layout.addWidget(QLabel("视频端口:"), 2, 0)
        self._video_port_spin = QSpinBox()
        self._video_port_spin.setRange(1, 65535)
        self._video_port_spin.setValue(27185)
        self._video_port_spin.valueChanged.connect(self._on_config_changed)
        network_layout.addWidget(self._video_port_spin, 2, 1)

        # Audio port
        network_layout.addWidget(QLabel("音频端口:"), 3, 0)
        self._audio_port_spin = QSpinBox()
        self._audio_port_spin.setRange(1, 65535)
        self._audio_port_spin.setValue(27186)
        self._audio_port_spin.valueChanged.connect(self._on_config_changed)
        network_layout.addWidget(self._audio_port_spin, 3, 1)

        layout.addWidget(self._network_group)

        # Stay-alive option
        self._stay_alive_check = QCheckBox("Stay-Alive 模式 (热连接，自动重连)")
        self._stay_alive_check.setToolTip(
            "断开连接后服务端继续运行，支持快速重连。"
            "适用于网络模式下频繁断开/重连的场景。"
        )
        self._stay_alive_check.toggled.connect(self._on_config_changed)
        layout.addWidget(self._stay_alive_check)

        # Server lifecycle options
        lifecycle_group = QGroupBox("服务端生命周期 (网络模式)")
        lifecycle_layout = QVBoxLayout(lifecycle_group)

        self._push_server_check = QCheckBox("连接时推送服务端 APK")
        self._push_server_check.setChecked(True)
        self._push_server_check.toggled.connect(self._on_config_changed)
        lifecycle_layout.addWidget(self._push_server_check)

        self._reuse_server_check = QCheckBox("复用现有服务端")
        self._reuse_server_check.setToolTip("如果服务端已在运行，复用它而不是重启")
        self._reuse_server_check.toggled.connect(self._on_config_changed)
        lifecycle_layout.addWidget(self._reuse_server_check)

        layout.addWidget(lifecycle_group)

        # Add stretch to push everything up
        layout.addStretch()

        # Initial state
        self._update_ui_state()

    def _on_mode_changed(self):
        """Handle connection mode change."""
        self._update_ui_state()
        self._on_config_changed()

    def _update_ui_state(self):
        """Update UI state based on connection mode."""
        is_network = self._network_radio.isChecked()
        self._network_group.setEnabled(is_network)
        self._stay_alive_check.setEnabled(is_network)

    def _on_config_changed(self):
        """Handle configuration change."""
        self.config_changed.emit()

    def _on_discover(self):
        """Handle discover button click."""
        self.discover_requested.emit()

    def load_config(self, config: "DeviceConfig"):
        """Load configuration into UI."""
        # Block signals during load
        self.blockSignals(True)

        # Connection mode
        if config.connection_mode == "network":
            self._network_radio.setChecked(True)
        else:
            self._adb_radio.setChecked(True)

        # Network settings
        self._ip_edit.setText(config.host)
        self._control_port_spin.setValue(config.control_port)
        self._video_port_spin.setValue(config.video_port)
        self._audio_port_spin.setValue(config.audio_port)

        # Stay-alive
        self._stay_alive_check.setChecked(config.stay_alive)

        # Server lifecycle
        self._push_server_check.setChecked(config.push_server)
        self._reuse_server_check.setChecked(config.reuse_server)

        # Unblock signals
        self.blockSignals(False)

        # Update UI state
        self._update_ui_state()

    def save_config(self, config: "DeviceConfig"):
        """Save UI state to configuration."""
        # Connection mode
        config.connection_mode = "network" if self._network_radio.isChecked() else "adb_tunnel"

        # Network settings
        config.host = self._ip_edit.text().strip()
        config.control_port = self._control_port_spin.value()
        config.video_port = self._video_port_spin.value()
        config.audio_port = self._audio_port_spin.value()

        # Stay-alive
        config.stay_alive = self._stay_alive_check.isChecked()

        # Server lifecycle
        config.push_server = self._push_server_check.isChecked()
        config.reuse_server = self._reuse_server_check.isChecked()

    def get_device_ip(self) -> str:
        """Get device IP address."""
        return self._ip_edit.text().strip()

    def set_device_ip(self, ip: str):
        """Set device IP address."""
        self._ip_edit.setText(ip)

    def is_network_mode(self) -> bool:
        """Check if network mode is selected."""
        return self._network_radio.isChecked()
