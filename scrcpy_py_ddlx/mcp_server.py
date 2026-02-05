"""
MCP Server for scrcpy-py-ddlx

This module provides a Model Context Protocol (MCP) server for AI agents
to interact with Android devices through scrcpy.

Features:
- Device connection and state management
- Screenshot capture
- Clipboard operations
- Application listing
- Full device control (tap, swipe, keys, text input, etc.)
"""

import asyncio
import json
import logging
import base64
from typing import Any, Dict, List, Optional, Callable
from pathlib import Path
from datetime import datetime

from . import ScrcpyClient, ClientConfig

logger = logging.getLogger(__name__)


class ScrcpyMCPServer:
    """
    MCP Server for scrcpy-py-ddlx

    Provides tools for AI agents to:
    - Connect/disconnect from devices
    - Capture screenshots
    - Get/set clipboard
    - List installed apps
    - Control device (touch, keys, text input)
    """

    def __init__(
        self,
        default_config: Optional[ClientConfig] = None,
        log_file: Optional[str] = None,
        log_level: int = logging.INFO,
        enable_console_log: bool = True,
    ):
        """Initialize the MCP server

        Args:
            default_config: Default configuration for new connections
            log_file: Path to log file (None to disable file logging)
            log_level: Logging level (default: INFO)
            enable_console_log: Enable console output (default: True)
        """
        self._client: Optional[ScrcpyClient] = None
        self._default_config = default_config or ClientConfig(
            show_window=False,
            control=True,
            audio=False,
            lazy_decode=False,  # MCP 需要持续解码视频以支持随时截图
        )
        self._tools: Dict[str, Dict] = self._register_tools()

        # Configure logging
        self._setup_logging(log_file, log_level, enable_console_log)

    def _register_tools(self) -> Dict[str, Dict]:
        """Register all available MCP tools"""
        return {
            "connect": {
                "description": "Connect to an Android device via ADB",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "device_id": {
                            "type": "string",
                            "description": "ADB device ID (optional, auto-connects if not specified)",
                        },
                        "audio": {
                            "type": "boolean",
                            "description": "Enable audio streaming",
                            "default": False,
                        },
                        "tcpip": {
                            "type": "boolean",
                            "description": "Enable TCP/IP wireless mode",
                            "default": False,
                        },
                        "stay_awake": {
                            "type": "boolean",
                            "description": "Keep device awake",
                            "default": True,
                        },
                    },
                },
            },
            "disconnect": {
                "description": "Disconnect from the device",
                "parameters": {"type": "object", "properties": {}},
            },
            "get_state": {
                "description": "Get current device state (name, size, connection status)",
                "parameters": {"type": "object", "properties": {}},
            },
            "screenshot": {
                "description": "Capture a screenshot from the device",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Optional filename to save (PNG format)",
                        },
                        "return_base64": {
                            "type": "boolean",
                            "description": "Return image as base64 data URL",
                            "default": False,
                        },
                    },
                },
            },
            "get_clipboard": {
                "description": "Get the current clipboard content from the device",
                "parameters": {"type": "object", "properties": {}},
            },
            "set_clipboard": {
                "description": "Set the clipboard content on the device",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Text to set in clipboard",
                        },
                    },
                    "required": ["text"],
                },
            },
            "list_apps": {
                "description": "List all installed applications on the device",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "system_apps": {
                            "type": "boolean",
                            "description": "Include system applications",
                            "default": False,
                        },
                        "timeout": {
                            "type": "number",
                            "description": "Timeout in seconds",
                            "default": 30.0,
                        },
                    },
                },
            },
            "tap": {
                "description": "Tap at a specific position on the screen",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {
                            "type": "integer",
                            "description": "X coordinate in pixels",
                        },
                        "y": {
                            "type": "integer",
                            "description": "Y coordinate in pixels",
                        },
                    },
                    "required": ["x", "y"],
                },
            },
            "long_press": {
                "description": "Long press at a specific position on the screen",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "X coordinate in pixels"},
                        "y": {"type": "integer", "description": "Y coordinate in pixels"},
                        "duration_ms": {
                            "type": "integer",
                            "description": "Press duration in milliseconds",
                            "default": 500,
                        },
                    },
                    "required": ["x", "y"],
                },
            },
            "swipe": {
                "description": "Swipe from one position to another",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x1": {"type": "integer", "description": "Start X coordinate"},
                        "y1": {"type": "integer", "description": "Start Y coordinate"},
                        "x2": {"type": "integer", "description": "End X coordinate"},
                        "y2": {"type": "integer", "description": "End Y coordinate"},
                        "duration_ms": {
                            "type": "integer",
                            "description": "Swipe duration in milliseconds",
                            "default": 300,
                        },
                    },
                    "required": ["x1", "y1", "x2", "y2"],
                },
            },
            "press_key": {
                "description": "Press a hardware or software key",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key_code": {
                            "type": "string",
                            "description": "Key code (e.g., 'HOME', 'BACK', 'ENTER', 'VOLUME_UP')",
                        },
                    },
                    "required": ["key_code"],
                },
            },
            "input_text": {
                "description": "Input text as if typed on the keyboard",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Text to input",
                        },
                    },
                    "required": ["text"],
                },
            },
            "back": {
                "description": "Press the back button",
                "parameters": {"type": "object", "properties": {}},
            },
            "home": {
                "description": "Press the home button",
                "parameters": {"type": "object", "properties": {}},
            },
            "recent_apps": {
                "description": "Open recent apps (overview) screen",
                "parameters": {"type": "object", "properties": {}},
            },
            "volume_up": {
                "description": "Press volume up button",
                "parameters": {"type": "object", "properties": {}},
            },
            "volume_down": {
                "description": "Press volume down button",
                "parameters": {"type": "object", "properties": {}},
            },
            "wake_up": {
                "description": "Wake up the device screen",
                "parameters": {"type": "object", "properties": {}},
            },
            "open_app": {
                "description": "Launch an application by package name",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "package": {
                            "type": "string",
                            "description": "Package name (e.g., 'com.android.settings')",
                        },
                    },
                    "required": ["package"],
                },
            },
            # ==================== Additional Key Controls ====================
            "menu": {
                "description": "Press the menu button",
                "parameters": {"type": "object", "properties": {}},
            },
            "enter": {
                "description": "Press the enter key",
                "parameters": {"type": "object", "properties": {}},
            },
            "tab": {
                "description": "Press the tab key",
                "parameters": {"type": "object", "properties": {}},
            },
            "escape": {
                "description": "Press the escape key",
                "parameters": {"type": "object", "properties": {}},
            },
            "dpad_up": {
                "description": "Press D-pad up button",
                "parameters": {"type": "object", "properties": {}},
            },
            "dpad_down": {
                "description": "Press D-pad down button",
                "parameters": {"type": "object", "properties": {}},
            },
            "dpad_left": {
                "description": "Press D-pad left button",
                "parameters": {"type": "object", "properties": {}},
            },
            "dpad_right": {
                "description": "Press D-pad right button",
                "parameters": {"type": "object", "properties": {}},
            },
            "dpad_center": {
                "description": "Press D-pad center button",
                "parameters": {"type": "object", "properties": {}},
            },
            # ==================== Notification & Panels ====================
            "expand_notification_panel": {
                "description": "Expand the notification panel",
                "parameters": {"type": "object", "properties": {}},
            },
            "expand_settings_panel": {
                "description": "Expand the settings panel",
                "parameters": {"type": "object", "properties": {}},
            },
            "collapse_panels": {
                "description": "Collapse all panels (notification/settings)",
                "parameters": {"type": "object", "properties": {}},
            },
            # ==================== Display Control ====================
            "turn_screen_on": {
                "description": "Turn the screen on",
                "parameters": {"type": "object", "properties": {}},
            },
            "turn_screen_off": {
                "description": "Turn the screen off",
                "parameters": {"type": "object", "properties": {}},
            },
            "rotate_device": {
                "description": "Rotate the device",
                "parameters": {"type": "object", "properties": {}},
            },
            "reset_video": {
                "description": "Reset video stream (useful if video freezes)",
                "parameters": {"type": "object", "properties": {}},
            },
            # ==================== Audio Recording ====================
            "record_audio": {
                "description": "Record audio to file for a specific duration",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Output filename. Extension determines format: .wav (default), .opus, .mp3",
                        },
                        "duration": {
                            "type": "number",
                            "description": "Recording duration in seconds",
                        },
                        "format": {
                            "type": "string",
                            "description": "Output format: 'wav' (default), 'opus', 'mp3'. If not specified, uses filename extension.",
                            "enum": ["wav", "opus", "mp3"],
                        },
                    },
                    "required": ["filename", "duration"],
                },
            },
            "stop_audio_recording": {
                "description": "Stop audio recording and save the file",
                "parameters": {"type": "object", "properties": {}},
            },
            "is_recording_audio": {
                "description": "Check if audio recording is in progress",
                "parameters": {"type": "object", "properties": {}},
            },
            "get_recording_duration": {
                "description": "Get the current recording duration in seconds",
                "parameters": {"type": "object", "properties": {}},
            },
            # ==================== Advanced Screenshots ====================
            "screenshot_device": {
                "description": "Take a screenshot from the device server (full process)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Optional filename to save (PNG format)",
                        },
                    },
                },
            },
            "screenshot_standalone": {
                "description": "Take a standalone screenshot (connects temporarily)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Optional filename to save (PNG format)",
                        },
                    },
                },
            },
        }

    def _setup_logging(
        self,
        log_file: Optional[str],
        log_level: int,
        enable_console_log: bool,
    ) -> None:
        """Setup logging configuration

        Args:
            log_file: Path to log file (None to disable)
            log_level: Logging level
            enable_console_log: Enable console output
        """
        # Create logger for this server instance
        self._logger = logging.getLogger(f"{__name__}.ScrcpyMCPServer")
        self._logger.setLevel(log_level)

        # Remove existing handlers to avoid duplicates
        self._logger.handlers.clear()

        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # Add file handler if log_file is specified
        if log_file:
            # Create directory if it doesn't exist
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(log_level)
            file_handler.setFormatter(formatter)
            self._logger.addHandler(file_handler)
            self._log_file = log_file
        else:
            self._log_file = None

        # Add console handler if enabled
        if enable_console_log:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(log_level)
            console_handler.setFormatter(formatter)
            self._logger.addHandler(console_handler)

        # Don't propagate to root logger
        self._logger.propagate = False

    def get_log_file(self) -> Optional[str]:
        """Get the current log file path

        Returns:
            Log file path or None if not set
        """
        return self._log_file

    # ==================== Connection Methods ====================

    def connect(
        self,
        device_id: Optional[str] = None,
        audio: Optional[bool] = None,
        tcpip: bool = False,
        stay_awake: bool = True,
    ) -> Dict[str, Any]:
        """Connect to an Android device

        Args:
            device_id: ADB device ID (None for auto-connect)
            audio: Enable audio streaming (None uses default_config.audio)
            tcpip: Enable TCP/IP wireless mode
            stay_awake: Keep device awake

        Returns:
            Dictionary with connection result and device info
        """
        # Use default_config.audio if audio parameter is not specified
        if audio is None:
            audio = self._default_config.audio
        if self._client is not None and self._client.state.connected:
            return {
                "success": False,
                "error": "Already connected",
                "device_name": self._client.state.device_name,
            }

        # Preserve lazy_decode from default_config, override other settings
        config = ClientConfig(
            show_window=False,
            control=True,
            audio=audio,
            lazy_decode=self._default_config.lazy_decode,
            tcpip=tcpip,
            tcpip_auto_disconnect=False,
            stay_awake=stay_awake,
        )

        try:
            self._client = ScrcpyClient(config)

            if not self._client.connect():
                return {
                    "success": False,
                    "error": "Connection failed",
                }

            return {
                "success": True,
                "device_name": self._client.state.device_name,
                "device_size": list(self._client.state.device_size),
                "codec_id": hex(self._client.state.codec_id),
                "tcpip_connected": self._client.state.tcpip_connected,
                "tcpip_address": (
                    f"{self._client.state.tcpip_ip}:{self._client.state.tcpip_port}"
                    if self._client.state.tcpip_connected
                    else None
                ),
            }

        except Exception as e:
            self._client = None
            self._logger.error(f"Connection error: {e}")
            return {"success": False, "error": str(e)}

    def disconnect(self) -> Dict[str, Any]:
        """Disconnect from the device

        Returns:
            Dictionary with disconnect result
        """
        if self._client is None:
            return {"success": True, "message": "No active connection"}

        try:
            self._client.disconnect()
            self._client = None
            return {"success": True, "message": "Disconnected"}
        except Exception as e:
            self._logger.error(f"Disconnect error: {e}")
            return {"success": False, "error": str(e)}

    def get_state(self) -> Dict[str, Any]:
        """Get current device state

        Returns:
            Dictionary with device state information
        """
        if self._client is None or not self._client.state.connected:
            return {
                "connected": False,
                "device_name": None,
                "device_size": None,
            }

        return {
            "connected": True,
            "device_name": self._client.state.device_name,
            "device_size": list(self._client.state.device_size),
            "codec_id": hex(self._client.state.codec_id),
            "tcpip_connected": self._client.state.tcpip_connected,
        }

    # ==================== Screenshot ====================

    def screenshot(
        self, filename: Optional[str] = None, return_base64: bool = False
    ) -> Dict[str, Any]:
        """Capture a screenshot

        Args:
            filename: Optional filename to save (PNG format)
            return_base64: Return image as base64 data URL

        Returns:
            Dictionary with screenshot result
        """
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}

        try:
            import numpy as np

            frame = self._client.screenshot(filename)

            result = {
                "success": True,
                "shape": list(frame.shape) if frame is not None else None,
            }

            if filename:
                result["filename"] = filename

            if return_base64 and frame is not None:
                import io

                # Try OpenCV first, fallback to Pillow
                try:
                    import cv2

                    _, buffer = cv2.imencode(".png", frame)
                    img_bytes = buffer.tobytes()
                except ImportError:
                    try:
                        from PIL import Image

                        img = Image.fromarray(frame)
                        buffer = io.BytesIO()
                        img.save(buffer, format="PNG")
                        img_bytes = buffer.getvalue()
                    except ImportError:
                        return {
                            "success": False,
                            "error": "No image encoder available (install opencv-python or Pillow)",
                        }

                b64_data = base64.b64encode(img_bytes).decode("utf-8")
                result["base64"] = f"data:image/png;base64,{b64_data}"

            return result

        except Exception as e:
            self._logger.error(f"Screenshot error: {e}")
            return {"success": False, "error": str(e)}

    # ==================== Clipboard ====================

    def get_clipboard(self) -> Dict[str, Any]:
        """Get the current clipboard content

        Returns:
            Dictionary with clipboard content
        """
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}

        try:
            text = self._client.get_clipboard()
            return {"success": True, "text": text}
        except Exception as e:
            self._logger.error(f"Get clipboard error: {e}")
            return {"success": False, "error": str(e)}

    def set_clipboard(self, text: str) -> Dict[str, Any]:
        """Set the clipboard content

        Args:
            text: Text to set in clipboard

        Returns:
            Dictionary with set result
        """
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}

        try:
            self._client.set_clipboard(text)
            return {"success": True, "text": text}
        except Exception as e:
            self._logger.error(f"Set clipboard error: {e}")
            return {"success": False, "error": str(e)}

    # ==================== Apps ====================

    def list_apps(
        self, system_apps: bool = False, timeout: float = 30.0
    ) -> Dict[str, Any]:
        """List all installed applications

        Args:
            system_apps: Include system applications
            timeout: Timeout in seconds

        Returns:
            Dictionary with app list
        """
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}

        try:
            apps = self._client.list_apps(timeout=timeout)

            if system_apps:
                result_apps = apps
            else:
                result_apps = [app for app in apps if not app["system"]]

            return {
                "success": True,
                "total_count": len(apps),
                "filtered_count": len(result_apps),
                "apps": result_apps,
            }
        except Exception as e:
            self._logger.error(f"List apps error: {e}")
            return {"success": False, "error": str(e)}

    # ==================== Touch Control ====================

    def tap(self, x: int, y: int) -> Dict[str, Any]:
        """Tap at a specific position

        Args:
            x: X coordinate in pixels
            y: Y coordinate in pixels

        Returns:
            Dictionary with tap result
        """
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}

        try:
            self._client.tap(x, y)
            return {"success": True, "position": {"x": x, "y": y}}
        except Exception as e:
            self._logger.error(f"Tap error: {e}")
            return {"success": False, "error": str(e)}

    def long_press(self, x: int, y: int, duration_ms: int = 500) -> Dict[str, Any]:
        """Long press at a specific position

        Args:
            x: X coordinate in pixels
            y: Y coordinate in pixels
            duration_ms: Press duration in milliseconds

        Returns:
            Dictionary with long press result
        """
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}

        try:
            import time
            from scrcpy_py_ddlx.core.protocol import AndroidMotionEventAction

            width, height = self._client.state.device_size
            pointer_id = -2  # POINTER_ID_GENERIC_FINGER

            # DOWN event with pressure 1.0
            self._client.inject_touch_event(
                AndroidMotionEventAction.DOWN,
                pointer_id,
                x,
                y,
                width,
                height,
                1.0,  # pressure
            )

            # Wait for the specified duration
            time.sleep(duration_ms / 1000.0)

            # UP event with pressure 0.0
            self._client.inject_touch_event(
                AndroidMotionEventAction.UP,
                pointer_id,
                x,
                y,
                width,
                height,
                0.0,  # pressure
            )

            return {
                "success": True,
                "position": {"x": x, "y": y},
                "duration_ms": duration_ms,
            }
        except Exception as e:
            self._logger.error(f"Long press error: {e}")
            return {"success": False, "error": str(e)}

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
    ) -> Dict[str, Any]:
        """Swipe from one position to another

        Args:
            x1: Start X coordinate
            y1: Start Y coordinate
            x2: End X coordinate
            y2: End Y coordinate
            duration_ms: Swipe duration in milliseconds

        Returns:
            Dictionary with swipe result
        """
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}

        try:
            self._client.swipe(x1, y1, x2, y2, duration_ms)
            return {
                "success": True,
                "start": {"x": x1, "y": y1},
                "end": {"x": x2, "y": y2},
                "duration_ms": duration_ms,
            }
        except Exception as e:
            self._logger.error(f"Swipe error: {e}")
            return {"success": False, "error": str(e)}

    # ==================== Key Control ====================

    def press_key(self, key_code: str) -> Dict[str, Any]:
        """Press a hardware or software key

        Args:
            key_code: Key code name (e.g., 'HOME', 'BACK', 'ENTER')

        Returns:
            Dictionary with press result
        """
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}

        try:
            from scrcpy_py_ddlx.core.keycode import KeyCode

            # Map string key codes to KeyCode enum values
            # Include both official names and common aliases
            key_map = {
                # Navigation keys
                "HOME": KeyCode.HOME,
                "BACK": KeyCode.BACK,
                "ENTER": KeyCode.ENTER,
                "TAB": KeyCode.TAB,
                "ESCAPE": KeyCode.ESCAPE,
                "SPACE": KeyCode.SPACE,

                # Volume keys
                "VOLUME_UP": KeyCode.VOLUME_UP,
                "VOLUME_DOWN": KeyCode.VOLUME_DOWN,
                "VOLUME_MUTE": KeyCode.VOLUME_MUTE,

                # D-Pad
                "DPAD_UP": KeyCode.DPAD_UP,
                "DPAD_DOWN": KeyCode.DPAD_DOWN,
                "DPAD_LEFT": KeyCode.DPAD_LEFT,
                "DPAD_RIGHT": KeyCode.DPAD_RIGHT,
                "DPAD_CENTER": KeyCode.DPAD_CENTER,

                # Media/app keys
                "APP_SWITCH": KeyCode.APP_SWITCH,
                "MENU": KeyCode.MENU,
                "MEDIA_PLAY_PAUSE": KeyCode.MEDIA_PLAY_PAUSE,
                "MEDIA_NEXT": KeyCode.MEDIA_NEXT,
                "MEDIA_PREVIOUS": KeyCode.MEDIA_PREVIOUS,
                "CUT": KeyCode.CUT,
                "COPY": KeyCode.COPY,
                "PASTE": KeyCode.PASTE,  # Android 专用粘贴 keycode (279)

                # Special keys
                "DELETE": KeyCode.DEL,  # Backspace
                "DEL": KeyCode.DEL,       # Backspace
                "FORWARD_DEL": KeyCode.FORWARD_DEL,  # Delete key
                "CLEAR": KeyCode.CLEAR,  # Clear key
                "KEYCODE_DEL": KeyCode.DEL,  # Common alias
                "KEYCODE_CLEAR": KeyCode.CLEAR,  # Common alias
                "MOVE_HOME": KeyCode.MOVE_HOME,  # Cursor to start
                "MOVE_END": KeyCode.MOVE_END,    # Cursor to end
                "INSERT": KeyCode.INSERT,

                # Function keys (F1-F12)
                "F1": KeyCode.F1,
                "F2": KeyCode.F2,
                "F3": KeyCode.F3,
                "F4": KeyCode.F4,
                "F5": KeyCode.F5,
                "F6": KeyCode.F6,
                "F7": KeyCode.F7,
                "F8": KeyCode.F8,
                "F9": KeyCode.F9,
                "F10": KeyCode.F10,
                "F11": KeyCode.F11,
                "F12": KeyCode.F12,

                # Modifiers
                "CTRL_LEFT": KeyCode.CTRL_LEFT,
                "CTRL_RIGHT": KeyCode.CTRL_RIGHT,
                "SHIFT_LEFT": KeyCode.SHIFT_LEFT,
                "SHIFT_RIGHT": KeyCode.SHIFT_RIGHT,
                "ALT_LEFT": KeyCode.ALT_LEFT,
                "ALT_RIGHT": KeyCode.ALT_RIGHT,

                # Letter keys (A-Z)
                "A": KeyCode.A, "B": KeyCode.B, "C": KeyCode.C, "D": KeyCode.D,
                "E": KeyCode.E, "F": KeyCode.F, "G": KeyCode.G, "H": KeyCode.H,
                "I": KeyCode.I, "J": KeyCode.J, "K": KeyCode.K, "L": KeyCode.L,
                "M": KeyCode.M, "N": KeyCode.N, "O": KeyCode.O, "P": KeyCode.P,
                "Q": KeyCode.Q, "R": KeyCode.R, "S": KeyCode.S, "T": KeyCode.T,
                "U": KeyCode.U, "V": KeyCode.V, "W": KeyCode.W, "X": KeyCode.X,
                "Y": KeyCode.Y, "Z": KeyCode.Z,

                # Number keys (0-9)
                "KEY_0": KeyCode.KEY_0, "KEY_1": KeyCode.KEY_1, "KEY_2": KeyCode.KEY_2,
                "KEY_3": KeyCode.KEY_3, "KEY_4": KeyCode.KEY_4, "KEY_5": KeyCode.KEY_5,
                "KEY_6": KeyCode.KEY_6, "KEY_7": KeyCode.KEY_7, "KEY_8": KeyCode.KEY_8,
                "KEY_9": KeyCode.KEY_9,
                "0": KeyCode.KEY_0, "1": KeyCode.KEY_1, "2": KeyCode.KEY_2,
                "3": KeyCode.KEY_3, "4": KeyCode.KEY_4, "5": KeyCode.KEY_5,
                "6": KeyCode.KEY_6, "7": KeyCode.KEY_7, "8": KeyCode.KEY_8,
                "9": KeyCode.KEY_9,

                # Punctuation
                "STAR": KeyCode.STAR,     # *
                "POUND": KeyCode.POUND,   # #
                "COMMA": KeyCode.COMMA,    # ,
                "PERIOD": KeyCode.PERIOD,  # .
                "EQUALS": KeyCode.EQUALS,  # =
                "MINUS": KeyCode.MINUS,    # -
                "PLUS": KeyCode.PLUS,      # +
            }

            code = key_map.get(key_code.upper())
            if code is None:
                return {"success": False, "error": f"Unknown key code: {key_code}"}

            # Send both DOWN and UP events for complete key press
            from scrcpy_py_ddlx.core.protocol import AndroidKeyEventAction
            self._client.inject_keycode(code.value, AndroidKeyEventAction.DOWN)
            self._client.inject_keycode(code.value, AndroidKeyEventAction.UP)
            return {"success": True, "key_code": key_code}
        except Exception as e:
            self._logger.error(f"Press key error: {e}")
            return {"success": False, "error": str(e)}

    def input_text(self, text: str) -> Dict[str, Any]:
        """Input text as if typed on the keyboard

        Args:
            text: Text to input

        Returns:
            Dictionary with input result

        Note:
            - ASCII characters: use scrcpy inject_text (fast, direct)
            - Non-ASCII (Chinese): use YADB for reliable Unicode support
        """
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}

        try:
            # 检测是否包含非 ASCII 字符（如中文）
            if any(ord(char) > 127 for char in text):
                # 使用 YADB 支持中文（gelab-zero 项目使用的方案）
                # YADB: Yet Another ADB Bridge - 支持 Unicode 键盘输入
                import subprocess

                device_serial = getattr(self._client.state, "device_serial", None)
                if not device_serial:
                    return {"success": False, "error": "Device serial not available"}

                # 预处理文本：转义特殊字符
                def preprocess_text_for_yadb(t):
                    t = t.replace("\n", " ").replace("\t", " ")
                    t = t.replace(" ", "\\ ")
                    return t

                escaped_text = preprocess_text_for_yadb(text)
                # YADB 命令格式（gelab-zero 验证可用）
                cmd = f"adb -s {device_serial} shell app_process -Djava.class.path=/data/local/tmp/yadb /data/local/tmp com.ysbing.yadb.Main -keyboard '{escaped_text}'"

                self._logger.debug(f"Running YADB input: {cmd}")
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)

                if result.returncode == 0:
                    return {"success": True, "text": text, "method": "yadb_keyboard"}
                else:
                    # YADB 失败时回退到 ADB input text（部分中文可能不支持）
                    self._logger.warning(f"YADB failed: {result.stderr}, falling back to ADB input text")
                    escaped_text = text.replace(" ", "%s")
                    cmd = ["adb", "-s", device_serial, "shell", "input", "text", escaped_text]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        return {"success": True, "text": text, "method": "adb_input_text"}
                    return {"success": False, "error": result.stderr}
            else:
                # ASCII 字符使用原生 inject_text（更快，直接通过 scrcpy 协议）
                self._client.inject_text(text)
                return {"success": True, "text": text, "method": "inject_text"}
        except Exception as e:
            self._logger.error(f"Input text error: {e}")
            return {"success": False, "error": str(e)}

    # ==================== Convenience Methods ====================

    def back(self) -> Dict[str, Any]:
        """Press the back button"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.back()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Back error: {e}")
            return {"success": False, "error": str(e)}

    def home(self) -> Dict[str, Any]:
        """Press the home button"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.home()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Home error: {e}")
            return {"success": False, "error": str(e)}

    def recent_apps(self) -> Dict[str, Any]:
        """Open recent apps screen"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.app_switch()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Recent apps error: {e}")
            return {"success": False, "error": str(e)}

    def volume_up(self) -> Dict[str, Any]:
        """Press volume up button"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.volume_up()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Volume up error: {e}")
            return {"success": False, "error": str(e)}

    def volume_down(self) -> Dict[str, Any]:
        """Press volume down button"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.volume_down()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Volume down error: {e}")
            return {"success": False, "error": str(e)}

    def wake_up(self) -> Dict[str, Any]:
        """Wake up the device"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.set_display_power(True)
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Wake up error: {e}")
            return {"success": False, "error": str(e)}

    # ==================== Additional Key Controls ====================

    def menu(self) -> Dict[str, Any]:
        """Press the menu button"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.menu()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Menu error: {e}")
            return {"success": False, "error": str(e)}

    def enter(self) -> Dict[str, Any]:
        """Press the enter key"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.enter()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Enter error: {e}")
            return {"success": False, "error": str(e)}

    def tab(self) -> Dict[str, Any]:
        """Press the tab key"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.tab()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Tab error: {e}")
            return {"success": False, "error": str(e)}

    def escape(self) -> Dict[str, Any]:
        """Press the escape key"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.escape()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Escape error: {e}")
            return {"success": False, "error": str(e)}

    def dpad_up(self) -> Dict[str, Any]:
        """Press D-pad up button"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.dpad_up()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"DPad up error: {e}")
            return {"success": False, "error": str(e)}

    def dpad_down(self) -> Dict[str, Any]:
        """Press D-pad down button"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.dpad_down()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"DPad down error: {e}")
            return {"success": False, "error": str(e)}

    def dpad_left(self) -> Dict[str, Any]:
        """Press D-pad left button"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.dpad_left()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"DPad left error: {e}")
            return {"success": False, "error": str(e)}

    def dpad_right(self) -> Dict[str, Any]:
        """Press D-pad right button"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.dpad_right()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"DPad right error: {e}")
            return {"success": False, "error": str(e)}

    def dpad_center(self) -> Dict[str, Any]:
        """Press D-pad center button"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.dpad_center()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"DPad center error: {e}")
            return {"success": False, "error": str(e)}

    # ==================== Notification & Panels ====================

    def expand_notification_panel(self) -> Dict[str, Any]:
        """Expand the notification panel"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.expand_notification_panel()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Expand notification panel error: {e}")
            return {"success": False, "error": str(e)}

    def expand_settings_panel(self) -> Dict[str, Any]:
        """Expand the settings panel"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.expand_settings_panel()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Expand settings panel error: {e}")
            return {"success": False, "error": str(e)}

    def collapse_panels(self) -> Dict[str, Any]:
        """Collapse all panels"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.collapse_panels()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Collapse panels error: {e}")
            return {"success": False, "error": str(e)}

    # ==================== Display Control ====================

    def turn_screen_on(self) -> Dict[str, Any]:
        """Turn the screen on"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.turn_screen_on()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Turn screen on error: {e}")
            return {"success": False, "error": str(e)}

    def turn_screen_off(self) -> Dict[str, Any]:
        """Turn the screen off"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.turn_screen_off()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Turn screen off error: {e}")
            return {"success": False, "error": str(e)}

    def rotate_device(self) -> Dict[str, Any]:
        """Rotate the device"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.rotate_device()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Rotate device error: {e}")
            return {"success": False, "error": str(e)}

    def reset_video(self) -> Dict[str, Any]:
        """Reset video stream"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            self._client.reset_video()
            return {"success": True}
        except Exception as e:
            self._logger.error(f"Reset video error: {e}")
            return {"success": False, "error": str(e)}

    # ==================== Audio Recording ====================

    def record_audio(
        self, filename: str, duration: float, format: Optional[str] = None
    ) -> Dict[str, Any]:
        """Record audio to file

        Args:
            filename: Output filename (extension determines format: .wav, .opus, .mp3)
            duration: Recording duration in seconds
            format: Output format ('wav', 'opus', 'mp3'). If None, uses filename extension

        Returns:
            Dictionary with recording result
        """
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            # Determine format from filename or parameter
            if format is None:
                if filename.endswith(".opus"):
                    format = "opus"
                elif filename.endswith(".mp3"):
                    format = "mp3"
                else:
                    format = "wav"

            # Map format to auto_convert_to value
            format_map = {
                "wav": None,
                "opus": "opus",
                "mp3": "mp3",
            }

            auto_convert = format_map.get(format)

            # 检查音频解码器状态
            self._logger.info(f"Audio state check:")
            self._logger.info(f"  _audio_enabled: {self._client._audio_enabled if hasattr(self._client, '_audio_enabled') else 'N/A'}")
            self._logger.info(f"  _audio_decoder: {self._client._audio_decoder}")
            self._logger.info(f"  config.audio: {self._client.config.audio if hasattr(self._client, 'config') else 'N/A'}")

            if self._client._audio_decoder is None:
                self._logger.error("Audio decoder is None - audio not enabled?")
                return {"success": False, "error": "Audio decoder not available. Is audio enabled in connection?"}

            # 检查 frame_sink 状态
            frame_sink = self._client._audio_decoder._frame_sink
            self._logger.info(f"  frame_sink type: {type(frame_sink).__name__ if frame_sink else 'None'}")
            self._logger.info(f"  frame_sink: {frame_sink}")

            success = self._client.start_audio_recording(
                filename,
                max_duration=duration,
                auto_convert_to=auto_convert,
            )

            if not success:
                self._logger.error("start_audio_recording returned False")
                return {"success": False, "error": "Failed to start recording - check server logs"}

            # Wait for recording to complete
            if success:
                import time
                time.sleep(duration + 0.5)  # Add small buffer
                final_filename = self._client.stop_audio_recording()
                # Wait for file to be fully written
                time.sleep(1.0)
                return {
                    "success": True,
                    "filename": final_filename or filename,
                    "duration": duration,
                    "format": format,
                }

            return {"success": False, "error": "Failed to start recording"}
        except Exception as e:
            self._logger.error(f"Record audio error: {e}")
            return {"success": False, "error": str(e)}

    def stop_audio_recording(self) -> Dict[str, Any]:
        """Stop audio recording"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            filename = self._client.stop_audio_recording()
            if filename:
                return {"success": True, "filename": filename}
            else:
                return {"success": False, "error": "No recording was in progress"}
        except Exception as e:
            self._logger.error(f"Stop audio recording error: {e}")
            return {"success": False, "error": str(e)}

    def is_recording_audio(self) -> Dict[str, Any]:
        """Check if audio recording is in progress"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected", "recording": False}
        try:
            recording = self._client.is_recording_audio()
            return {"success": True, "recording": recording}
        except Exception as e:
            self._logger.error(f"Is recording audio error: {e}")
            return {"success": False, "error": str(e), "recording": False}

    def get_recording_duration(self) -> Dict[str, Any]:
        """Get the current recording duration"""
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected", "duration": 0.0}
        try:
            duration = self._client.get_recording_duration()
            return {"success": True, "duration": duration}
        except Exception as e:
            self._logger.error(f"Get recording duration error: {e}")
            return {"success": False, "error": str(e), "duration": 0.0}

    # ==================== Advanced Screenshots ====================

    def screenshot_device(self, filename: Optional[str] = None) -> Dict[str, Any]:
        """Take a screenshot from the device server (full process)

        Args:
            filename: Optional filename to save

        Returns:
            Dictionary with screenshot result
        """
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            frame = self._client.screenshot_device(filename)

            result = {"success": frame is not None}
            if filename:
                result["filename"] = filename
            if frame is not None:
                result["shape"] = list(frame.shape)

            return result
        except Exception as e:
            self._logger.error(f"Screenshot device error: {e}")
            return {"success": False, "error": str(e)}

    def screenshot_standalone(self, filename: Optional[str] = None) -> Dict[str, Any]:
        """Take a standalone screenshot (connects temporarily)

        Args:
            filename: Optional filename to save

        Returns:
            Dictionary with screenshot result
        """
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}
        try:
            frame = self._client.screenshot_standalone(filename)

            result = {"success": frame is not None}
            if filename:
                result["filename"] = filename
            if frame is not None:
                result["shape"] = list(frame.shape)

            return result
        except Exception as e:
            self._logger.error(f"Screenshot standalone error: {e}")
            return {"success": False, "error": str(e)}

    # ==================== App Control ====================

    def open_app(self, package: str) -> Dict[str, Any]:
        """Launch an application by package name

        Args:
            package: Package name (e.g., 'com.android.settings')

        Returns:
            Dictionary with launch result
        """
        if self._client is None or not self._client.state.connected:
            return {"success": False, "error": "Not connected"}

        try:
            self._client.start_app(package)
            return {"success": True, "package": package}
        except Exception as e:
            self._logger.error(f"Open app error: {e}")
            return {"success": False, "error": str(e)}

    # ==================== MCP Protocol Handlers ====================

    def get_tools_schema(self) -> Dict[str, Any]:
        """Get the JSON schema of all available tools"""
        return {
            "tools": [
                {"name": name, **definition} for name, definition in self._tools.items()
            ]
        }

    def handle_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Handle an MCP tool call

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments

        Returns:
            Tool execution result
        """
        if tool_name not in self._tools:
            return {
                "success": False,
                "error": f"Unknown tool: {tool_name}",
            }

        method = getattr(self, tool_name, None)
        if method is None:
            return {
                "success": False,
                "error": f"Tool not implemented: {tool_name}",
            }

        try:
            return method(**arguments)
        except TypeError as e:
            return {
                "success": False,
                "error": f"Invalid arguments: {e}",
            }
        except Exception as e:
            self._logger.error(f"Tool call error ({tool_name}): {e}")
            return {
                "success": False,
                "error": str(e),
            }


def create_mcp_server(
    default_config: Optional[ClientConfig] = None,
    log_file: Optional[str] = None,
    log_level: int = logging.INFO,
    enable_console_log: bool = True,
) -> ScrcpyMCPServer:
    """Create a new MCP server instance

    Args:
        default_config: Default configuration for connections
        log_file: Path to log file (None to disable file logging)
        log_level: Logging level (default: logging.INFO)
        enable_console_log: Enable console output (default: True)

    Returns:
        ScrcpyMCPServer instance

    Examples:
        >>> # Create server with file logging
        >>> server = create_mcp_server(log_file="logs/mcp_server.log")

        >>> # Create server with timestamp in log filename
        >>> from datetime import datetime
        >>> timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        >>> server = create_mcp_server(log_file=f"logs/mcp_{timestamp}.log")

        >>> # Create server with debug level
        >>> server = create_mcp_server(log_level=logging.DEBUG)
    """
    return ScrcpyMCPServer(default_config, log_file, log_level, enable_console_log)


# ==================== Example CLI Usage ====================

def main():
    """Simple CLI for testing the MCP server"""
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        description="scrcpy-py-ddlx MCP Server - Test CLI"
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Path to log file (e.g., logs/mcp_server.log)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--no-console",
        action="store_true",
        help="Disable console log output",
    )

    args = parser.parse_args()

    # Parse log level
    log_level = getattr(logging, args.log_level)

    # Create logs directory if using log file
    if args.log_file:
        Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)

    # Create server with logging
    server = create_mcp_server(
        log_file=args.log_file,
        log_level=log_level,
        enable_console_log=not args.no_console,
    )

    print("scrcpy-py-ddlx MCP Server CLI")
    print("=" * 50)

    # Example: Connect
    print("\n1. Connecting to device...")
    result = server.connect()
    print(f"   Result: {result}")

    if not result.get("success"):
        print("   Exiting due to connection failure")
        sys.exit(1)

    # Example: Get state
    print("\n2. Getting device state...")
    state = server.get_state()
    print(f"   Device: {state.get('device_name')}")
    print(f"   Size: {state.get('device_size')}")

    # Example: Screenshot
    print("\n3. Capturing screenshot...")
    screen = server.screenshot("mcp_test_screenshot.png")
    print(f"   Result: {screen}")

    # Example: List apps
    print("\n4. Listing user apps...")
    apps_result = server.list_apps(system_apps=False)
    print(f"   Found {apps_result.get('filtered_count')} apps")
    if apps_result.get("success"):
        for app in apps_result["apps"][:5]:
            print(f"   - {app['name']} ({app['package']})")

    # Example: Disconnect
    print("\n5. Disconnecting...")
    disc = server.disconnect()
    print(f"   Result: {disc}")

    print("\nDone!")


if __name__ == "__main__":
    main()
