"""
Media configuration panel for scrcpy-py-ddlx GUI.
"""

import logging
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox,
    QLabel, QFrame
)
from PySide6.QtCore import Signal

if TYPE_CHECKING:
    from scrcpy_py_ddlx.gui.config_manager import DeviceConfig

logger = logging.getLogger(__name__)


class MediaPanel(QWidget):
    """Panel for media (video/audio) configuration."""

    # Signals
    config_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        """Setup UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # === Video Settings ===
        video_group = QGroupBox("视频设置")
        video_layout = QGridLayout(video_group)

        # Video enabled
        self._video_check = QCheckBox("启用视频")
        self._video_check.setChecked(True)
        self._video_check.toggled.connect(self._on_video_toggled)
        video_layout.addWidget(self._video_check, 0, 0, 1, 3)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        video_layout.addWidget(separator, 1, 0, 1, 3)

        # Video codec
        video_layout.addWidget(QLabel("编码器:"), 2, 0)
        self._video_codec_combo = QComboBox()
        self._video_codec_combo.addItems(["自动 (推荐)", "H.264", "H.265 (HEVC)", "AV1"])
        self._video_codec_combo.setCurrentIndex(0)
        self._video_codec_combo.currentIndexChanged.connect(self._on_config_changed)
        video_layout.addWidget(self._video_codec_combo, 2, 1, 1, 2)

        # Video bitrate
        video_layout.addWidget(QLabel("码率:"), 3, 0)
        bitrate_layout = QHBoxLayout()
        self._video_bitrate_spin = QSpinBox()
        self._video_bitrate_spin.setRange(1, 100)
        self._video_bitrate_spin.setValue(4)
        self._video_bitrate_spin.setSuffix(" Mbps")
        self._video_bitrate_spin.valueChanged.connect(self._on_config_changed)
        bitrate_layout.addWidget(self._video_bitrate_spin)
        bitrate_layout.addStretch()
        video_layout.addLayout(bitrate_layout, 3, 1, 1, 2)

        # Max FPS
        video_layout.addWidget(QLabel("最大帧率:"), 4, 0)
        self._max_fps_combo = QComboBox()
        self._max_fps_combo.addItems(["30", "60", "90", "120"])
        self._max_fps_combo.setCurrentIndex(1)  # 60 fps
        self._max_fps_combo.currentIndexChanged.connect(self._on_config_changed)
        video_layout.addWidget(self._max_fps_combo, 4, 1, 1, 2)

        # Bitrate mode
        video_layout.addWidget(QLabel("码率模式:"), 5, 0)
        self._bitrate_mode_combo = QComboBox()
        self._bitrate_mode_combo.addItems(["VBR (可变)", "CBR (恒定)"])
        self._bitrate_mode_combo.setToolTip(
            "VBR: 可变码率 - 更好的画质，带宽波动\n"
            "CBR: 恒定码率 - 稳定的带宽，画质较低"
        )
        self._bitrate_mode_combo.currentIndexChanged.connect(self._on_config_changed)
        video_layout.addWidget(self._bitrate_mode_combo, 5, 1, 1, 2)

        # I-frame interval
        video_layout.addWidget(QLabel("关键帧间隔:"), 6, 0)
        iframe_layout = QHBoxLayout()
        self._iframe_spin = QDoubleSpinBox()
        self._iframe_spin.setRange(0.1, 60.0)
        self._iframe_spin.setValue(10.0)
        self._iframe_spin.setSingleStep(0.5)
        self._iframe_spin.setSuffix(" 秒")
        self._iframe_spin.setToolTip(
            "关键帧间隔。较低的值 = 丢包后画质恢复更快，但带宽占用更高。"
        )
        self._iframe_spin.valueChanged.connect(self._on_config_changed)
        iframe_layout.addWidget(self._iframe_spin)
        iframe_layout.addStretch()
        video_layout.addLayout(iframe_layout, 6, 1, 1, 2)

        layout.addWidget(video_group)

        # === Audio Settings ===
        audio_group = QGroupBox("音频设置")
        audio_layout = QGridLayout(audio_group)

        # Audio enabled
        self._audio_check = QCheckBox("启用音频")
        self._audio_check.setToolTip("启用设备音频流")
        self._audio_check.toggled.connect(self._on_audio_toggled)
        audio_layout.addWidget(self._audio_check, 0, 0, 1, 3)

        # Separator
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.Shape.HLine)
        separator2.setFrameShadow(QFrame.Shadow.Sunken)
        audio_layout.addWidget(separator2, 1, 0, 1, 3)

        # Audio codec
        audio_layout.addWidget(QLabel("编码器:"), 2, 0)
        self._audio_codec_combo = QComboBox()
        self._audio_codec_combo.addItems(["OPUS (推荐)", "AAC", "FLAC"])
        self._audio_codec_combo.setToolTip(
            "OPUS: 最佳质量和压缩率\n"
            "AAC: 良好的兼容性\n"
            "FLAC: 无损，带宽较高"
        )
        self._audio_codec_combo.currentIndexChanged.connect(self._on_config_changed)
        audio_layout.addWidget(self._audio_codec_combo, 2, 1, 1, 2)

        layout.addWidget(audio_group)

        # === FEC Settings ===
        fec_group = QGroupBox("前向纠错 (FEC)")
        fec_layout = QGridLayout(fec_group)

        # FEC description
        fec_desc = QLabel(
            "FEC 添加冗余数据包以恢复丢包。\n"
            "适用于网络不稳定的场景。"
        )
        fec_desc.setWordWrap(True)
        fec_desc.setStyleSheet("color: gray; font-size: 10px;")
        fec_layout.addWidget(fec_desc, 0, 0, 1, 4)

        # Video FEC
        self._video_fec_check = QCheckBox("视频 FEC")
        self._video_fec_check.setToolTip("为视频流启用 FEC")
        self._video_fec_check.toggled.connect(self._on_config_changed)
        fec_layout.addWidget(self._video_fec_check, 1, 0)

        # Audio FEC
        self._audio_fec_check = QCheckBox("音频 FEC")
        self._audio_fec_check.setToolTip("为音频流启用 FEC")
        self._audio_fec_check.toggled.connect(self._on_config_changed)
        fec_layout.addWidget(self._audio_fec_check, 1, 1)

        # FEC K (data packets)
        fec_layout.addWidget(QLabel("K:"), 2, 0)
        self._fec_k_spin = QSpinBox()
        self._fec_k_spin.setRange(1, 32)
        self._fec_k_spin.setValue(4)
        self._fec_k_spin.setToolTip("每组数据包数量")
        self._fec_k_spin.valueChanged.connect(self._on_config_changed)
        fec_layout.addWidget(self._fec_k_spin, 2, 1)

        # FEC M (parity packets)
        fec_layout.addWidget(QLabel("M:"), 2, 2)
        self._fec_m_spin = QSpinBox()
        self._fec_m_spin.setRange(1, 8)
        self._fec_m_spin.setValue(1)
        self._fec_m_spin.setToolTip("每组冗余包数量")
        self._fec_m_spin.valueChanged.connect(self._on_config_changed)
        fec_layout.addWidget(self._fec_m_spin, 2, 3)

        layout.addWidget(fec_group)

        # Add stretch
        layout.addStretch()

        # Initial state
        self._update_ui_state()

    def _on_video_toggled(self):
        """Handle video checkbox toggle."""
        self._update_ui_state()
        self._on_config_changed()

    def _on_audio_toggled(self):
        """Handle audio checkbox toggle."""
        self._update_ui_state()
        self._on_config_changed()

    def _update_ui_state(self):
        """Update UI state based on checkboxes."""
        video_enabled = self._video_check.isChecked()
        audio_enabled = self._audio_check.isChecked()

        # Enable/disable video options
        self._video_codec_combo.setEnabled(video_enabled)
        self._video_bitrate_spin.setEnabled(video_enabled)
        self._max_fps_combo.setEnabled(video_enabled)
        self._bitrate_mode_combo.setEnabled(video_enabled)
        self._iframe_spin.setEnabled(video_enabled)

        # Enable/disable audio options
        self._audio_codec_combo.setEnabled(audio_enabled)

    def _on_config_changed(self):
        """Handle configuration change."""
        self.config_changed.emit()

    def load_config(self, config: "DeviceConfig"):
        """Load configuration into UI."""
        # Block signals during load
        self.blockSignals(True)

        # Video settings
        self._video_check.setChecked(config.video_enabled)

        codec_map = {"auto": 0, "h264": 1, "h265": 2, "av1": 3}
        self._video_codec_combo.setCurrentIndex(codec_map.get(config.video_codec, 0))

        self._video_bitrate_spin.setValue(config.video_bitrate // 1000000)  # Convert to Mbps

        fps_map = {30: 0, 60: 1, 90: 2, 120: 3}
        self._max_fps_combo.setCurrentIndex(fps_map.get(config.max_fps, 1))

        self._bitrate_mode_combo.setCurrentIndex(0 if config.bitrate_mode == "vbr" else 1)

        self._iframe_spin.setValue(config.i_frame_interval)

        # Audio settings
        self._audio_check.setChecked(config.audio_enabled)

        # Audio codec: OPUS=3, AAC=1, FLAC=2
        audio_codec_map = {3: 0, 1: 1, 2: 2}  # OPUS, AAC, FLAC
        self._audio_codec_combo.setCurrentIndex(audio_codec_map.get(config.audio_codec, 0))

        # FEC settings
        self._video_fec_check.setChecked(config.video_fec_enabled)
        self._audio_fec_check.setChecked(config.audio_fec_enabled)
        self._fec_k_spin.setValue(config.fec_group_size)
        self._fec_m_spin.setValue(config.fec_parity_count)

        # Unblock signals
        self.blockSignals(False)

        # Update UI state
        self._update_ui_state()

    def save_config(self, config: "DeviceConfig"):
        """Save UI state to configuration."""
        # Video settings
        config.video_enabled = self._video_check.isChecked()

        codec_values = ["auto", "h264", "h265", "av1"]
        config.video_codec = codec_values[self._video_codec_combo.currentIndex()]

        config.video_bitrate = self._video_bitrate_spin.value() * 1000000  # Convert to bps

        fps_values = [30, 60, 90, 120]
        config.max_fps = fps_values[self._max_fps_combo.currentIndex()]

        config.bitrate_mode = "vbr" if self._bitrate_mode_combo.currentIndex() == 0 else "cbr"

        config.i_frame_interval = self._iframe_spin.value()

        # Audio settings
        config.audio_enabled = self._audio_check.isChecked()

        # Audio codec: OPUS=3, AAC=1, FLAC=2
        audio_codec_values = [3, 1, 2]  # OPUS, AAC, FLAC
        config.audio_codec = audio_codec_values[self._audio_codec_combo.currentIndex()]

        # FEC settings
        config.video_fec_enabled = self._video_fec_check.isChecked()
        config.audio_fec_enabled = self._audio_fec_check.isChecked()
        config.fec_group_size = self._fec_k_spin.value()
        config.fec_parity_count = self._fec_m_spin.value()
