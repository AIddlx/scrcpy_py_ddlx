"""
Log display panel for scrcpy-py-ddlx GUI.
"""

import logging
import sys
from datetime import datetime
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QComboBox, QLabel, QCheckBox
)
from PySide6.QtCore import Qt, Slot, QTimer, QMutex, QMutexLocker
from PySide6.QtGui import QTextCursor, QColor, QTextCharFormat


class LogHandler(logging.Handler):
    """Custom log handler that emits signals for Qt."""

    def __init__(self, callback):
        super().__init__()
        self._callback = callback
        self._mutex = QMutex()

    def emit(self, record):
        """Emit log record."""
        try:
            msg = self.format(record)
            level = record.levelno
            self._callback(msg, level)
        except Exception:
            pass


class LogPanel(QWidget):
    """Panel for displaying log messages."""

    MAX_LINES = 1000  # Maximum lines to keep

    def __init__(self, parent=None):
        super().__init__(parent)
        self._log_handler: Optional[LogHandler] = None
        self._setup_ui()
        self._setup_logging()

    def _setup_ui(self):
        """Setup UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Log text area
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 11px;
            }
        """)
        layout.addWidget(self._log_text)

        # Control bar
        control_layout = QHBoxLayout()

        # Log level filter
        control_layout.addWidget(QLabel("级别:"))
        self._level_combo = QComboBox()
        self._level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._level_combo.setCurrentText("INFO")
        self._level_combo.currentTextChanged.connect(self._on_level_changed)
        control_layout.addWidget(self._level_combo)

        # Auto-scroll
        self._auto_scroll_check = QCheckBox("自动滚动")
        self._auto_scroll_check.setChecked(True)
        control_layout.addWidget(self._auto_scroll_check)

        control_layout.addStretch()

        # Clear button
        self._clear_btn = QPushButton("清空")
        self._clear_btn.clicked.connect(self._on_clear)
        control_layout.addWidget(self._clear_btn)

        # Copy button
        self._copy_btn = QPushButton("复制全部")
        self._copy_btn.clicked.connect(self._on_copy)
        control_layout.addWidget(self._copy_btn)

        layout.addLayout(control_layout)

    def _setup_logging(self):
        """Setup logging handler."""
        self._log_handler = LogHandler(self._append_log)
        self._log_handler.setFormatter(logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))

        # Add handler to root logger
        root_logger = logging.getLogger()
        root_logger.addHandler(self._log_handler)

        # Set initial level
        self._on_level_changed(self._level_combo.currentText())

    def _get_level_color(self, level: int) -> str:
        """Get color for log level."""
        if level >= logging.ERROR:
            return "#ff6b6b"  # Red
        elif level >= logging.WARNING:
            return "#ffd93d"  # Yellow
        elif level >= logging.INFO:
            return "#6bcb77"  # Green
        else:
            return "#4d96ff"  # Blue (DEBUG)

    @Slot(str, int)
    def _append_log(self, message: str, level: int):
        """Append log message to text area."""
        # This is called from logging thread, need to use QTimer for thread safety
        QTimer.singleShot(0, lambda: self._do_append_log(message, level))

    def _do_append_log(self, message: str, level: int):
        """Actually append the log message (called on main thread)."""
        # Check level filter
        level_names = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
                      "WARNING": logging.WARNING, "ERROR": logging.ERROR}
        current_level = level_names.get(self._level_combo.currentText(), logging.INFO)

        if level < current_level:
            return

        # Get cursor and format
        cursor = self._log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        # Set color format
        fmt = QTextCharFormat()
        color = self._get_level_color(level)
        fmt.setForeground(QColor(color))

        # Insert text with format
        cursor.insertText(message + "\n", fmt)

        # Limit lines
        self._limit_lines()

        # Auto-scroll
        if self._auto_scroll_check.isChecked():
            scrollbar = self._log_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _limit_lines(self):
        """Limit the number of lines in the log."""
        document = self._log_text.document()
        if document.blockCount() > self.MAX_LINES:
            cursor = self._log_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(
                QTextCursor.MoveOperation.Down,
                QTextCursor.MoveMode.KeepAnchor,
                document.blockCount() - self.MAX_LINES
            )
            cursor.removeSelectedText()

    def _on_level_changed(self, level: str):
        """Handle log level change."""
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR
        }
        if self._log_handler:
            self._log_handler.setLevel(level_map.get(level, logging.INFO))

    def _on_clear(self):
        """Clear log text."""
        self._log_text.clear()

    def _on_copy(self):
        """Copy all log text to clipboard."""
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(self._log_text.toPlainText())

    def cleanup(self):
        """Cleanup logging handler."""
        if self._log_handler:
            root_logger = logging.getLogger()
            root_logger.removeHandler(self._log_handler)
            self._log_handler = None

    def closeEvent(self, event):
        """Handle close event."""
        self.cleanup()
        super().closeEvent(event)
