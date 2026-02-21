"""
scrcpy-py-ddlx GUI Control Console

A GUI application for managing scrcpy client connections with MCP server support.

Usage:
    python -m scrcpy_py_ddlx.gui

Features:
    - ADB / Network mode selection
    - Stay-alive mode for network connections
    - Audio/Video independent control
    - Encoder, bitrate, framerate configuration
    - Multi-device configuration management
    - Server control (query/terminate)
    - Real-time preview window
    - MCP server integration
"""

import sys
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Check PySide6 availability
try:
    from PySide6.QtWidgets import QApplication
    PYSIDE6_AVAILABLE = True
except ImportError:
    QApplication = None
    PYSIDE6_AVAILABLE = False


def check_dependencies():
    """Check if all required dependencies are available."""
    missing = []

    if not PYSIDE6_AVAILABLE:
        missing.append("PySide6")

    # Check other dependencies
    try:
        import numpy
    except ImportError:
        missing.append("numpy")

    try:
        import av
    except ImportError:
        missing.append("av")

    return missing


def setup_logging(level=logging.INFO):
    """Setup logging configuration."""
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )


def main():
    """Main entry point for GUI application."""
    # Setup logging
    setup_logging(logging.INFO)

    if not PYSIDE6_AVAILABLE:
        print("Error: PySide6 is not installed")
        print("Please install: pip install PySide6")
        sys.exit(1)

    # Check dependencies
    missing = check_dependencies()
    if missing:
        print(f"Error: Missing dependencies: {', '.join(missing)}")
        print("Please install: pip install " + " ".join(missing))
        sys.exit(1)

    from scrcpy_py_ddlx.gui.main_window import MainWindow

    # Create Qt application
    app = QApplication(sys.argv)
    app.setApplicationName("scrcpy-py-ddlx")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("scrcpy-py-ddlx")

    # Set application style
    app.setStyle("Fusion")

    # Create main window
    window = MainWindow()
    window.show()

    logger.info("GUI started")

    # Run event loop
    sys.exit(app.exec())


# Export main classes
from scrcpy_py_ddlx.gui.main_window import MainWindow
from scrcpy_py_ddlx.gui.config_manager import ConfigManager, DeviceConfig, get_config_manager
from scrcpy_py_ddlx.gui.mcp_manager import MCPManager, MCPServerStatus, get_mcp_manager
from scrcpy_py_ddlx.gui.preview_window import PreviewWindow
from scrcpy_py_ddlx.gui.panels import ConnectionPanel, MediaPanel, DevicePanel, LogPanel

__all__ = [
    "main",
    "MainWindow",
    "ConfigManager",
    "DeviceConfig",
    "get_config_manager",
    "MCPManager",
    "MCPServerStatus",
    "get_mcp_manager",
    "PreviewWindow",
    "ConnectionPanel",
    "MediaPanel",
    "DevicePanel",
    "LogPanel",
    "PYSIDE6_AVAILABLE",
]


if __name__ == "__main__":
    main()
