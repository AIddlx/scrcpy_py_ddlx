"""
Device management panel for scrcpy-py-ddlx GUI.
"""

import logging
from typing import Optional, List, TYPE_CHECKING

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QComboBox, QPushButton, QLabel, QMessageBox,
    QInputDialog, QFrame, QListWidget, QListWidgetItem
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

if TYPE_CHECKING:
    from scrcpy_py_ddlx.gui.config_manager import DeviceConfig

logger = logging.getLogger(__name__)


class DevicePanel(QWidget):
    """Panel for device configuration management and server control."""

    # Signals
    config_selected = Signal(str)  # Config name
    config_created = Signal(str)  # Config name
    config_deleted = Signal(str)  # Config name
    config_saved = Signal(str)  # Config name
    query_server_requested = Signal()
    device_selected = Signal(str, str)  # device_name, device_ip
    terminate_server_requested = Signal()
    discover_devices_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        """Setup UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # === Device Configuration ===
        config_group = QGroupBox("设备配置")
        config_layout = QGridLayout(config_group)

        # Config dropdown
        config_layout.addWidget(QLabel("配置文件:"), 0, 0)
        self._config_combo = QComboBox()
        self._config_combo.currentTextChanged.connect(self._on_config_selected)
        config_layout.addWidget(self._config_combo, 0, 1)

        # Config buttons
        btn_layout = QHBoxLayout()

        self._new_btn = QPushButton("新建")
        self._new_btn.setMaximumWidth(60)
        self._new_btn.clicked.connect(self._on_new_config)
        btn_layout.addWidget(self._new_btn)

        self._delete_btn = QPushButton("删除")
        self._delete_btn.setMaximumWidth(60)
        self._delete_btn.clicked.connect(self._on_delete_config)
        btn_layout.addWidget(self._delete_btn)

        self._save_btn = QPushButton("保存")
        self._save_btn.setMaximumWidth(60)
        self._save_btn.clicked.connect(self._on_save_config)
        btn_layout.addWidget(self._save_btn)

        config_layout.addLayout(btn_layout, 0, 2)

        layout.addWidget(config_group)

        # === Server Status ===
        status_group = QGroupBox("服务端状态")
        status_layout = QGridLayout(status_group)

        # Status indicator
        status_layout.addWidget(QLabel("状态:"), 0, 0)
        self._status_indicator = QLabel("●")
        self._status_indicator.setStyleSheet("color: gray; font-size: 20px;")
        status_layout.addWidget(self._status_indicator, 0, 1)

        self._status_label = QLabel("未连接")
        status_layout.addWidget(self._status_label, 0, 2)

        # Device info
        status_layout.addWidget(QLabel("设备:"), 1, 0)
        self._device_label = QLabel("-")
        status_layout.addWidget(self._device_label, 1, 1, 1, 2)

        # Mode info
        status_layout.addWidget(QLabel("模式:"), 2, 0)
        self._mode_label = QLabel("-")
        status_layout.addWidget(self._mode_label, 2, 1, 1, 2)

        # IP info
        status_layout.addWidget(QLabel("IP:"), 3, 0)
        self._ip_label = QLabel("-")
        status_layout.addWidget(self._ip_label, 3, 1, 1, 2)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        status_layout.addWidget(separator, 4, 0, 1, 3)

        # Server control buttons
        ctrl_layout = QHBoxLayout()

        self._query_btn = QPushButton("查询状态")
        self._query_btn.clicked.connect(self._on_query_server)
        ctrl_layout.addWidget(self._query_btn)

        self._terminate_btn = QPushButton("终止服务端")
        self._terminate_btn.clicked.connect(self._on_terminate_server)
        ctrl_layout.addWidget(self._terminate_btn)

        status_layout.addLayout(ctrl_layout, 5, 0, 1, 3)

        layout.addWidget(status_group)

        # === Device Discovery ===
        discovery_group = QGroupBox("设备发现")
        discovery_layout = QVBoxLayout(discovery_group)

        discovery_desc = QLabel(
            "扫描 ADB 设备和 UDP 服务端 (约 2 秒)。"
        )
        discovery_desc.setStyleSheet("color: gray; font-size: 10px;")
        discovery_layout.addWidget(discovery_desc)

        self._discover_btn = QPushButton("发现设备")
        self._discover_btn.clicked.connect(self._on_discover_devices)
        discovery_layout.addWidget(self._discover_btn)

        # Discovered devices list (可点击选择)
        self._discovered_list = QListWidget()
        self._discovered_list.setMaximumHeight(100)
        self._discovered_list.itemDoubleClicked.connect(self._on_device_double_clicked)
        self._discovered_list.setToolTip("双击设备自动填充 IP")
        discovery_layout.addWidget(self._discovered_list)

        # 提示标签
        self._discovered_hint = QLabel("点击\"发现设备\"扫描")
        self._discovered_hint.setStyleSheet("color: gray; font-size: 10px;")
        discovery_layout.addWidget(self._discovered_hint)

        layout.addWidget(discovery_group)

        # === Preview Control ===
        preview_group = QGroupBox("预览控制")
        preview_layout = QHBoxLayout(preview_group)

        self._show_preview_btn = QPushButton("显示预览窗口")
        self._show_preview_btn.clicked.connect(self._on_show_preview)
        preview_layout.addWidget(self._show_preview_btn)

        self._hide_preview_btn = QPushButton("隐藏预览")
        self._hide_preview_btn.clicked.connect(self._on_hide_preview)
        preview_layout.addWidget(self._hide_preview_btn)

        layout.addWidget(preview_group)

        # Add stretch
        layout.addStretch()

    def _on_config_selected(self, name: str):
        """Handle config selection."""
        if name:
            self.config_selected.emit(name)

    def _on_new_config(self):
        """Handle new config button."""
        name, ok = QInputDialog.getText(
            self, "新建配置", "输入配置名称:"
        )
        if ok and name:
            self.config_created.emit(name)

    def _on_delete_config(self):
        """Handle delete config button."""
        current = self._config_combo.currentText()
        if current:
            reply = QMessageBox.question(
                self, "确认删除",
                f"确定删除配置 '{current}' 吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.config_deleted.emit(current)

    def _on_save_config(self):
        """Handle save config button."""
        current = self._config_combo.currentText()
        if current:
            self.config_saved.emit(current)

    def _on_query_server(self):
        """Handle query server button."""
        self.query_server_requested.emit()

    def _on_terminate_server(self):
        """Handle terminate server button."""
        reply = QMessageBox.question(
            self, "确认终止",
            "确定终止设备上的 scrcpy 服务端吗？\n"
            "这将断开所有客户端连接。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.terminate_server_requested.emit()

    def _on_discover_devices(self):
        """Handle discover devices button."""
        self._discovered_list.clear()
        self._discovered_list.addItem("正在扫描...")
        self._discover_btn.setEnabled(False)
        self.discover_devices_requested.emit()

    def _on_device_double_clicked(self, item: QListWidgetItem):
        """Handle device double click - emit device info for auto-fill."""
        # 解析设备信息: "name (ip)"
        text = item.text()
        if " (" in text and text.endswith(")"):
            name = text.split(" (")[0]
            ip = text.split(" (")[1].rstrip(")")
            self.device_selected.emit(name, ip)

    def _on_show_preview(self):
        """Handle show preview button."""
        pass  # Connected by main window

    def _on_hide_preview(self):
        """Handle hide preview button."""
        pass  # Connected by main window

    def set_configs(self, names: List[str], current: str = None):
        """Set available configurations."""
        self._config_combo.blockSignals(True)
        self._config_combo.clear()
        self._config_combo.addItems(names)
        if current and current in names:
            self._config_combo.setCurrentText(current)
        self._config_combo.blockSignals(False)

    def set_server_status(self, connected: bool, device_name: str = "",
                          mode: str = "", ip: str = ""):
        """Update server status display."""
        if connected:
            self._status_indicator.setStyleSheet("color: #00aa00; font-size: 20px;")
            self._status_label.setText("运行中")
            self._device_label.setText(device_name or "-")
            self._mode_label.setText(mode or "-")
            self._ip_label.setText(ip or "-")
        else:
            self._status_indicator.setStyleSheet("color: gray; font-size: 20px;")
            self._status_label.setText("未连接")
            self._device_label.setText("-")
            self._mode_label.setText("-")
            self._ip_label.setText("-")

    def set_discovered_devices(self, devices: List[tuple]):
        """Set discovered devices list."""
        self._discover_btn.setEnabled(True)
        self._discovered_list.clear()

        if devices:
            for name, ip in devices:
                item = QListWidgetItem(f"{name} ({ip})")
                item.setData(Qt.ItemDataRole.UserRole, (name, ip))
                self._discovered_list.addItem(item)
            self._discovered_hint.setText(f"发现 {len(devices)} 个设备，双击选择")
        else:
            self._discovered_list.addItem("未发现 ADB 设备")
            self._discovered_hint.setText(
                "请确保设备已通过 USB 或 WiFi 连接\n"
                "并已启用 USB 调试"
            )

    def get_current_config(self) -> str:
        """Get current config name."""
        return self._config_combo.currentText()
