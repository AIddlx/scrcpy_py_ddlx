"""
GUI panels for scrcpy-py-ddlx control console.
"""

from scrcpy_py_ddlx.gui.panels.connection_panel import ConnectionPanel
from scrcpy_py_ddlx.gui.panels.media_panel import MediaPanel
from scrcpy_py_ddlx.gui.panels.device_panel import DevicePanel
from scrcpy_py_ddlx.gui.panels.log_panel import LogPanel

__all__ = [
    "ConnectionPanel",
    "MediaPanel",
    "DevicePanel",
    "LogPanel",
]
