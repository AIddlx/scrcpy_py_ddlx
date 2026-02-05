#!/usr/bin/env python3
"""
Simple MCP stdio Server for Claude Code Integration

This server communicates with Claude Code via stdin/stdout using the MCP protocol.

Usage:
1. Add to Claude Code config
2. Restart Claude Code
3. Start using tools in chat
"""

import json
import sys
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

# Import with error handling
import_error = None
try:
    from scrcpy_py_ddlx import ScrcpyClient, ClientConfig
    from scrcpy_py_ddlx.core.keycode import AndroidKeyCode
except ImportError as e:
    import_error = str(e)

if import_error:
    # Print diagnostic info to stderr
    print(f"[MCP ERROR] Failed to import scrcpy_py_ddlx: {import_error}", file=sys.stderr, flush=True)
    print(f"[MCP ERROR] Python: {sys.executable}", file=sys.stderr, flush=True)
    print(f"[MCP ERROR] Install with: pip install -e {Path(__file__).parent}", file=sys.stderr, flush=True)
    sys.exit(1)

# Get project root for logs
project_root = Path(__file__).parent

# Setup logging
logs_dir = project_root / "logs"
logs_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(logs_dir / "mcp_stdio.log", encoding='utf-8'),
        logging.StreamHandler(sys.stderr)  # Log to stderr so stdout is clean for MCP
    ]
)
logger = logging.getLogger(__name__)


class SimpleMCPServer:
    """Simplified MCP Server for scrcpy"""

    def __init__(self):
        self.client: Optional[ScrcpyClient] = None
        self._connected = False

    def get_tools(self) -> List[Dict]:
        """Return available tools"""
        return [
            {
                "name": "connect",
                "description": "Connect to Android device via ADB",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "audio": {
                            "type": "boolean",
                            "description": "Enable audio streaming",
                            "default": False
                        }
                    }
                }
            },
            {
                "name": "disconnect",
                "description": "Disconnect from device",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "get_state",
                "description": "Get device state (name, size, connection)",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "screenshot",
                "description": "Capture screen to file",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Output filename (default: screenshot.png)"
                        }
                    }
                }
            },
            {
                "name": "list_apps",
                "description": "List all installed applications",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "system_apps": {
                            "type": "boolean",
                            "description": "Include system apps",
                            "default": False
                        }
                    }
                }
            },
            {
                "name": "tap",
                "description": "Tap at screen position",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"}
                    },
                    "required": ["x", "y"]
                }
            },
            {
                "name": "swipe",
                "description": "Swipe from (x1,y1) to (x2,y2)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x1": {"type": "integer"},
                        "y1": {"type": "integer"},
                        "x2": {"type": "integer"},
                        "y2": {"type": "integer"},
                        "duration_ms": {
                            "type": "integer",
                            "default": 300
                        }
                    },
                    "required": ["x1", "y1", "x2", "y2"]
                }
            },
            {
                "name": "home",
                "description": "Press home button",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "back",
                "description": "Press back button",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "input_text",
                "description": "Input text as if typed on keyboard",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Text to input"
                        }
                    },
                    "required": ["text"]
                }
            },
            {
                "name": "volume_up",
                "description": "Volume up",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "volume_down",
                "description": "Volume down",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "record_audio",
                "description": "Record audio from device",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Output filename (default: recording.wav)"
                        },
                        "duration": {
                            "type": "number",
                            "description": "Recording duration in seconds (default: 5.0)"
                        },
                        "format": {
                            "type": "string",
                            "description": "Audio format: wav, opus, or mp3 (default: wav)"
                        }
                    },
                    "required": ["filename"]
                }
            },
            {
                "name": "stop_audio_recording",
                "description": "Stop audio recording and save file",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "is_recording_audio",
                "description": "Check if audio recording is in progress",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "get_recording_duration",
                "description": "Get current audio recording duration in seconds",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "long_press",
                "description": "Long press at a specific position on the screen",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer", "description": "X coordinate in pixels"},
                        "y": {"type": "integer", "description": "Y coordinate in pixels"},
                        "duration_ms": {"type": "integer", "default": 500}
                    },
                    "required": ["x", "y"]
                }
            },
            {
                "name": "press_key",
                "description": "Press a hardware or software key",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key_code": {"type": "string", "description": "Key code (e.g., 'HOME', 'BACK', 'ENTER')"}
                    },
                    "required": ["key_code"]
                }
            },
            {
                "name": "recent_apps",
                "description": "Open recent apps (overview) screen",
                "inputSchema": {"type": "object", "properties": {}}
            },
            {
                "name": "wake_up",
                "description": "Wake up the device screen",
                "inputSchema": {"type": "object", "properties": {}}
            },
            {
                "name": "open_app",
                "description": "Launch an application by package name",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "package": {"type": "string", "description": "Package name"}
                    },
                    "required": ["package"]
                }
            },
            {
                "name": "get_clipboard",
                "description": "Get the current clipboard content from the device",
                "inputSchema": {"type": "object", "properties": {}}
            },
            {
                "name": "set_clipboard",
                "description": "Set the clipboard content on the device",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"}
                    },
                    "required": ["text"]
                }
            },
            {"name": "menu", "description": "Press menu button", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "enter", "description": "Press enter key", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "tab", "description": "Press tab key", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "escape", "description": "Press escape key", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "dpad_up", "description": "D-pad up", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "dpad_down", "description": "D-pad down", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "dpad_left", "description": "D-pad left", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "dpad_right", "description": "D-pad right", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "dpad_center", "description": "D-pad center", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "expand_notification_panel", "description": "Expand notification panel", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "expand_settings_panel", "description": "Expand settings panel", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "collapse_panels", "description": "Collapse all panels", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "turn_screen_on", "description": "Turn screen on", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "turn_screen_off", "description": "Turn screen off", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "rotate_device", "description": "Rotate device", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "reset_video", "description": "Reset video stream", "inputSchema": {"type": "object", "properties": {}}},
            {
                "name": "screenshot_device",
                "description": "Screenshot from device server (full process)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string"}
                    }
                }
            },
            {
                "name": "screenshot_standalone",
                "description": "Standalone screenshot (connects temporarily)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string"}
                    }
                }
            },
        ]

    def call_tool(self, name: str, arguments: Dict) -> Dict[str, Any]:
        """Call a tool"""
        try:
            if name == "connect":
                audio = arguments.get("audio", False)
                # Use absolute path to scrcpy-server
                server_path = project_root / "scrcpy-server"
                config = ClientConfig(
                    show_window=False,
                    control=True,
                    audio=audio,
                    server_jar=str(server_path)
                )
                self.client = ScrcpyClient(config)
                success = self.client.connect()
                self._connected = success
                return {
                    "content": [{
                        "type": "text",
                        "text": f"{'Connected' if success else 'Failed to connect'} to device"
                    }]
                }

            elif name == "disconnect":
                if self.client:
                    self.client.disconnect()
                    self._connected = False
                    self.client = None
                return {
                    "content": [{"type": "text", "text": "Disconnected"}]
                }

            elif name == "get_state":
                if not self._connected or not self.client:
                    return {
                        "content": [{"type": "text", "text": "Not connected"}]
                    }
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Device: {self.client.state.device_name}, "
                               f"Size: {self.client.state.device_size[0]}x{self.client.state.device_size[1]}"
                    }]
                }

            elif name == "screenshot":
                if not self._connected or not self.client:
                    return {
                        "content": [{"type": "text", "text": "Not connected"}]
                    }
                filename = arguments.get("filename", "screenshot.png")
                self.client.screenshot(filename)
                return {
                    "content": [{"type": "text", "text": f"Screenshot saved to {filename}"}]
                }

            elif name == "list_apps":
                if not self._connected or not self.client:
                    return {
                        "content": [{"type": "text", "text": "Not connected"}]
                    }
                system_apps = arguments.get("system_apps", False)
                apps = self.client.list_apps()
                if system_apps:
                    filtered_apps = apps
                else:
                    filtered_apps = [app for app in apps if not app["system"]]

                result_text = f"Found {len(filtered_apps)} apps\n\n"
                for i, app in enumerate(filtered_apps[:20], 1):
                    result_text += f"{i}. {app['name']} ({app['package']})\n"
                if len(filtered_apps) > 20:
                    result_text += f"... and {len(filtered_apps) - 20} more"

                return {
                    "content": [{"type": "text", "text": result_text}]
                }

            elif name == "tap":
                if not self._connected or not self.client:
                    return {
                        "content": [{"type": "text", "text": "Not connected"}]
                    }
                x = arguments.get("x")
                y = arguments.get("y")
                self.client.tap(x, y)
                return {
                    "content": [{"type": "text", "text": f"Tapped at ({x}, {y})"}]
                }

            elif name == "swipe":
                if not self._connected or not self.client:
                    return {
                        "content": [{"type": "text", "text": "Not connected"}]
                    }
                x1 = arguments.get("x1")
                y1 = arguments.get("y1")
                x2 = arguments.get("x2")
                y2 = arguments.get("y2")
                duration_ms = arguments.get("duration_ms", 300)
                self.client.swipe(x1, y1, x2, y2, duration_ms)
                return {
                    "content": [{"type": "text", "text": f"Swiped from ({x1},{y1}) to ({x2},{y2})"}]
                }

            elif name == "home":
                if not self._connected or not self.client:
                    return {
                        "content": [{"type": "text", "text": "Not connected"}]
                    }
                self.client.home()
                return {
                    "content": [{"type": "text", "text": "Pressed home button"}]
                }

            elif name == "back":
                if not self._connected or not self.client:
                    return {
                        "content": [{"type": "text", "text": "Not connected"}]
                    }
                self.client.back()
                return {
                    "content": [{"type": "text", "text": "Pressed back button"}]
                }

            elif name == "input_text":
                if not self._connected or not self.client:
                    return {
                        "content": [{"type": "text", "text": "Not connected"}]
                    }
                text = arguments.get("text")
                self.client.inject_text(text)
                return {
                    "content": [{"type": "text", "text": f"Input text: {text}"}]
                }

            elif name == "volume_up":
                if not self._connected or not self.client:
                    return {
                        "content": [{"type": "text", "text": "Not connected"}]
                    }
                self.client.volume_up()
                return {
                    "content": [{"type": "text", "text": "Volume up"}]
                }

            elif name == "volume_down":
                if not self._connected or not self.client:
                    return {
                        "content": [{"type": "text", "text": "Not connected"}]
                    }
                self.client.volume_down()
                return {
                    "content": [{"type": "text", "text": "Volume down"}]
                }

            elif name == "record_audio":
                if not self._connected or not self.client:
                    return {
                        "content": [{"type": "text", "text": "Not connected"}]
                    }
                filename = arguments.get("filename")
                duration = arguments.get("duration", 5.0)
                format = arguments.get("format", "wav")

                # Map format to auto_convert parameter
                format_map = {"wav": None, "opus": "opus", "mp3": "mp3"}
                auto_convert = format_map.get(format)

                # Start recording in background with auto-close via max_duration
                success = self.client.start_audio_recording(
                    filename,
                    max_duration=duration,
                    play_while_recording=True,  # Play sound while recording
                    auto_convert_to=auto_convert
                )
                if success:
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"Recording audio to {filename} for {duration}s\nPlaying sound... will auto-save when complete."
                        }]
                    }
                else:
                    return {
                        "content": [{"type": "text", "text": "Failed to start recording"}],
                        "isError": True
                    }

            elif name == "stop_audio_recording":
                if not self._connected or not self.client:
                    return {
                        "content": [{"type": "text", "text": "Not connected"}]
                    }
                self.client.stop_audio_recording()
                return {
                    "content": [{"type": "text", "text": "Audio recording stopped"}]
                }

            elif name == "is_recording_audio":
                if not self._connected or not self.client:
                    return {
                        "content": [{"type": "text", "text": "Not connected"}]
                    }
                is_recording = self.client.is_recording_audio()
                return {
                    "content": [{"type": "text", "text": f"Recording: {is_recording}"}]
                }

            elif name == "get_recording_duration":
                if not self._connected or not self.client:
                    return {
                        "content": [{"type": "text", "text": "Not connected"}]
                    }
                duration = self.client.get_recording_duration()
                return {
                    "content": [{"type": "text", "text": f"Duration: {duration:.2f}s"}]
                }

            elif name == "long_press":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                x = arguments.get("x")
                y = arguments.get("y")
                duration_ms = arguments.get("duration_ms", 500)
                self.client.long_press(x, y, duration_ms)
                return {"content": [{"type": "text", "text": f"Long pressed at ({x}, {y})"}]}

            elif name == "press_key":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                key_code = arguments.get("key_code").upper()
                key_map = {
                    "HOME": AndroidKeyCode.HOME, "BACK": AndroidKeyCode.BACK,
                    "ENTER": AndroidKeyCode.ENTER, "VOLUME_UP": AndroidKeyCode.VOLUME_UP,
                    "VOLUME_DOWN": AndroidKeyCode.VOLUME_DOWN, "APP_SWITCH": AndroidKeyCode.APP_SWITCH,
                    "MENU": AndroidKeyCode.MENU, "TAB": AndroidKeyCode.TAB,
                    "ESCAPE": AndroidKeyCode.ESCAPE, "DPAD_UP": AndroidKeyCode.DPAD_UP,
                    "DPAD_DOWN": AndroidKeyCode.DPAD_DOWN, "DPAD_LEFT": AndroidKeyCode.DPAD_LEFT,
                    "DPAD_RIGHT": AndroidKeyCode.DPAD_RIGHT, "DPAD_CENTER": AndroidKeyCode.DPAD_CENTER,
                }
                code = key_map.get(key_code)
                if code:
                    self.client.inject_keycode(code.value)
                return {"content": [{"type": "text", "text": f"Pressed key: {key_code}"}]}

            elif name == "recent_apps":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.app_switch()
                return {"content": [{"type": "text", "text": "Opened recent apps"}]}

            elif name == "wake_up":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.set_display_power(True)
                return {"content": [{"type": "text", "text": "Woke up device"}]}

            elif name == "open_app":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                package = arguments.get("package")
                self.client.start_app(package)
                return {"content": [{"type": "text", "text": f"Launched {package}"}]}

            elif name == "get_clipboard":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                text = self.client.get_clipboard()
                return {"content": [{"type": "text", "text": f"Clipboard: {text}"}]}

            elif name == "set_clipboard":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                text = arguments.get("text")
                self.client.set_clipboard(text)
                return {"content": [{"type": "text", "text": f"Clipboard set"}]}

            elif name == "menu":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.menu()
                return {"content": [{"type": "text", "text": "Pressed menu"}]}

            elif name == "enter":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.enter()
                return {"content": [{"type": "text", "text": "Pressed enter"}]}

            elif name == "tab":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.tab()
                return {"content": [{"type": "text", "text": "Pressed tab"}]}

            elif name == "escape":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.escape()
                return {"content": [{"type": "text", "text": "Pressed escape"}]}

            elif name == "dpad_up":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.dpad_up()
                return {"content": [{"type": "text", "text": "D-pad up"}]}

            elif name == "dpad_down":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.dpad_down()
                return {"content": [{"type": "text", "text": "D-pad down"}]}

            elif name == "dpad_left":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.dpad_left()
                return {"content": [{"type": "text", "text": "D-pad left"}]}

            elif name == "dpad_right":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.dpad_right()
                return {"content": [{"type": "text", "text": "D-pad right"}]}

            elif name == "dpad_center":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.dpad_center()
                return {"content": [{"type": "text", "text": "D-pad center"}]}

            elif name == "expand_notification_panel":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.expand_notification_panel()
                return {"content": [{"type": "text", "text": "Expanded notification panel"}]}

            elif name == "expand_settings_panel":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.expand_settings_panel()
                return {"content": [{"type": "text", "text": "Expanded settings panel"}]}

            elif name == "collapse_panels":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.collapse_panels()
                return {"content": [{"type": "text", "text": "Collapsed panels"}]}

            elif name == "turn_screen_on":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.turn_screen_on()
                return {"content": [{"type": "text", "text": "Screen turned on"}]}

            elif name == "turn_screen_off":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.turn_screen_off()
                return {"content": [{"type": "text", "text": "Screen turned off"}]}

            elif name == "rotate_device":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.rotate_device()
                return {"content": [{"type": "text", "text": "Device rotated"}]}

            elif name == "reset_video":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                self.client.reset_video()
                return {"content": [{"type": "text", "text": "Video reset"}]}

            elif name == "screenshot_device":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                filename = arguments.get("filename")
                self.client.screenshot_device(filename)
                return {"content": [{"type": "text", "text": f"Screenshot saved to {filename}"}]}

            elif name == "screenshot_standalone":
                if not self._connected or not self.client:
                    return {"content": [{"type": "text", "text": "Not connected"}]}
                filename = arguments.get("filename")
                self.client.screenshot_standalone(filename)
                return {"content": [{"type": "text", "text": f"Screenshot saved to {filename}"}]}

            else:
                return {
                    "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
                    "isError": True
                }

        except Exception as e:
            logger.error(f"Tool call error ({name}): {e}")
            return {
                "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                "isError": True
            }


def main():
    """MCP stdio server main loop"""
    server = SimpleMCPServer()

    # Server waits for initialize from client
    logger.info("MCP Server waiting for client...")

    # Message loop
    for line in sys.stdin:
        try:
            if not line.strip():
                continue

            message = json.loads(line)

            if message.get("method") == "initialize":
                # Client initialization - respond with capabilities
                response = {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {
                            "name": "scrcpy-py-ddlx",
                            "version": "0.1.0"
                        },
                        "capabilities": {
                            "tools": {}
                        }
                    }
                }
                print(json.dumps(response), flush=True)
                logger.info("MCP Server initialized")

            elif message.get("method") == "tools/list":
                # List tools
                tools = server.get_tools()
                response = {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": {"tools": tools}
                }
                print(json.dumps(response), flush=True)
                logger.info(f"Listed {len(tools)} tools")

            elif message.get("method") == "tools/call":
                # Call tool
                call = message.get("params", {})
                name = call.get("name")
                arguments = call.get("arguments", {})

                logger.info(f"Tool call: {name} with {arguments}")
                result = server.call_tool(name, arguments)

                response = {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": result
                }
                print(json.dumps(response), flush=True)

        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            continue
        except Exception as e:
            logger.error(f"Message loop error: {e}")
            continue


if __name__ == "__main__":
    main()
