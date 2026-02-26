#!/usr/bin/env python3
"""
Scrcpy HTTP MCP Server

无状态的 HTTP MCP 服务器，支持通过 URL 配置 Claude Code 客户端。
使用标准 HTTP POST (JSON-RPC)，无需 SSE。

配置方式:
{
  "mcpServers": {
    "scrcpy-http": {
      "url": "http://localhost:3359/mcp"
    }
  }
}
"""

import sys
import json
import logging
import asyncio
import threading
import subprocess
import urllib.request
import os
import socket
import select
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# 尝试导入依赖
try:
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    import uvicorn
    STARLETTE_AVAILABLE = True
except ImportError:
    STARLETTE_AVAILABLE = False

# 配置日志
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

timestamp = datetime.now()
log_file = log_dir / f"scrcpy_http_mcp_{timestamp.strftime('%Y%m%d_%H%M%S')}.log"

# 创建日志格式
log_format = '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'


class ConsoleFilter(logging.Filter):
    """
    控制台日志过滤器

    - 对于音频模块：只显示 WARNING 及以上
    - 对于其他模块：显示 INFO 及以上
    """
    def filter(self, record):
        # 音频模块只显示 WARNING+
        if 'audio' in record.name.lower():
            return record.levelno >= logging.WARNING
        # 其他模块显示 INFO+
        return record.levelno >= logging.INFO


# 文件处理器：保存全部日志（DEBUG 及以上）
file_handler = logging.FileHandler(log_file, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(log_format))
# 文件不使用过滤器，保存所有日志

# 控制台处理器：使用过滤器控制显示
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG)  # 设为 DEBUG，让过滤器决定
console_handler.setFormatter(logging.Formatter(log_format))
console_handler.addFilter(ConsoleFilter())  # 添加自定义过滤器

# 配置根日志器
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)  # 根级别设为 DEBUG，允许所有日志通过
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)

# 启用 scrcpy_py_ddlx 模块的日志
# 不在这里设置级别，让日志传播到根日志器处理
scrcpy_logger = logging.getLogger('scrcpy_py_ddlx')
# 不要设置级别，使用默认的 NOTSET，让日志传播到根日志器

# 默认音频配置（可通过 --audio 命令行参数修改）
DEFAULT_AUDIO_ENABLED = False
DEFAULT_AUDIO_DUP = False

# MCP 协议版本和服务器信息
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {
    "name": "scrcpy-http-mcp-server",
    "version": "2.0.0",  # 支持网络模式和更多参数
    "protocolVersion": MCP_PROTOCOL_VERSION
}

# 统一坐标系统描述（用于所有涉及坐标的工具）
COORDINATE_SYSTEM = """
COORDINATE SYSTEM:
- Origin (0, 0): Top-left corner of the screen (regardless of rotation)
- X axis: Horizontal, increases from left to right (0 to width-1)
- Y axis: Vertical, increases from top to bottom (0 to height-1)
- IMPORTANT: width and height change with device rotation:

PORTRAIT (竖屏):  width < height
  Example: 1080x2400 screen (width=1080, height=2400)
  - Top-left: (0, 0)
  - Top-right: (1079, 0)
  - Bottom-left: (0, 2399)
  - Bottom-right: (1079, 2399)
  - Center: (540, 1200)

LANDSCAPE (横屏):  width > height
  Example: 2400x1080 screen (width=2400, height=1080)
  - Top-left: (0, 0)
  - Top-right: (2399, 0)
  - Bottom-left: (0, 1079)
  - Bottom-right: (2399, 1079)
  - Center: (1200, 540)

NOTE: Call get_state() to get current width, height, and orientation before using coordinates.
"""

# Scrcpy MCP 工具定义（47+ 个工具）
TOOLS = [
    # 连接管理
    {
        "name": "connect",
        "description": "Connect to an Android device. RECOMMENDED: Use USB with adb_tunnel mode (default, most secure). For wireless, first run push_server(stay_alive=True) via USB, then use network mode. SECURITY: ADB WiFi (5555) is insecure - do NOT use in public networks. Network mode should only be used in trusted private networks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                # 连接模式
                "connection_mode": {
                    "type": "string",
                    "enum": ["adb_tunnel", "network"],
                    "default": "adb_tunnel",
                    "description": "Connection mode: 'adb_tunnel' (default, secure, uses USB) or 'network' (direct TCP+UDP, requires server running on device)"
                },
                "device_id": {
                    "type": "string",
                    "description": "Device serial (USB) or IP address (network mode). For network mode, use just IP like '192.168.1.100'"
                },
                # 网络模式参数
                "control_port": {"type": "integer", "default": 27184, "description": "TCP control port (network mode, default: 27184)"},
                "video_port": {"type": "integer", "default": 27185, "description": "UDP video port (network mode, default: 27185)"},
                "audio_port": {"type": "integer", "default": 27186, "description": "UDP audio port (network mode, default: 27186)"},
                "file_port": {"type": "integer", "default": 27187, "description": "TCP file transfer port (network mode, default: 27187)"},
                "stay_alive": {"type": "boolean", "default": False, "description": "Stay-alive mode: server keeps running after disconnect (network mode)"},
                "wake_server": {"type": "boolean", "default": True, "description": "Use UDP wake packet to connect to sleeping server (network mode, stay-alive)"},
                # 媒体参数
                "video": {"type": "boolean", "default": True, "description": "Enable video streaming"},
                "audio": {"type": "boolean", "default": False, "description": "Enable audio streaming"},
                "codec": {
                    "type": "string",
                    "enum": ["auto", "h264", "h265", "av1"],
                    "default": "auto",
                    "description": "Video codec: auto (detect), h264, h265, or av1"
                },
                "bitrate": {"type": "integer", "default": 8000000, "description": "Video bitrate in bps (default: 8 Mbps)"},
                "max_fps": {"type": "integer", "default": 60, "description": "Max frame rate (30, 60, 90, 120)"},
                "bitrate_mode": {
                    "type": "string",
                    "enum": ["vbr", "cbr"],
                    "default": "vbr",
                    "description": "Bitrate mode: vbr (variable quality) or cbr (constant bandwidth)"
                },
                "i_frame_interval": {"type": "number", "default": 10.0, "description": "I-frame (keyframe) interval in seconds. Lower = faster recovery but more bandwidth"},
                # FEC 参数
                "fec_enabled": {"type": "boolean", "default": False, "description": "Enable FEC for both video and audio (network mode)"},
                "video_fec_enabled": {"type": "boolean", "default": False, "description": "Enable FEC for video only (network mode)"},
                "audio_fec_enabled": {"type": "boolean", "default": False, "description": "Enable FEC for audio only (network mode)"},
                "fec_group_size": {"type": "integer", "default": 4, "description": "FEC data packets per group (K), default: 4"},
                "fec_parity_count": {"type": "integer", "default": 1, "description": "FEC parity packets per group (M), default: 1"},
                # 兼容旧参数
                "tcpip": {"type": "boolean", "default": False, "description": "[DEPRECATED - Insecure] Enable TCP/IP wireless mode via ADB. Use network mode instead."},
                "stay_awake": {"type": "boolean", "default": True, "description": "Keep device screen awake"}
            }
        }
    },
    {
        "name": "disconnect",
        "description": "Disconnect from the device",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "set_video",
        "description": "Enable or disable video streaming. Requires reconnection to take effect.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "Enable (true) or disable (false) video streaming"}
            },
            "required": ["enabled"]
        }
    },
    {
        "name": "set_audio",
        "description": "Enable or disable audio streaming. Requires reconnection to take effect.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "Enable (true) or disable (false) audio streaming"}
            },
            "required": ["enabled"]
        }
    },
    {
        "name": "get_state",
        "description": "Get current device state including width, height, orientation (portrait/landscape), and connection status. Call this before using any coordinate-based tools to get current screen dimensions. IMPORTANT: If this returns 'Not connected', first call discover_devices() or list_devices() to find available devices.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "list_devices",
        "description": "List all ADB connected devices. If empty, call discover_devices() to scan the local network for wireless devices.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_device_ip",
        "description": "Get the WiFi IP address of a connected device. The device must be connected via ADB first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "serial": {"type": "string", "description": "Device serial number (use list_devices to find it)"}
            },
            "required": ["serial"]
        }
    },
    {
        "name": "enable_wireless",
        "description": "⚠️ NOT RECOMMENDED - Enable ADB WiFi (5555) which is INSECURE (plaintext, anyone can connect). RECOMMENDED ALTERNATIVE: Use USB for adb_tunnel mode, or use network mode (push_server first). Only use this in trusted private networks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "serial": {"type": "string", "description": "Device serial number (USB device)"},
                "port": {"type": "integer", "description": "TCP port (default: 5555)", "default": 5555}
            },
            "required": ["serial"]
        }
    },
    {
        "name": "connect_wireless",
        "description": "⚠️ NOT RECOMMENDED - Connect via ADB WiFi (5555) which is INSECURE. RECOMMENDED: For wireless, use network mode - first push_server(stay_alive=True) via USB, then connect(connection_mode='network', device_id='IP').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ip": {"type": "string", "description": "Device IP address"},
                "port": {"type": "integer", "description": "TCP port (default: 5555)", "default": 5555}
            },
            "required": ["ip"]
        }
    },
    {
        "name": "disconnect_wireless",
        "description": "Disconnect from an ADB WiFi device (5555 port)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ip": {"type": "string", "description": "Device IP address"},
                "port": {"type": "integer", "description": "TCP port (default: 5555)", "default": 5555}
            },
            "required": ["ip"]
        }
    },
    {
        "name": "push_server",
        "description": "[DEPRECATED] Use push_server_onetime or push_server_persistent instead. Deploy scrcpy server for network mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                # 基础参数
                "device_id": {"type": "string", "description": "Device serial number (USB device, uses first device if not specified)"},
                "server_path": {"type": "string", "description": "Path to scrcpy-server file (default: ./scrcpy-server)", "default": "./scrcpy-server"},
                "push": {"type": "boolean", "description": "Push server APK to device", "default": True},
                "start": {"type": "boolean", "description": "Start server after push with nohup", "default": True},
                "kill_old": {"type": "boolean", "description": "Kill old server before starting new one", "default": True},
                "reuse": {"type": "boolean", "description": "Reuse existing server if running, skip push/start", "default": False},
                "stay_alive": {"type": "boolean", "description": "Start in stay-alive mode for persistent UDP discovery and hot-reconnect", "default": True},
                # 端口配置
                "control_port": {"type": "integer", "description": "TCP control port", "default": 27184},
                "video_port": {"type": "integer", "description": "UDP video port", "default": 27185},
                "audio_port": {"type": "integer", "description": "UDP audio port", "default": 27186},
                "file_port": {"type": "integer", "description": "TCP file transfer port", "default": 27187},
                # 视频参数
                "video_codec": {
                    "type": "string",
                    "enum": ["auto", "h264", "h265", "av1"],
                    "default": "auto",
                    "description": "Video codec: auto (detect best), h264 (compatible), h265 (efficient), av1 (newest)"
                },
                "video_bitrate": {"type": "integer", "default": 8000000, "description": "Video bitrate in bps (default: 8 Mbps)"},
                "max_fps": {"type": "integer", "default": 60, "description": "Max frame rate (30, 60, 90, 120)"},
                "bitrate_mode": {
                    "type": "string",
                    "enum": ["vbr", "cbr"],
                    "default": "vbr",
                    "description": "Bitrate mode: vbr (variable quality) or cbr (constant bandwidth)"
                },
                "i_frame_interval": {"type": "number", "default": 2.0, "description": "I-frame interval in seconds. Lower = faster recovery but more bandwidth (default=2.0 for network mode)"},
                # 音频参数
                "audio": {"type": "boolean", "default": False, "description": "Enable audio streaming"},
                # FEC 参数
                "fec_enabled": {"type": "boolean", "default": False, "description": "Enable FEC for both video and audio"},
                "video_fec_enabled": {"type": "boolean", "default": False, "description": "Enable FEC for video only (recommended for bandwidth)"},
                "audio_fec_enabled": {"type": "boolean", "default": False, "description": "Enable FEC for audio only"},
                "fec_group_size": {"type": "integer", "default": 4, "description": "FEC data packets per group (K), default: 4"},
                "fec_parity_count": {"type": "integer", "default": 1, "description": "FEC parity packets per group (M), default: 1"}
            }
        }
    },
    # 网络模式专用推送工具
    {
        "name": "push_server_onetime",
        "description": "Push and start scrcpy server for ONE-TIME network connection. REQUIRES USB. Server exits after disconnect. Set auto_connect=true to connect immediately after push. DEFAULT: control-only (no video/audio). Set video=true for preview/screenshot, audio=true for recording.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device serial (optional, uses first USB device)"},
                "video": {"type": "boolean", "default": False, "description": "Enable video (for preview/screenshot)"},
                "audio": {"type": "boolean", "default": False, "description": "Enable audio (for recording)"},
                "audio_dup": {"type": "boolean", "default": False, "description": "Duplicate audio: play on both device and computer (Android 11+)"},
                "video_codec": {"type": "string", "enum": ["auto", "h264", "h265", "av1"], "default": "auto"},
                "video_bitrate": {"type": "integer", "default": 8000000},
                "max_fps": {"type": "integer", "default": 60},
                "auto_connect": {"type": "boolean", "default": False, "description": "Auto connect via network after push (one-step flow)"}
            }
        }
    },
    {
        "name": "push_server_persistent",
        "description": "Push and start scrcpy server for PERSISTENT network connection. REQUIRES USB. Server keeps running after disconnect, supports hot-reconnect. DEFAULT: control-only (no video/audio). Set video=true for preview/screenshot, audio=true for recording. Flow: 1) USB connect, 2) push_server_persistent(video=true,audio=true), 3) unplug USB, 4) connect/disconnect anytime. Server runs until stop_server() or device reboot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device serial (optional, uses first USB device)"},
                "video": {"type": "boolean", "default": False, "description": "Enable video (for preview/screenshot)"},
                "audio": {"type": "boolean", "default": False, "description": "Enable audio (for recording)"},
                "audio_dup": {"type": "boolean", "default": False, "description": "Duplicate audio: play on both device and computer (Android 11+)"},
                "video_codec": {"type": "string", "enum": ["auto", "h264", "h265", "av1"], "default": "auto"},
                "video_bitrate": {"type": "integer", "default": 8000000},
                "max_fps": {"type": "integer", "default": 60},
                "max_connections": {"type": "integer", "default": -1, "description": "Max connections before exit (-1 = unlimited)"},
                "fec_enabled": {"type": "boolean", "default": False, "description": "Enable FEC for unstable networks"}
            }
        }
    },
    {
        "name": "stop_server",
        "description": "Stop scrcpy server on device (for persistent mode cleanup). Use when you want to completely shut down the server.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device serial (optional)"}
            }
        }
    },
    {
        "name": "restart_adb",
        "description": "Restart ADB server. Use when ADB connection is stuck or device not detected. Executes: adb kill-server && adb start-server",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "discover_devices",
        "description": "Auto-discover devices on local network. Scans for: 1) ADB connected devices (USB/WiFi), 2) Running scrcpy servers (UDP broadcast on port 27183). RECOMMENDED: Use USB when possible. For network mode, first push_server via USB, then discover and connect.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout": {"type": "integer", "description": "Scan timeout per IP in milliseconds (default: 500)", "default": 500}
            }
        }
    },
    # 屏幕
    {
        "name": "screenshot",
        "description": "Capture screenshot from device. MODES: 1) video=true (default): Fast screenshot from video stream (~16ms). 2) video=false (low power): Screenshot via ADB screencap (~500ms), no video stream needed. Recording requires audio=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["jpg", "png"],
                    "default": "jpg",
                    "description": "Image format: jpg (smaller, lossy) or png (larger, lossless)"
                },
                "quality": {
                    "type": "integer",
                    "default": 80,
                    "minimum": 1,
                    "maximum": 100,
                    "description": "JPEG quality (1-100, only for jpg format). 80=default (good balance), 95=high quality, 40=minimum recommended"
                }
            }
        }
    },
    {
        "name": "screenshot_device",
        "description": "Take a screenshot from the device server (full process)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Optional filename to save (PNG format)"}
            }
        }
    },
    {
        "name": "screenshot_standalone",
        "description": "Take a standalone screenshot (connects temporarily)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Optional filename to save (PNG format)"}
            }
        }
    },
    # 预览窗口 (分离进程)
    {
        "name": "start_preview",
        "description": "Start a real-time preview window in a separate process. Requires video=true and device must be connected. The preview runs independently and shows live screen.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "stop_preview",
        "description": "Stop the preview window. Connection remains active.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_preview_status",
        "description": "Check if preview window is running",
        "inputSchema": {"type": "object", "properties": {}}
    },
    # 剪贴板
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
                "text": {"type": "string", "description": "Text to set in clipboard"}
            },
            "required": ["text"]
        }
    },
    # 应用
    {
        "name": "list_apps",
        "description": "List all installed applications on the device",
        "inputSchema": {
            "type": "object",
            "properties": {
                "system_apps": {"type": "boolean", "description": "Include system applications", "default": False},
                "timeout": {"type": "number", "description": "Timeout in seconds", "default": 30.0}
            }
        }
    },
    {
        "name": "open_app",
        "description": "Launch an application by package name",
        "inputSchema": {
            "type": "object",
            "properties": {
                "package": {"type": "string", "description": "Package name (e.g., 'com.android.settings')"}
            },
            "required": ["package"]
        }
    },
    # 触控
    {
        "name": "tap",
        "description": "Tap at a specific position on the screen. " + COORDINATE_SYSTEM,
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate in pixels (0 to width-1, left to right)"},
                "y": {"type": "integer", "description": "Y coordinate in pixels (0 to height-1, top to bottom)"}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "long_press",
        "description": "Long press at a specific position on the screen. " + COORDINATE_SYSTEM,
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate in pixels (0 to width-1, left to right)"},
                "y": {"type": "integer", "description": "Y coordinate in pixels (0 to height-1, top to bottom)"},
                "duration_ms": {"type": "integer", "description": "Press duration in milliseconds", "default": 500}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "swipe",
        "description": "Swipe from one position to another. " + COORDINATE_SYSTEM,
        "inputSchema": {
            "type": "object",
            "properties": {
                "x1": {"type": "integer", "description": "Start X coordinate in pixels (0 to width-1, left to right)"},
                "y1": {"type": "integer", "description": "Start Y coordinate in pixels (0 to height-1, top to bottom)"},
                "x2": {"type": "integer", "description": "End X coordinate in pixels (0 to width-1, left to right)"},
                "y2": {"type": "integer", "description": "End Y coordinate in pixels (0 to height-1, top to bottom)"},
                "duration_ms": {"type": "integer", "description": "Swipe duration in milliseconds", "default": 300}
            },
            "required": ["x1", "y1", "x2", "y2"]
        }
    },
    # 键盘
    {
        "name": "press_key",
        "description": "Press a hardware or software key",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key_code": {"type": "string", "description": "Key code (e.g., 'HOME', 'BACK', 'ENTER', 'VOLUME_UP')"}
            },
            "required": ["key_code"]
        }
    },
    {
        "name": "input_text",
        "description": "Input text as if typed on the keyboard",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to input"}
            },
            "required": ["text"]
        }
    },
    # 导航
    {
        "name": "back",
        "description": "Press the back button",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "home",
        "description": "Press the home button",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "recent_apps",
        "description": "Open recent apps (overview) screen",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "menu",
        "description": "Press the menu button",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "enter",
        "description": "Press the enter key",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "tab",
        "description": "Press the tab key",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "escape",
        "description": "Press the escape key",
        "inputSchema": {"type": "object", "properties": {}}
    },
    # D-Pad
    {
        "name": "dpad_up",
        "description": "Press D-pad up button",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "dpad_down",
        "description": "Press D-pad down button",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "dpad_left",
        "description": "Press D-pad left button",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "dpad_right",
        "description": "Press D-pad right button",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "dpad_center",
        "description": "Press D-pad center button",
        "inputSchema": {"type": "object", "properties": {}}
    },
    # 面板
    {
        "name": "expand_notification_panel",
        "description": "Expand the notification panel",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "expand_settings_panel",
        "description": "Expand the settings panel",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "collapse_panels",
        "description": "Collapse all panels (notification/settings)",
        "inputSchema": {"type": "object", "properties": {}}
    },
    # 显示
    {
        "name": "turn_screen_on",
        "description": "Turn the screen on",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "turn_screen_off",
        "description": "Turn the screen off",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "rotate_device",
        "description": "Rotate the device",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "reset_video",
        "description": "Reset video stream (useful if video freezes)",
        "inputSchema": {"type": "object", "properties": {}}
    },
    # 音量
    {
        "name": "volume_up",
        "description": "Press volume up button",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "volume_down",
        "description": "Press volume down button",
        "inputSchema": {"type": "object", "properties": {}}
    },
    # 电源
    {
        "name": "wake_up",
        "description": "Wake up the device screen",
        "inputSchema": {"type": "object", "properties": {}}
    },
    # 录 音
    {
        "name": "record_audio",
        "description": "Record audio to file for a specific duration. Three modes: (1) format=auto or omit -> passthrough original OPUS to .ogg (best quality), (2) format=wav -> decode to PCM WAV, (3) format=opus/mp3 -> decode and re-encode",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Output filename. Extension determines format: .wav, .opus, .mp3, .ogg"},
                "duration": {"type": "number", "description": "Recording duration in seconds"},
                "format": {"type": "string", "description": "Output format: 'auto' (passthrough), 'wav' (PCM), 'opus' (re-encode), 'mp3' (re-encode)", "enum": ["auto", "wav", "opus", "mp3"]}
            },
            "required": ["filename", "duration"]
        }
    },
    {
        "name": "stop_audio_recording",
        "description": "Stop audio recording and save the file",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "is_recording_audio",
        "description": "Check if audio recording is in progress",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_recording_duration",
        "description": "Get the current recording duration in seconds",
        "inputSchema": {"type": "object", "properties": {}}
    },
    # 视频录制 - 功能不完整，暂时禁用
    # TODO: 视频录制功能存在帧同步和编码问题，待修复后启用
    # {
    #     "name": "record_video",
    #     "description": "Record video with audio (MKV format for audio support, MP4 for video only)",
    #     "inputSchema": {
    #         "type": "object",
    #         "properties": {
    #             "duration": {"type": "number", "description": "Recording duration in seconds"},
    #             "fps": {"type": "integer", "description": "Video frame rate (default 30)"}
    #         },
    #         "required": ["duration"]
    #     }
    # },
    # ==================== 文件传输 ====================
    # 文件传输支持两种模式：
    # - ADB 模式 (默认): 使用 adb push/pull/shell 命令，简单可靠
    # - 网络模式: 使用独立文件通道，适用于无线直连
    {
        "name": "list_dir",
        "description": "List files and directories on the device. Works in both ADB mode (via adb shell) and network mode (via file channel).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path on device (default: /sdcard)", "default": "/sdcard"}
            }
        }
    },
    {
        "name": "pull_file",
        "description": "Download a file from the device to PC. Works in both ADB mode (via adb pull) and network mode (via file channel).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_path": {"type": "string", "description": "File path on device (e.g., /sdcard/Download/file.txt)"},
                "local_path": {"type": "string", "description": "Local path to save the file"}
            },
            "required": ["device_path", "local_path"]
        }
    },
    {
        "name": "push_file",
        "description": "Upload a file from PC to the device. Works in both ADB mode (via adb push) and network mode (via file channel).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "local_path": {"type": "string", "description": "Local file path on PC"},
                "device_path": {"type": "string", "description": "Target path on device (e.g., /sdcard/Download/file.txt)"}
            },
            "required": ["local_path", "device_path"]
        }
    },
    {
        "name": "delete_file",
        "description": "Delete a file or directory on the device. Works in both ADB mode and network mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_path": {"type": "string", "description": "Path to delete on device"}
            },
            "required": ["device_path"]
        }
    },
    {
        "name": "make_dir",
        "description": "Create a directory on the device. Works in both ADB mode and network mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_path": {"type": "string", "description": "Directory path to create"}
            },
            "required": ["device_path"]
        }
    },
    {
        "name": "file_stat",
        "description": "Get file information on the device. Works in both ADB mode and network mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_path": {"type": "string", "description": "File or directory path"}
            },
            "required": ["device_path"]
        }
    },
]

# 服务器资源列表
RESOURCES = []

# 提示词列表
PROMPTS = []


# ==================== YADB 自动安装 ====================
YADB_VERSION = "v1.0.0"
YADB_DOWNLOAD_URL = "https://github.com/yuanbing-CN/yadb/releases/download/v1.0.0/yadb"
YADB_REMOTE_PATH = "/data/local/tmp/yadb"
# 优先使用本地 yadb 文件（项目内部）
YADB_LOCAL_PATH = Path(__file__).parent / "yadb"


def check_and_install_yadb(device_serial: str, timeout: float = 30.0) -> bool:
    """
    检查并自动安装 YADB 到设备

    Args:
        device_serial: 设备序列号
        timeout: 超时时间（秒）

    Returns:
        True 如果 YADB 已安装或成功安装，False 否则
    """
    logger.info(f"[YADB] 检查设备 {device_serial} 是否已安装 YADB...")

    # 1. 检查 YADB 是否已存在
    try:
        cmd = f"adb -s {device_serial} shell test -f {YADB_REMOTE_PATH} && echo exists"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        if "exists" in result.stdout:
            logger.info(f"[YADB] YADB 已安装在设备上")
            return True
    except Exception as e:
        logger.debug(f"[YADB] 检查失败: {e}")

    # 2. 确定本地 YADB 文件路径
    local_yadb_path = None

    # 优先使用项目根目录的 yadb
    if YADB_LOCAL_PATH.exists():
        local_yadb_path = YADB_LOCAL_PATH
        logger.info(f"[YADB] 使用项目本地 YADB: {local_yadb_path}")
    else:
        # 尝试从当前目录查找
        current_dir_yadb = Path("yadb")
        if current_dir_yadb.exists():
            local_yadb_path = current_dir_yadb
            logger.info(f"[YADB] 使用当前目录 YADB: {local_yadb_path}")
        else:
            # 下载 YADB 到本地临时目录
            local_yadb_path = Path("yadb")
            logger.info(f"[YADB] 从 GitHub 下载 YADB {YADB_VERSION}...")
            try:
                urllib.request.urlretrieve(YADB_DOWNLOAD_URL, local_yadb_path)
                # 在 Windows 上添加 .exe 扩展名
                if sys.platform == "win32":
                    exe_path = Path("yadb.exe")
                    if local_yadb_path.exists() and not exe_path.exists():
                        local_yadb_path.rename(exe_path)
                        local_yadb_path = exe_path
                logger.info(f"[YADB] 下载完成: {local_yadb_path}")
            except Exception as e:
                logger.error(f"[YADB] 下载失败: {e}")
                return False

    # 3. 推送到设备
    try:
        logger.info(f"[YADB] 推送 YADB 到设备 {device_serial}...")
        cmd = f"adb -s {device_serial} push \"{local_yadb_path}\" {YADB_REMOTE_PATH}"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            logger.error(f"[YADB] 推送失败: {result.stderr}")
            return False
        logger.info(f"[YADB] 推送成功")
    except Exception as e:
        logger.error(f"[YADB] 推送失败: {e}")
        return False

    # 4. 设置执行权限
    try:
        logger.info(f"[YADB] 设置执行权限...")
        cmd = f"adb -s {device_serial} shell chmod 755 {YADB_REMOTE_PATH}"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            logger.warning(f"[YADB] 设置权限警告: {result.stderr}")
        logger.info(f"[YADB] 安装完成！")
    except Exception as e:
        logger.warning(f"[YADB] 设置权限警告: {e}")

    # 5. 验证安装
    try:
        cmd = f"adb -s {device_serial} shell test -f {YADB_REMOTE_PATH} && echo OK"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        if "OK" in result.stdout:
            logger.info(f"[YADB] 安装验证成功")
            return True
        else:
            logger.error(f"[YADB] 安装验证失败: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"[YADB] 安装验证失败: {e}")
        return False


class ScrcpyMCPHandler:
    """MCP 请求处理器，管理 scrcpy 连接"""

    def __init__(self):
        self._client = None
        self._server = None
        self._lock = threading.Lock()
        self._current_config = None
        self._preview_manager = None  # Separated preview process
        self._server = None
        self._lock = threading.Lock()  # 线程安全锁
        self._current_config = None    # 当前配置

    def _is_client_connected(self) -> bool:
        """检查客户端是否已连接"""
        return self._client is not None and self._client.is_connected

    def _check_server_alive(self) -> tuple:
        """
        检查服务端是否还存活。

        Returns:
            tuple: (is_alive: bool, error_message: str or None)

        对于不同连接模式使用不同的检测方式：
        - 网络模式：检查 TCP 控制通道是否可写
        - ADB 隧道模式：检查 demuxer 线程是否还活跃
        """
        if not self._is_client_connected():
            return False, "Not connected"

        if self._current_config is None:
            return False, "No configuration"

        try:
            # 检查客户端状态
            if self._client and hasattr(self._client, 'state') and self._client.state:
                state = self._client.state

                # 首先检查 state.connected 标志
                if not getattr(state, 'connected', False):
                    return False, "Client state disconnected"

                # 检查视频 demuxer 是否还在运行（如果视频启用）
                if self._current_config.video:
                    video_demuxer = getattr(self._client, '_video_demuxer', None)
                    if video_demuxer is not None:
                        # 检查线程是否还活着
                        demuxer_thread = getattr(video_demuxer, '_thread', None)
                        stopped_event = getattr(video_demuxer, '_stopped', None)

                        # 如果线程不存在或已停止
                        if demuxer_thread is None or not demuxer_thread.is_alive():
                            return False, "Video demuxer thread dead"

                        # 如果 stopped 标志被设置
                        if stopped_event and stopped_event.is_set():
                            return False, "Video demuxer stopped"

                # 网络模式额外检查：TCP 控制通道
                connection_mode = getattr(self._current_config, 'connection_mode', 'adb_tunnel')
                if connection_mode == "network":
                    control_socket = getattr(state, 'control_socket', None)
                    if control_socket:
                        try:
                            control_socket.getpeername()
                        except (OSError, ConnectionError, socket.error) as e:
                            return False, f"Network connection lost: {e}"

                return True, None
            else:
                return False, "Client state not available"

        except Exception as e:
            logger.exception(f"Server alive check failed: {e}")
            return False, f"Alive check error: {e}"

    def _check_screen_off(self) -> Optional[str]:
        """
        检测手机是否可能息屏。

        Returns:
            None if screen likely on, or warning message if screen may be off.

        检测方式：检查视频 demuxer 最近是否收到新数据。
        如果超过 3 秒没有新数据包，可能手机息屏了。
        """
        if not self._current_config or not self._current_config.video:
            return None

        if not self._client:
            return None

        video_demuxer = getattr(self._client, '_video_demuxer', None)
        if video_demuxer is None:
            return None

        # 检查是否有 get_idle_seconds 方法
        if not hasattr(video_demuxer, 'get_idle_seconds'):
            return None

        try:
            idle_seconds = video_demuxer.get_idle_seconds()

            # 处理无穷大的情况（从未收到数据）
            if idle_seconds == float('inf'):
                return "No video data received yet. Screen may be off or connection not established."

            # 超过 3 秒没有新数据，可能息屏
            if idle_seconds > 3.0:
                return f"Screen may be off (no video for {idle_seconds:.1f}s). Please unlock the device."

        except Exception as e:
            logger.debug(f"Screen off check error: {e}")

        return None

    def _ensure_server_alive_for_operation(self, operation_name: str) -> Optional[Dict]:
        """
        确保服务端存活才能执行操作。

        Args:
            operation_name: 操作名称（用于错误提示）

        Returns:
            None if server is alive, or error dict if not alive
        """
        is_alive, error_msg = self._check_server_alive()
        if not is_alive:
            logger.warning(f"Server not alive for {operation_name}: {error_msg}")
            # 清理无效连接
            self._cleanup_dead_connection()
            return {
                "success": False,
                "error": f"Server not available: {error_msg}",
                "hint": "The server has stopped or disconnected. Reconnect with connect() or push_server() first.",
                "operation": operation_name
            }
        return None

    def _cleanup_dead_connection(self):
        """清理无效的连接状态"""
        try:
            if self._server is not None:
                try:
                    self._server.disconnect()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self._server = None
            self._client = None
            self._current_config = None
            logger.info("Cleaned up dead connection")

    def _config_matches(self, **kwargs) -> bool:
        """检查配置是否匹配当前连接

        只检查明确提供的参数。未提供的参数被认为匹配当前配置。
        """
        if not self._is_client_connected() or self._current_config is None:
            return False

        config = self._current_config

        # audio: 只有明确提供时才检查
        if 'audio' in kwargs:
            if config.audio != kwargs['audio']:
                return False

        # video: 只有明确提供时才检查
        if 'video' in kwargs:
            if config.video != kwargs['video']:
                return False

        # connection_mode: 只有明确提供时才检查
        if 'connection_mode' in kwargs:
            if config.connection_mode != kwargs['connection_mode']:
                return False

        # host/device_id: 只有明确提供 device_id 时才检查
        if 'device_id' in kwargs and kwargs['device_id']:
            expected_host = kwargs['device_id'].split(':')[0]
            if config.host != expected_host:
                return False

        return True

    def _start_control_event_reader(self):
        """Start background thread to read control events from preview window.

        This is separate from frame sender because in Direct SHM mode,
        frames are sent directly via shared memory, but we still need to
        read control events (touch, key, etc.) from the preview process.
        """
        if not self._preview_manager or not self._preview_manager.is_running:
            return

        # Check if control reader is already running
        if hasattr(self, '_control_reader_thread') and self._control_reader_thread is not None:
            if self._control_reader_thread.is_alive():
                return

        def control_reader_loop():
            """Read control events from preview and forward to client."""
            import time
            logger.info("Control event reader thread started")
            while self._preview_manager and self._preview_manager.is_running:
                try:
                    # Check connection
                    if not self._is_client_connected():
                        time.sleep(0.1)
                        continue

                    # Handle control events from preview window
                    events = self._preview_manager.get_control_events()
                    for event in events:
                        self._handle_preview_control_event(event)

                    time.sleep(0.01)  # 10ms polling interval

                except Exception as e:
                    logger.warning(f"Control event reader error: {e}")
                    time.sleep(0.1)

            logger.info("Control event reader thread stopped")

        import threading
        self._control_reader_thread = threading.Thread(
            target=control_reader_loop,
            daemon=True,
            name="ControlEventReader"
        )
        self._control_reader_thread.start()
        logger.info("Control event reader thread started")

    def _start_frame_sender(self):
        """Start background thread to send frames to preview window."""
        if not self._preview_manager or not self._preview_manager.is_running:
            return

        # Check if frame sender is already running
        if hasattr(self, '_frame_sender_thread') and self._frame_sender_thread is not None:
            if self._frame_sender_thread.is_alive():
                logger.warning("Frame sender already running, skipping")
                return

        def frame_sender_loop():
            """Send frames from decoder to preview manager and handle control events."""
            import time
            import numpy as np
            last_frame_time = 0
            frame_interval = 1.0 / 60  # Poll at 60fps
            frames_sent = 0
            error_count = 0
            control_events_sent = 0
            loop_count = 0
            has_new_frame_false_count = 0
            consume_none_count = 0  # Track when consume() returns None
            send_failed_count = 0  # Track when send_frame() returns False
            last_log_time = time.time()

            # PTS Clock Drift Diagnostic variables
            last_sender_pts = 0
            last_sender_pts_time = 0.0
            first_sender_pts = 0
            first_sender_pts_time = 0.0

            logger.info("Frame sender loop started (60fps polling)")

            while self._preview_manager and self._preview_manager.is_running:
                try:
                    loop_count += 1
                    current_time = time.time()

                    # Check connection
                    if not self._is_client_connected():
                        time.sleep(0.1)
                        continue

                    # Handle control events from preview window
                    try:
                        events = self._preview_manager.get_control_events()
                        for event in events:
                            if self._handle_preview_control_event(event):
                                control_events_sent += 1
                    except Exception as e:
                        if error_count <= 5:
                            logger.warning(f"Control event handling error: {e}")

                    # Event-driven frame consumption (eliminates polling latency)
                    frame = None
                    pts = 0
                    capture_time = 0.0

                    if self._client:
                        decoder = getattr(self._client, '_video_decoder', None)
                        if decoder:
                            frame_buffer = getattr(decoder, '_frame_buffer', None)

                            if frame_buffer:
                                # Try non-blocking consume first (fast path)
                                result = frame_buffer.consume()
                                if result is not None:
                                    frame = result.frame if hasattr(result, 'frame') else result
                                    pts = result.pts if hasattr(result, 'pts') else 0
                                    capture_time = result.capture_time if hasattr(result, 'capture_time') else 0.0
                                    udp_recv_time = result.udp_recv_time if hasattr(result, 'udp_recv_time') else 0.0
                                    # Get frame dimensions from metadata (works for NV12 too!)
                                    frame_w = result.width if hasattr(result, 'width') else 0
                                    frame_h = result.height if hasattr(result, 'height') else 0
                                else:
                                    # No frame available, briefly yield CPU to decoder
                                    time.sleep(0.001)  # 1ms - allows decoder to run
                                    has_new_frame_false_count += 1

                    if frame is not None:
                        # PTS Clock Drift Diagnostic in frame_sender
                        # IMPORTANT: PTS from scrcpy device is in MICROSECONDS, not nanoseconds!
                        sender_current_time = time.time()
                        if last_sender_pts != 0 and pts != 0:
                            pts_delta_us = pts - last_sender_pts  # PTS increment in MICROSECONDS
                            wall_delta_us = int((sender_current_time - last_sender_pts_time) * 1e6)  # Wall clock in MICROSECONDS
                            drift_us = pts_delta_us - wall_delta_us

                            # Log every 60 frames
                            if frames_sent % 60 == 0 and first_sender_pts != 0:
                                total_pts_us = pts - first_sender_pts  # MICROSECONDS
                                total_wall_us = int((sender_current_time - first_sender_pts_time) * 1e6)  # MICROSECONDS
                                total_drift_ms = (total_pts_us - total_wall_us) / 1e3  # us to ms

                                true_e2e_at_sender = (sender_current_time - udp_recv_time) * 1000 if udp_recv_time > 0 else 0

                                logger.info(
                                    f"[SENDER_PTS] Frame #{frames_sent}: "
                                    f"pts={pts}, "
                                    f"pts_delta={pts_delta_us/1e3:.1f}ms, "  # us to ms
                                    f"wall_delta={wall_delta_us/1e3:.1f}ms, "  # us to ms
                                    f"drift={drift_us/1e3:.2f}ms/frame, "  # us to ms
                                    f"total_drift={total_drift_ms:.0f}ms, "
                                    f"TRUE_E2E={true_e2e_at_sender:.0f}ms"
                                )

                                # CRITICAL WARNING: Detect clock mismatch
                                if abs(total_drift_ms) > 1000 and true_e2e_at_sender < 200:
                                    logger.warning(
                                        f"[SENDER_PTS] CLOCK MISMATCH! "
                                        f"TRUE_E2E={true_e2e_at_sender:.0f}ms looks OK, but "
                                        f"total_drift={total_drift_ms:.0f}ms indicates "
                                        f"{'device clock faster than PC' if total_drift_ms > 0 else 'device clock slower than PC'}"
                                    )

                        # Record first PTS
                        if first_sender_pts == 0 and pts != 0:
                            first_sender_pts = pts
                            first_sender_pts_time = sender_current_time
                            logger.info(f"[SENDER_PTS] First frame: pts={pts}")

                        last_sender_pts = pts
                        last_sender_pts_time = sender_current_time

                        # Check for device rotation (frame size change)
                        # Use metadata width/height (works for NV12 too!)
                        if frame_w > 0 and frame_h > 0:
                            current_w, current_h = self._client.state.device_size
                            if (frame_w, frame_h) != (current_w, current_h):
                                logger.info(f"Device rotation detected: {current_w}x{current_h} -> {frame_w}x{frame_h}")
                                self._client.state.device_size = (frame_w, frame_h)
                        else:
                            # Fallback: use frame.shape for RGB format
                            if len(frame.shape) >= 2:
                                frame_h, frame_w = frame.shape[:2]
                                current_w, current_h = self._client.state.device_size
                                if (frame_w, frame_h) != (current_w, current_h):
                                    logger.info(f"Device rotation detected: {current_w}x{current_h} -> {frame_w}x{frame_h}")
                                    self._client.state.device_size = (frame_w, frame_h)

                        # Calculate TRUE_E2E latency at frame_sender stage
                        sender_time = time.time()
                        true_e2e_at_sender = (sender_time - udp_recv_time) * 1000 if udp_recv_time > 0 else 0

                        # CRITICAL DIAGNOSTIC: Log every 100 frames with human-readable times
                        if frames_sent % 100 == 0:
                            from datetime import datetime as dt
                            udp_time_str = dt.fromtimestamp(udp_recv_time).strftime('%H:%M:%S.%f')[:-3] if udp_recv_time > 0 else "N/A"
                            sender_time_str = dt.fromtimestamp(sender_time).strftime('%H:%M:%S.%f')[:-3]
                            logger.info(f"[FRAME_SENDER] Frame #{frames_sent}: UDP={udp_time_str}, SENDER={sender_time_str}, E2E={true_e2e_at_sender:.0f}ms, pts={pts}")

                        # Log if latency is high at this stage (before sending to preview)
                        if true_e2e_at_sender > 100:
                            logger.info(f"[FRAME_SENDER] TRUE_E2E at sender: {true_e2e_at_sender:.0f}ms, pts={pts}")

                        # Send frame directly (shared memory handles copy internally)
                        try:
                            sent = self._preview_manager.send_frame(frame, pts, capture_time, udp_recv_time)
                            if sent:
                                frames_sent += 1
                                last_frame_time = current_time
                                if frames_sent <= 5:
                                    logger.debug(f"Sent frame #{frames_sent}, shape={frame.shape}")
                            else:
                                send_failed_count += 1
                                if send_failed_count <= 10:
                                    logger.warning(f"[FRAME_SENDER] send_frame() returned False (count={send_failed_count})")
                        except Exception as e:
                            error_count += 1
                            if error_count <= 5:
                                logger.warning(f"Frame send error: {e}")

                    # Log stats every 5 seconds for debugging
                    current_time = time.time()
                    if current_time - last_log_time >= 5.0:
                        logger.info(f"[FRAME_SENDER] Sent={frames_sent}, timeout={has_new_frame_false_count}")
                        last_log_time = current_time
                        has_new_frame_false_count = 0
                        send_failed_count = 0

                except Exception as e:
                    error_count += 1
                    if error_count <= 10:
                        logger.warning(f"Frame sender error: {e}")
                    time.sleep(0.1)

            logger.info(f"Frame sender stopped, sent {frames_sent} frames, {control_events_sent} control events")

        import threading
        self._frame_sender_thread = threading.Thread(target=frame_sender_loop, daemon=True, name="PreviewFrameSender")
        self._frame_sender_thread.start()
        logger.info("Frame sender thread started")

    def _handle_preview_control_event(self, event) -> bool:
        """Handle control event from preview window.

        Args:
            event: Tuple of (type, *args)
                - ('touch_down', x, y) for touch start
                - ('touch_move', x, y) for touch move
                - ('touch_up', x, y) for touch end
                - ('tap', x, y) for simple tap (legacy)
                - ('swipe', x1, y1, x2, y2) for swipe (legacy)
                - ('scroll', x, y, hscroll, vscroll) for scroll
                - ('key', action) for key press

        Returns:
            True if event was handled successfully
        """
        if not self._client:
            return False

        try:
            event_type = event[0]

            # Real-time touch events
            if event_type == 'touch_down':
                x, y = event[1], event[2]
                logger.debug(f"Preview touch DOWN: ({x}, {y})")
                if hasattr(self._client, 'inject_touch_event'):
                    from scrcpy_py_ddlx.core.protocol import AndroidMotionEventAction, POINTER_ID_GENERIC_FINGER
                    width, height = self._client.state.device_size
                    self._client.inject_touch_event(
                        AndroidMotionEventAction.DOWN,
                        POINTER_ID_GENERIC_FINGER,
                        x, y, width, height, 1.0  # pressure = 1.0 for DOWN
                    )
                    return True

            elif event_type == 'touch_move':
                x, y = event[1], event[2]
                logger.debug(f"Preview touch MOVE: ({x}, {y})")
                if hasattr(self._client, 'inject_touch_event'):
                    from scrcpy_py_ddlx.core.protocol import AndroidMotionEventAction, POINTER_ID_GENERIC_FINGER
                    width, height = self._client.state.device_size
                    self._client.inject_touch_event(
                        AndroidMotionEventAction.MOVE,
                        POINTER_ID_GENERIC_FINGER,
                        x, y, width, height, 1.0  # pressure = 1.0 for MOVE
                    )
                    return True

            elif event_type == 'touch_up':
                x, y = event[1], event[2]
                logger.debug(f"Preview touch UP: ({x}, {y})")
                if hasattr(self._client, 'inject_touch_event'):
                    from scrcpy_py_ddlx.core.protocol import AndroidMotionEventAction, POINTER_ID_GENERIC_FINGER
                    width, height = self._client.state.device_size
                    self._client.inject_touch_event(
                        AndroidMotionEventAction.UP,
                        POINTER_ID_GENERIC_FINGER,
                        x, y, width, height, 0.0  # pressure = 0.0 for UP
                    )
                    return True

            # Device size change notification from preview process
            elif event_type == 'device_size_changed':
                w, h = event[1], event[2]
                logger.info(f"[MCP] Received device_size_changed from preview: {w}x{h}")
                # Update client.state.device_size for touch events
                old_w, old_h = self._client.state.device_size
                if (w, h) != (old_w, old_h):
                    self._client.state.device_size = (w, h)
                    logger.info(f"[MCP] Updated client.state.device_size: {old_w}x{old_h} -> {w}x{h}")
                return True

            # Legacy tap and swipe (for backward compatibility)
            elif event_type == 'tap':
                x, y = event[1], event[2]
                logger.debug(f"Preview tap: ({x}, {y})")
                if hasattr(self._client, 'tap'):
                    self._client.tap(x, y)
                    return True

            elif event_type == 'swipe':
                x1, y1, x2, y2 = event[1], event[2], event[3], event[4]
                logger.debug(f"Preview swipe: ({x1}, {y1}) -> ({x2}, {y2})")
                if hasattr(self._client, 'swipe'):
                    self._client.swipe(x1, y1, x2, y2, duration_ms=300)
                    return True

            elif event_type == 'scroll':
                x, y, hscroll, vscroll = event[1], event[2], event[3], event[4]
                logger.debug(f"Preview scroll: ({x}, {y}) h={hscroll} v={vscroll}")
                if hasattr(self._client, 'inject_scroll_event'):
                    width, height = self._client.state.device_size
                    self._client.inject_scroll_event(x, y, width, height, hscroll, vscroll)
                    return True

            elif event_type == 'key':
                action = event[1]
                logger.debug(f"Preview key: {action}")
                # Use inject_keycode method (client doesn't have key_event)
                if hasattr(self._client, 'inject_keycode'):
                    from scrcpy_py_ddlx.core.protocol import AndroidKeyEventAction
                    # Map action names to Android key codes
                    # Reference: https://developer.android.com/reference/android/view/KeyEvent
                    key_map = {
                        # Navigation keys
                        'back': 4,          # KEYCODE_BACK
                        'home': 3,          # KEYCODE_HOME
                        'menu': 82,         # KEYCODE_MENU
                        'enter': 66,        # KEYCODE_ENTER

                        # Arrow / DPAD keys
                        'dpad_left': 21,    # KEYCODE_DPAD_LEFT
                        'dpad_right': 22,   # KEYCODE_DPAD_RIGHT
                        'dpad_up': 19,      # KEYCODE_DPAD_UP
                        'dpad_down': 20,    # KEYCODE_DPAD_DOWN
                        'dpad_center': 23,  # KEYCODE_DPAD_CENTER

                        # Media keys
                        'volume_up': 24,    # KEYCODE_VOLUME_UP
                        'volume_down': 25,  # KEYCODE_VOLUME_DOWN
                        'volume_mute': 164, # KEYCODE_VOLUME_MUTE
                        'media_play_pause': 85,  # KEYCODE_MEDIA_PLAY_PAUSE
                        'media_stop': 86,   # KEYCODE_MEDIA_STOP
                        'media_next': 87,   # KEYCODE_MEDIA_NEXT
                        'media_previous': 88, # KEYCODE_MEDIA_PREVIOUS

                        # Function keys
                        'f1': 131,          # KEYCODE_F1
                        'f2': 132,          # KEYCODE_F2
                        'f3': 133,          # KEYCODE_F3
                        'f4': 134,          # KEYCODE_F4
                        'f5': 135,          # KEYCODE_F5
                        'f6': 136,          # KEYCODE_F6
                        'f7': 137,          # KEYCODE_F7
                        'f8': 138,          # KEYCODE_F8
                        'f9': 139,          # KEYCODE_F9
                        'f10': 140,         # KEYCODE_F10
                        'f11': 141,         # KEYCODE_F11
                        'f12': 142,         # KEYCODE_F12

                        # Special keys
                        'tab': 61,          # KEYCODE_TAB
                        'del': 67,          # KEYCODE_DEL (backspace)
                        'insert': 124,      # KEYCODE_INSERT
                        'page_up': 92,      # KEYCODE_PAGE_UP
                        'page_down': 93,    # KEYCODE_PAGE_DOWN
                        'caps_lock': 115,   # KEYCODE_CAPS_LOCK
                        'num_lock': 143,    # KEYCODE_NUM_LOCK
                        'scroll_lock': 116, # KEYCODE_SCROLL_LOCK

                        # Power
                        'power': 26,        # KEYCODE_POWER

                        # Camera
                        'camera': 27,       # KEYCODE_CAMERA
                        'camera_focus': 80, # KEYCODE_FOCUS

                        # Search
                        'search': 84,       # KEYCODE_SEARCH

                        # Calculator
                        'calculator': 210,  # KEYCODE_CALCULATOR

                        # Notifications
                        'notification': 83, # KEYCODE_NOTIFICATION
                    }
                    keycode = key_map.get(action)
                    if keycode:
                        # Send DOWN and UP events
                        self._client.inject_keycode(keycode, AndroidKeyEventAction.DOWN)
                        self._client.inject_keycode(keycode, AndroidKeyEventAction.UP)
                        return True

            elif event_type == 'text':
                text = event[1]
                logger.debug(f"Preview text: '{text}'")
                # Use inject_text for all text input (ASCII and non-ASCII)
                if hasattr(self._client, 'inject_text'):
                    self._client.inject_text(text)
                    return True

            return False

        except Exception as e:
            logger.warning(f"Failed to handle preview control event: {e}")
            return False

    def _ensure_connected(self, **kwargs) -> Optional[Any]:
        """确保已连接到设备，支持完整的配置参数

        Args:
            **kwargs: 连接参数
                - connection_mode: "adb_tunnel" 或 "network"
                - device_id: 设备 ID 或 IP 地址
                - video: 启用视频
                - audio: 启用音频
                - codec: 视频编码器
                - bitrate: 视频码率
                - max_fps: 最大帧率
                - bitrate_mode: 码率模式 (vbr/cbr)
                - i_frame_interval: 关键帧间隔
                - control_port, video_port, audio_port: 网络模式端口
                - stay_alive: stay-alive 模式
                - wake_server: UDP wake 唤醒
                - fec_enabled, video_fec_enabled, audio_fec_enabled: FEC 开关
                - fec_group_size, fec_parity_count: FEC 参数
        """
        # 提取参数
        connection_mode = kwargs.get('connection_mode', 'adb_tunnel')
        device_id = kwargs.get('device_id', None)
        video = kwargs.get('video', True)
        audio = kwargs.get('audio', DEFAULT_AUDIO_ENABLED)
        audio_dup = kwargs.get('audio_dup', DEFAULT_AUDIO_DUP)  # Duplicate audio on device and computer
        codec = kwargs.get('codec', 'auto')
        bitrate = kwargs.get('bitrate', 8000000)
        max_fps = kwargs.get('max_fps', 60)
        bitrate_mode = kwargs.get('bitrate_mode', 'vbr')
        i_frame_interval = kwargs.get('i_frame_interval', 10.0)
        stay_alive = kwargs.get('stay_alive', False)
        stay_awake = kwargs.get('stay_awake', True)

        # 网络模式参数
        control_port = kwargs.get('control_port', 27184)
        video_port = kwargs.get('video_port', 27185)
        audio_port = kwargs.get('audio_port', 27186)
        file_port = kwargs.get('file_port', 27187)
        wake_server = kwargs.get('wake_server', True)

        # FEC 参数
        fec_enabled = kwargs.get('fec_enabled', False)
        video_fec_enabled = kwargs.get('video_fec_enabled', False)
        audio_fec_enabled = kwargs.get('audio_fec_enabled', False)
        fec_group_size = kwargs.get('fec_group_size', 4)
        fec_parity_count = kwargs.get('fec_parity_count', 1)

        # 如果客户端已连接且配置匹配，直接返回
        if self._config_matches(**kwargs):
            return self._server

        # 配置不匹配，需要重新连接
        if self._server is not None:
            logger.info("Config changed, reconnecting...")
            try:
                self._server.disconnect()
            except Exception:
                pass
            self._server = None
            self._client = None

        # 创建配置
        try:
            from scrcpy_py_ddlx import create_mcp_server, ClientConfig

            config = ClientConfig(
                # 连接设置
                connection_mode=connection_mode,
                host=device_id.split(':')[0] if device_id and ':' in device_id else (device_id or "localhost"),
                port=int(device_id.split(':')[1]) if device_id and ':' in device_id else 27183,
                control_port=control_port,
                video_port=video_port,
                audio_port=audio_port,
                file_port=file_port,

                # 媒体设置
                video=video,
                audio=audio,
                audio_dup=audio_dup,
                codec=codec,
                bitrate=bitrate,
                max_fps=max_fps,
                bitrate_mode=bitrate_mode,
                i_frame_interval=i_frame_interval,

                # FEC 设置
                fec_enabled=fec_enabled,
                video_fec_enabled=video_fec_enabled,
                audio_fec_enabled=audio_fec_enabled,
                fec_group_size=fec_group_size,
                fec_parity_count=fec_parity_count,

                # 其他设置
                show_window=False,
                control=True,
                stay_awake=stay_awake,
                lazy_decode=False,  # MCP 模式：解码器持续运行，截图低延迟

                # ADB 设置
                tcpip=kwargs.get('tcpip', False),
            )

            self._current_config = config

            # 创建服务器
            self._server = create_mcp_server(default_config=config, log_file=None, enable_console_log=False)

            # 连接
            if connection_mode == "network":
                # 网络模式：直接连接
                result = self._server.connect(audio_dup=audio_dup)
            else:
                # ADB 隧道模式
                result = self._server.connect(audio=audio, audio_dup=audio_dup)

            # 检查连接结果
            if result.get("success") or "Already connected" in str(result.get("error", "")):
                self._client = self._server._client

                # 连接成功后，自动安装 YADB（支持中文输入）
                device_serial = getattr(self._client.state, "device_serial", None)
                if device_serial:
                    try:
                        check_and_install_yadb(device_serial)
                    except Exception as e:
                        logger.warning(f"[YADB] 自动安装失败（中文输入可能不可用）: {e}")

                logger.info(f"Connected: mode={connection_mode}, video={video}, audio={audio}")
                return self._server

            # Connection failed - check if ADB needs restart
            logger.error(f"Connection failed: {result}")

            # Check for ADB-related errors and try to restart ADB server
            if self._should_restart_adb(result):
                logger.info("Attempting to restart ADB server due to connection failure...")
                if self._restart_adb_server():
                    # Retry connection once after ADB restart
                    logger.info("ADB server restarted, retrying connection...")
                    try:
                        result = self._server.connect()
                        if result.get("success"):
                            logger.info("Connection succeeded after ADB restart!")
                            return self._server
                    except Exception as retry_e:
                        logger.error(f"Retry after ADB restart failed: {retry_e}")

            return None

        except Exception as e:
            logger.exception(f"连接失败: {e}")

            # Check if this is an ADB-related exception
            if self._is_adb_error(e):
                logger.info("Attempting to restart ADB server due to exception...")
                self._restart_adb_server()

            return None

    def _should_restart_adb(self, result: Dict) -> bool:
        """Check if ADB server should be restarted based on connection result."""
        if not result:
            return False

        error = result.get("error", "")
        if isinstance(error, dict):
            error = str(error)

        # Common ADB issues that can be fixed by restart
        adb_error_patterns = [
            "device not found",
            "device offline",
            "unauthorized",
            "connection refused",
            "cannot connect",
            "timeout",
            "ADB server",
            "protocol fault",
            "closed",
        ]

        error_lower = error.lower()
        return any(pattern in error_lower for pattern in adb_error_patterns)

    def _is_adb_error(self, exception: Exception) -> bool:
        """Check if an exception is ADB-related."""
        error_str = str(exception).lower()
        adb_error_patterns = [
            "device not found",
            "device offline",
            "connection refused",
            "timeout",
            "adb",
            "protocol fault",
        ]
        return any(pattern in error_str for pattern in adb_error_patterns)

    def _restart_adb_server(self) -> bool:
        """Restart ADB server."""
        try:
            from scrcpy_py_ddlx.core.adb import ADBManager
            adb = ADBManager()
            return adb.restart_adb_server()
        except Exception as e:
            logger.error(f"Failed to restart ADB server: {e}")
            return False

    def call_tool(self, tool_name: str, arguments: Dict) -> Dict:
        """调用工具（线程安全）"""
        with self._lock:
            try:
                # connect 操作特殊处理 - 支持完整参数
                if tool_name == "connect":
                    server = self._ensure_connected(**arguments)
                    if server is not None:
                        # Get codec info
                        codec_id = getattr(self._client.state, "codec_id", 0) if self._client else 0
                        codec_name = "unknown"
                        if codec_id == 0x68323634:  # 'h264'
                            codec_name = "h264"
                        elif codec_id == 0x68323635:  # 'h265'
                            codec_name = "h265"
                        elif codec_id == 0x61763031:  # 'av01'
                            codec_name = "av1"

                        # Get cached encoder info if available
                        encoder_info = {}
                        try:
                            from scrcpy_py_ddlx.client.capability_cache import CapabilityCache
                            cache = CapabilityCache.get_instance()
                            device_serial = getattr(self._client.state, "device_serial", None) if self._client else None
                            if device_serial:
                                device_cap = cache.get_device_capability(device_serial, force_refresh=False)
                                if device_cap and device_cap.video_encoders:
                                    encoder_info = {
                                        "h264": device_cap.video_encoders.get("h264", []),
                                        "h265": device_cap.video_encoders.get("h265", []),
                                        "av1": device_cap.video_encoders.get("av1", []),
                                    }
                        except Exception as e:
                            logger.debug(f"Failed to get encoder info from cache: {e}")

                        result = {
                            "success": True,
                            "device_name": self._client.device_name if self._client else "unknown",
                            "device_size": self._client.device_size if self._client else [0, 0],
                            "connection_mode": arguments.get("connection_mode", "adb_tunnel"),
                            "video": arguments.get("video", True),
                            "audio": arguments.get("audio", False),
                            "codec": {
                                "name": codec_name,
                                "id": f"0x{codec_id:08x}" if codec_id else None,
                            },
                            "hardware_encoders": encoder_info if encoder_info else "not cached"
                        }
                    else:
                        result = {
                            "success": False,
                            "error": "Failed to connect",
                            "hint": "For network mode, ensure scrcpy server is running on device. For ADB mode, ensure device is connected."
                        }
                    result_text = json.dumps(result, ensure_ascii=False, indent=2)
                    return {"content": [{"type": "text", "text": result_text}]}

                # set_video 操作 - 设置视频启用状态（需要重连）
                if tool_name == "set_video":
                    enabled = arguments.get("enabled", True)
                    if self._current_config:
                        self._current_config.video = enabled
                        # 需要重连才能生效
                        result = {
                            "success": True,
                            "video": enabled,
                            "note": "Reconnect required for changes to take effect"
                        }
                    else:
                        result = {"success": False, "error": "Not configured"}
                    result_text = json.dumps(result, ensure_ascii=False, indent=2)
                    return {"content": [{"type": "text", "text": result_text}]}

                # set_audio 操作 - 设置音频启用状态（需要重连）
                if tool_name == "set_audio":
                    enabled = arguments.get("enabled", True)
                    if self._current_config:
                        self._current_config.audio = enabled
                        result = {
                            "success": True,
                            "audio": enabled,
                            "note": "Reconnect required for changes to take effect"
                        }
                    else:
                        result = {"success": False, "error": "Not configured"}
                    result_text = json.dumps(result, ensure_ascii=False, indent=2)
                    return {"content": [{"type": "text", "text": result_text}]}

                # disconnect 操作特殊处理 - 不需要先连接
                if tool_name == "disconnect":
                    if self._server is not None:
                        result = self._server.disconnect()
                        self._client = None
                        self._server = None
                        self._current_config = None
                        result_text = json.dumps(result, ensure_ascii=False, indent=2)
                        return {"content": [{"type": "text", "text": result_text}]}
                    else:
                        return {"content": [{"type": "text", "text": json.dumps({"success": True, "already_disconnected": True}, ensure_ascii=False)}]}

                # list_devices 操作特殊处理 - 不需要先连接
                if tool_name == "list_devices":
                    try:
                        from scrcpy_py_ddlx.core.adb import ADBManager
                        adb = ADBManager()
                        devices = adb.list_devices(long_format=True)
                        devices_info = []
                        for d in devices:
                            # 尝试获取设备 IP 地址
                            ip = None
                            if d.is_ready():
                                try:
                                    ip = adb.get_device_ip(d.serial, timeout=3.0)
                                except Exception:
                                    pass

                            devices_info.append({
                                "serial": d.serial,
                                "state": d.state,
                                "model": d.model,
                                "type": d.device_type.value,
                                "ready": d.is_ready(),
                                "unauthorized": d.is_unauthorized(),
                                "ip": ip
                            })
                        result = {
                            "success": True,
                            "count": len(devices_info),
                            "devices": devices_info
                        }
                        # 如果没有设备，添加提示信息
                        if len(devices_info) == 0:
                            result["hint"] = "No devices found. Call discover_devices() to scan local network for wireless ADB devices."
                        result_text = json.dumps(result, ensure_ascii=False, indent=2)
                        return {"content": [{"type": "text", "text": result_text}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)}]}

                # get_device_ip 操作特殊处理 - 不需要先连接
                if tool_name == "get_device_ip":
                    try:
                        from scrcpy_py_ddlx.core.adb import ADBManager
                        adb = ADBManager()
                        serial = arguments.get("serial")
                        if not serial:
                            return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": "serial parameter is required"}, ensure_ascii=False)}]}

                        ip = adb.get_device_ip(serial, timeout=10.0)
                        if ip:
                            result = {"success": True, "serial": serial, "ip": ip}
                        else:
                            result = {"success": False, "error": "Failed to get IP address", "serial": serial}
                        result_text = json.dumps(result, ensure_ascii=False, indent=2)
                        return {"content": [{"type": "text", "text": result_text}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)}]}

                # enable_wireless 操作特殊处理 - 不需要先连接
                if tool_name == "enable_wireless":
                    try:
                        from scrcpy_py_ddlx.core.adb import ADBManager
                        adb = ADBManager()
                        serial = arguments.get("serial")
                        port = arguments.get("port", 5555)
                        if not serial:
                            return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": "serial parameter is required"}, ensure_ascii=False)}]}

                        success = adb.enable_tcpip(serial, port=port, timeout=30.0)
                        if success:
                            # 等待设备重启 TCP/IP 模式
                            import time
                            time.sleep(2.0)
                            # 尝试获取 IP 地址
                            ip = adb.get_device_ip(serial, timeout=10.0)
                            result = {"success": True, "serial": serial, "port": port, "ip": ip, "message": f"Wireless debugging enabled on port {port}. Use connect_wireless with IP: {ip}"}
                        else:
                            result = {"success": False, "error": "Failed to enable wireless debugging", "serial": serial}
                        result_text = json.dumps(result, ensure_ascii=False, indent=2)
                        return {"content": [{"type": "text", "text": result_text}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)}]}

                # connect_wireless 操作特殊处理 - 不需要先连接
                if tool_name == "connect_wireless":
                    try:
                        from scrcpy_py_ddlx.core.adb import ADBManager
                        adb = ADBManager()
                        ip = arguments.get("ip")
                        port = arguments.get("port", 5555)
                        if not ip:
                            return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": "ip parameter is required"}, ensure_ascii=False)}]}

                        success = adb.connect_tcpip(ip, port=port, timeout=30.0)
                        if success:
                            result = {"success": True, "ip": ip, "port": port, "device_id": f"{ip}:{port}", "message": f"Connected to {ip}:{port}"}
                        else:
                            result = {"success": False, "error": f"Failed to connect to {ip}:{port}", "ip": ip, "port": port}
                        result_text = json.dumps(result, ensure_ascii=False, indent=2)
                        return {"content": [{"type": "text", "text": result_text}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)}]}

                # disconnect_wireless 操作特殊处理 - 不需要先连接
                if tool_name == "disconnect_wireless":
                    try:
                        from scrcpy_py_ddlx.core.adb import ADBManager
                        adb = ADBManager()
                        ip = arguments.get("ip")
                        port = arguments.get("port", 5555)
                        if not ip:
                            return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": "ip parameter is required"}, ensure_ascii=False)}]}

                        success = adb.disconnect_tcpip(ip, port=port, timeout=10.0)
                        result = {"success": success, "ip": ip, "port": port, "message": f"Disconnected from {ip}:{port}"}
                        result_text = json.dumps(result, ensure_ascii=False, indent=2)
                        return {"content": [{"type": "text", "text": result_text}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)}]}

                # start_preview - 启动分离进程预览窗口
                if tool_name == "start_preview":
                    try:
                        if not self._is_client_connected():
                            return {"content": [{"type": "text", "text": json.dumps({
                                "success": False,
                                "error": "Not connected",
                                "hint": "Connect first with connect()"
                            }, ensure_ascii=False)}]}

                        if self._current_config and not self._current_config.video:
                            return {"content": [{"type": "text", "text": json.dumps({
                                "success": False,
                                "error": "Video not enabled",
                                "hint": "Reconnect with video=true to use preview"
                            }, ensure_ascii=False)}]}

                        # Check if already running
                        if self._preview_manager and self._preview_manager.is_running:
                            return {"content": [{"type": "text", "text": json.dumps({
                                "success": True,
                                "message": "Preview already running",
                                "device": self._client.state.device_name if self._client else "unknown"
                            }, ensure_ascii=False)}]}

                        # CRITICAL: Resume video decoder if paused (lazy decode mode)
                        # When show_window=False, the decoder is paused to save CPU
                        # We need to resume it to get frames for the preview window
                        was_paused = False
                        if hasattr(self._client, '_video_enabled') and not self._client._video_enabled:
                            was_paused = True
                            logger.info("Preview: Resuming video decoder from lazy decode mode")
                            if hasattr(self._client, 'enable_video'):
                                self._client.enable_video()

                            # Request a new keyframe to ensure proper decoding after resume
                            # Without this, the decoder might get P-frames without reference
                            if hasattr(self._client, 'reset_video'):
                                self._client.reset_video()
                                logger.info("Preview: Requested keyframe (reset_video)")

                        # Track lazy decode state for auto-pause on preview stop
                        self._preview_was_lazy = was_paused

                        # Import and create preview manager
                        from scrcpy_py_ddlx.preview_process import PreviewManager

                        device_name = getattr(self._client.state, 'device_name', 'Device')
                        device_size = getattr(self._client.state, 'device_size', (1080, 1920))
                        width, height = device_size[0], device_size[1]

                        # Use small queue (2 frames) for minimal latency
                        self._preview_manager = PreviewManager(max_queue_size=2)
                        success = self._preview_manager.start(device_name, width, height)

                        if success:
                            # Wait for preview window to be ready before starting frame sender
                            # This prevents frame loss during preview initialization
                            logger.info("Waiting for preview window to be ready...")
                            ready = self._preview_manager.wait_for_ready(timeout=5.0)
                            if not ready:
                                logger.warning("Preview window not ready, starting frame sender anyway")

                            # Get the SimpleSHMWriter and pass it to the decoder for direct writing
                            # This eliminates the frame_sender_thread and GIL contention
                            shm_writer = self._preview_manager.get_shm_writer()
                            if shm_writer is not None and self._client is not None:
                                decoder = getattr(self._client, '_video_decoder', None)
                                if decoder is not None:
                                    decoder._shm_writer = shm_writer
                                    # Try GPU NV12 mode first
                                    try:
                                        decoder._output_nv12 = True
                                        logger.info("Direct SHM mode enabled: GPU NV12 rendering")
                                        # Start control event reader (separate from frame sender)
                                        self._start_control_event_reader()
                                    except Exception as e:
                                        decoder._output_nv12 = False
                                        logger.warning(f"GPU NV12 not available, falling back to CPU: {e}")
                                        logger.warning("CPU mode: NOT recommended for >2Mbps or >30fps due to GIL contention")
                                        self._start_frame_sender()
                                else:
                                    logger.warning("Decoder not found, falling back to frame_sender_thread")
                                    self._start_frame_sender()
                            else:
                                logger.warning("SHM writer not available, falling back to frame_sender_thread")
                                self._start_frame_sender()

                            result = {
                                "success": True,
                                "message": "Preview window started",
                                "device": device_name,
                                "resolution": f"{width}x{height}"
                            }
                        else:
                            result = {"success": False, "error": "Failed to start preview process"}

                        result_text = json.dumps(result, ensure_ascii=False, indent=2)
                        return {"content": [{"type": "text", "text": result_text}]}
                    except Exception as e:
                        logger.exception(f"start_preview error: {e}")
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)}]}

                # stop_preview - 停止预览窗口
                if tool_name == "stop_preview":
                    try:
                        if self._preview_manager:
                            # Clear the decoder's SHM writer reference and disable NV12
                            if self._client is not None:
                                decoder = getattr(self._client, '_video_decoder', None)
                                if decoder is not None:
                                    decoder._shm_writer = None
                                    decoder._output_nv12 = False  # Disable NV12 output
                                    logger.info("Direct SHM mode disabled (NV12 disabled)")

                            self._preview_manager.stop()
                            self._preview_manager = None

                            # Re-pause video if it was in lazy decode mode before preview
                            if getattr(self, '_preview_was_lazy', False):
                                logger.info("Preview: Re-pausing video decoder (lazy decode mode)")
                                if hasattr(self._client, 'disable_video'):
                                    self._client.disable_video()
                                self._preview_was_lazy = False

                            result = {"success": True, "message": "Preview stopped"}
                        else:
                            result = {"success": True, "message": "Preview was not running"}

                        result_text = json.dumps(result, ensure_ascii=False, indent=2)
                        return {"content": [{"type": "text", "text": result_text}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)}]}

                # get_preview_status - 获取预览状态
                if tool_name == "get_preview_status":
                    try:
                        is_running = self._preview_manager and self._preview_manager.is_running
                        result = {
                            "success": True,
                            "running": is_running,
                            "device": getattr(self._client.state, 'device_name', None) if self._client else None
                        }
                        result_text = json.dumps(result, ensure_ascii=False, indent=2)
                        return {"content": [{"type": "text", "text": result_text}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)}]}

                # push_server_onetime / push_server_persistent - 必须在 push_server 之前处理
                auto_connect_after_push = False
                if tool_name == "push_server_onetime":
                    # 一次性模式：stay_alive=false，断开后服务端退出
                    # video/audio 由用户控制，默认都关闭（仅控制消息）
                    arguments["stay_alive"] = False
                    arguments["video"] = arguments.get("video", False)
                    arguments["audio"] = arguments.get("audio", False)
                    auto_connect_after_push = arguments.pop("auto_connect", False)  # 保存并移除
                    tool_name = "push_server"  # 继续到 push_server 的处理

                if tool_name == "push_server_persistent":
                    # 常驻模式：stay_alive=true，断开后服务端继续运行
                    # video/audio 由用户控制，默认都关闭（仅控制消息）
                    arguments["stay_alive"] = True
                    arguments["video"] = arguments.get("video", False)
                    arguments["audio"] = arguments.get("audio", False)
                    max_conn = arguments.get("max_connections", -1)
                    if max_conn > 0:
                        arguments["max_connections"] = max_conn
                    tool_name = "push_server"  # 继续到 push_server 的处理

                # push_server 操作 - 推送服务器文件到设备
                if tool_name == "push_server":
                    try:
                        import subprocess
                        import os
                        import time

                        # 基础参数
                        server_path = arguments.get("server_path", "./scrcpy-server")
                        device_id = arguments.get("device_id")
                        do_push = arguments.get("push", True)
                        start_server = arguments.get("start", True)
                        kill_old = arguments.get("kill_old", True)
                        reuse_server = arguments.get("reuse", False)
                        stay_alive = arguments.get("stay_alive", True)

                        # 端口配置
                        control_port = arguments.get("control_port", 27184)
                        video_port = arguments.get("video_port", 27185)
                        audio_port = arguments.get("audio_port", 27186)
                        file_port = arguments.get("file_port", 27187)

                        # 视频参数
                        video_enabled = arguments.get("video", True)  # 默认开启（兼容旧版）
                        video_codec = arguments.get("video_codec", "auto")
                        video_bitrate = arguments.get("video_bitrate", 8000000)
                        max_fps = arguments.get("max_fps", 60)
                        bitrate_mode = arguments.get("bitrate_mode", "vbr")
                        i_frame_interval = arguments.get("i_frame_interval", 10.0)

                        # 音频参数
                        audio_enabled = arguments.get("audio", False)
                        audio_dup = arguments.get("audio_dup", False)

                        # FEC 参数
                        fec_enabled = arguments.get("fec_enabled", False)
                        video_fec_enabled = arguments.get("video_fec_enabled", False)
                        audio_fec_enabled = arguments.get("audio_fec_enabled", False)
                        fec_group_size = arguments.get("fec_group_size", 4)
                        fec_parity_count = arguments.get("fec_parity_count", 1)

                        # 构建基础 adb 命令前缀
                        def adb_cmd(args):
                            if device_id:
                                return ["adb", "-s", device_id] + args
                            return ["adb"] + args

                        # 检查是否有服务端在运行
                        def check_server_running():
                            try:
                                result = subprocess.run(
                                    adb_cmd(["shell", "ps -A | grep app_process"]),
                                    capture_output=True, text=True, timeout=5
                                )
                                return "app_process" in result.stdout
                            except Exception:
                                return False

                        server_running = check_server_running()
                        result_data = {"success": True}

                        # 复用模式：如果服务端已运行，跳过所有操作
                        if reuse_server and server_running:
                            result_data["message"] = "Server already running, reusing it"
                            result_data["action"] = "reuse"
                            result_data["server_running"] = True

                            # 获取设备 IP
                            try:
                                ip_result = subprocess.run(
                                    adb_cmd(["shell", "ip route | awk '/src/ {print $NF}' | head -1"]),
                                    capture_output=True, text=True, timeout=5
                                )
                                device_ip = ip_result.stdout.strip() if ip_result.returncode == 0 else None
                                if device_ip:
                                    result_data["device_ip"] = device_ip
                                    result_data["next_step"] = f"Use connect(connection_mode='network', device_id='{device_ip}') to connect"
                            except Exception:
                                pass

                            result_text = json.dumps(result_data, ensure_ascii=False, indent=2)
                            return {"content": [{"type": "text", "text": result_text}]}

                        # 杀掉旧服务端（如果需要）
                        if start_server and kill_old and server_running:
                            logger.info("Killing old server...")
                            subprocess.run(
                                adb_cmd(["shell", "pkill -9 -f app_process"]),
                                capture_output=True, timeout=5
                            )
                            time.sleep(1)
                            result_data["killed_old"] = True

                        # 推送服务器文件
                        if do_push:
                            # 检查服务器文件是否存在
                            if not os.path.exists(server_path):
                                project_root = os.path.dirname(os.path.abspath(__file__))
                                alt_path = os.path.join(project_root, "scrcpy-server")
                                if os.path.exists(alt_path):
                                    server_path = alt_path
                                else:
                                    return {"content": [{"type": "text", "text": json.dumps({
                                        "success": False,
                                        "error": f"Server file not found: {server_path}",
                                        "hint": "Build the server first or specify correct path"
                                    }, ensure_ascii=False)}]}

                            server_path = os.path.abspath(server_path)
                            logger.info(f"Pushing server from: {server_path}")

                            remote_path = "/data/local/tmp/scrcpy-server.apk"
                            push_result = subprocess.run(
                                adb_cmd(["push", server_path, remote_path]),
                                capture_output=True, text=True, timeout=60
                            )

                            if push_result.returncode != 0:
                                result_data = {
                                    "success": False,
                                    "error": push_result.stderr.strip() or "adb push failed",
                                    "command": " ".join(adb_cmd(["push", server_path, remote_path]))
                                }
                                result_text = json.dumps(result_data, ensure_ascii=False, indent=2)
                                return {"content": [{"type": "text", "text": result_text}]}

                            result_data["push"] = {
                                "source": server_path,
                                "destination": remote_path,
                                "output": push_result.stdout.strip()
                            }
                        else:
                            result_data["push"] = "skipped"

                        # 启动服务端
                        if start_server:
                            logger.info("Starting server with nohup...")
                            remote_path = "/data/local/tmp/scrcpy-server.apk"

                            # 构建服务端启动命令
                            stay_alive_str = "true" if stay_alive else "false"
                            audio_str = "true" if audio_enabled else "false"

                            # Build audio parameters
                            if audio_enabled:
                                if audio_dup:
                                    audio_params = "audio=true audio_source=playback audio_dup=true"
                                else:
                                    audio_params = "audio=true audio_source=output"
                            else:
                                audio_params = "audio=false"

                            # 获取 discovery_port（用于 UDP 唤醒）
                            discovery_port = arguments.get("discovery_port", 27183)

                            # 自动检测编解码器（auto 不能直接传给服务端）
                            actual_codec = video_codec
                            if video_codec.lower() == "auto":
                                try:
                                    from scrcpy_py_ddlx.client.capability_cache import CapabilityCache
                                    cache = CapabilityCache.get_instance()
                                    # 获取当前设备 serial
                                    serial_arg = ["-s", device_id] if device_id else []
                                    serial_result = subprocess.run(
                                        ["adb"] + serial_arg + ["shell", "getprop ro.serialno"],
                                        capture_output=True, text=True, timeout=5
                                    )
                                    device_serial = serial_result.stdout.strip() if serial_result.returncode == 0 else None
                                    if device_serial:
                                        optimal_config = cache.get_optimal_config(device_serial)
                                        actual_codec = optimal_config.codec
                                        logger.info(f"Auto-selected codec for {device_serial}: {actual_codec}")
                                    else:
                                        actual_codec = "h264"
                                        logger.warning("Could not get device serial, using h264")
                                except Exception as e:
                                    actual_codec = "h264"
                                    logger.warning(f"Failed to auto-select codec: {e}, using h264")

                            server_cmd = (
                                f"CLASSPATH={remote_path} app_process / "
                                f"com.genymobile.scrcpy.Server 3.3.4 log_level=info "
                                f"discovery_port={discovery_port} "
                                f"control_port={control_port} video_port={video_port} audio_port={audio_port} file_port={file_port} "
                                f"video_codec={actual_codec} video_bit_rate={video_bitrate} max_fps={max_fps} "
                                f"bitrate_mode={bitrate_mode} i_frame_interval={i_frame_interval} "
                                f"stay_alive={stay_alive_str} "
                            )

                            # FEC 参数
                            if fec_enabled:
                                server_cmd += f"fec_enabled=true fec_group_size={fec_group_size} fec_parity_count={fec_parity_count} "
                            else:
                                if video_fec_enabled:
                                    server_cmd += f"video_fec_enabled=true "
                                if audio_fec_enabled:
                                    server_cmd += f"audio_fec_enabled=true "
                                if video_fec_enabled or audio_fec_enabled:
                                    server_cmd += f"fec_group_size={fec_group_size} fec_parity_count={fec_parity_count} "

                            server_cmd += (
                                f"video={'true' if video_enabled else 'false'} {audio_params} control=true send_device_meta=true send_dummy_byte=true cleanup=false"
                            )

                            shell_cmd = f"nohup sh -c '{server_cmd}' > /data/local/tmp/scrcpy_server.log 2>&1 &"

                            logger.info(f"Start command: adb shell {shell_cmd}")
                            subprocess.run(
                                adb_cmd(["shell", shell_cmd]),
                                capture_output=True, text=True, timeout=10
                            )

                            # 等待服务端启动
                            server_started = False
                            for i in range(10):
                                time.sleep(0.5)
                                if check_server_running():
                                    server_started = True
                                    break

                            result_data["start"] = {
                                "status": "running" if server_started else "failed",
                                "stay_alive": stay_alive,
                                "ports": {"control": control_port, "video": video_port, "audio": audio_port},
                                "video": {"codec": actual_codec, "bitrate": video_bitrate, "max_fps": max_fps, "bitrate_mode": bitrate_mode},  # Use actual_codec, not video_codec
                                "audio": audio_enabled,
                                "fec": {"enabled": fec_enabled, "video": video_fec_enabled, "audio": audio_fec_enabled}
                            }

                            if server_started:
                                result_data["message"] = "Server pushed and started"
                                result_data["action"] = "push_and_start"

                                # 获取设备 IP
                                try:
                                    ip_result = subprocess.run(
                                        adb_cmd(["shell", "ip route | awk '/src/ {print $NF}' | head -1"]),
                                        capture_output=True, text=True, timeout=5
                                    )
                                    device_ip = ip_result.stdout.strip() if ip_result.returncode == 0 else None
                                    if device_ip:
                                        result_data["start"]["device_ip"] = device_ip
                                        result_data["next_step"] = f"Use connect(connection_mode='network', device_id='{device_ip}') to connect"
                                except Exception as e:
                                    result_data["start"]["ip_check_error"] = str(e)
                            else:
                                result_data["success"] = False
                                result_data["message"] = "Server pushed but failed to start"
                                result_data["start"]["hint"] = "Check /data/local/tmp/scrcpy_server.log on device"
                        else:
                            result_data["message"] = "Server pushed" if do_push else "No action (push=false, start=false)"
                            result_data["action"] = "push_only" if do_push else "none"

                        # 自动连接（如果请求且条件满足）
                        if auto_connect_after_push and result_data.get("success") and device_ip:
                            logger.info(f"[auto_connect] Attempting network connection to {device_ip}")
                            connect_args = {
                                "connection_mode": "network",
                                "device_id": device_ip,
                                "video": video_enabled,
                                "audio": audio_enabled,
                                "audio_dup": audio_dup,  # Pass audio_dup to connection
                                "codec": actual_codec,  # Use resolved codec, not "auto"
                                "bitrate": video_bitrate,
                                "max_fps": max_fps
                            }
                            server = self._ensure_connected(**connect_args)
                            if server is not None:
                                result_data["auto_connect"] = {
                                    "success": True,
                                    "device_ip": device_ip,
                                    "mode": "network"
                                }
                                result_data["message"] = "Server pushed and connected via network"
                            else:
                                result_data["auto_connect"] = {
                                    "success": False,
                                    "device_ip": device_ip,
                                    "error": "Connection failed"
                                }
                                result_data["message"] = "Server pushed but auto-connect failed"

                        result_text = json.dumps(result_data, ensure_ascii=False, indent=2)
                        return {"content": [{"type": "text", "text": result_text}]}
                    except subprocess.TimeoutExpired:
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": "Timeout: adb command took too long"}, ensure_ascii=False)}]}
                    except FileNotFoundError:
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": "adb command not found in PATH"}, ensure_ascii=False)}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)}]}

                # stop_server - 停止设备上的服务端
                if tool_name == "stop_server":
                    try:
                        import subprocess
                        device_id = arguments.get("device_id")

                        def adb_cmd(args):
                            if device_id:
                                return ["adb", "-s", device_id] + args
                            return ["adb"] + args

                        result = subprocess.run(
                            adb_cmd(["shell", "pkill -9 -f app_process"]),
                            capture_output=True, text=True, timeout=5
                        )

                        result_data = {
                            "success": True,
                            "message": "Server stopped",
                            "device_id": device_id
                        }
                        result_text = json.dumps(result_data, ensure_ascii=False, indent=2)
                        return {"content": [{"type": "text", "text": result_text}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)}]}

                # restart_adb - 重启 ADB 服务器
                if tool_name == "restart_adb":
                    try:
                        from scrcpy_py_ddlx.core.adb import ADBManager
                        adb = ADBManager()

                        logger.info("Restarting ADB server via MCP tool...")
                        success = adb.restart_adb_server()

                        if success:
                            # Also check connection
                            connection_ok = adb.check_adb_connection(max_retries=1)
                            result_data = {
                                "success": True,
                                "message": "ADB server restarted successfully",
                                "connection_ok": connection_ok
                            }
                        else:
                            result_data = {
                                "success": False,
                                "error": "Failed to restart ADB server"
                            }

                        result_text = json.dumps(result_data, ensure_ascii=False, indent=2)
                        return {"content": [{"type": "text", "text": result_text}]}
                    except Exception as e:
                        logger.error(f"restart_adb error: {e}")
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)}]}

                # discover_devices 操作特殊处理 - 不需要先连接
                if tool_name == "discover_devices":
                    try:
                        all_devices = []

                        # 1. 获取已连接的 ADB 设备
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

                                    all_devices.append({
                                        "type": "adb",
                                        "serial": d.serial,
                                        "model": d.model,
                                        "ip": ip,
                                        "state": d.state,
                                        "ready": True
                                    })
                                    logger.info(f"[ADB] Found: {d.model or d.serial} ({ip or d.serial})")

                        except Exception as e:
                            logger.warning(f"[ADB] Scan failed: {e}")

                        # 2. UDP 广播发现 scrcpy 服务端（快速，约 2 秒）
                        try:
                            from scrcpy_py_ddlx.client.udp_wake import discover_devices as udp_discover
                            logger.info("[UDP] Broadcasting discovery...")
                            udp_devices = udp_discover(timeout=2.0)

                            for dev in udp_devices:
                                # 避免重复
                                if not any(d.get('ip') == dev['ip'] for d in all_devices):
                                    all_devices.append({
                                        "type": "udp_server",
                                        "name": dev['name'],
                                        "ip": dev['ip'],
                                        "ready": True
                                    })
                                    logger.info(f"[UDP] Found server: {dev['name']} ({dev['ip']})")

                        except Exception as e:
                            logger.warning(f"[UDP] Discovery failed: {e}")

                        # 3. 获取本机 IP
                        local_ip = "unknown"
                        try:
                            import socket
                            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                            s.connect(("8.8.8.8", 80))
                            local_ip = s.getsockname()[0]
                            s.close()
                        except Exception:
                            pass

                        result = {
                            "success": True,
                            "local_ip": local_ip,
                            "found": len(all_devices),
                            "devices": all_devices
                        }

                        # 添加提示
                        if not all_devices:
                            result["hint"] = "No devices found. For ADB mode, connect device via USB/WiFi. For network mode, ensure scrcpy server is running on device."

                        result_text = json.dumps(result, ensure_ascii=False, indent=2)
                        return {"content": [{"type": "text", "text": result_text}]}
                    except Exception as e:
                        import traceback
                        logger.exception("discover_devices error")
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": str(e), "traceback": traceback.format_exc()}, ensure_ascii=False)}]}

                # screenshot 操作特殊处理 - 自动生成文件名 + 处理 video=False 情况
                if tool_name == "screenshot":
                    from datetime import datetime
                    from pathlib import Path
                    screenshots_dir = Path("screenshots")
                    screenshots_dir.mkdir(exist_ok=True)
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]  # 毫秒精度

                    # 获取格式和质量参数
                    img_format = arguments.pop("format", "jpg")  # 使用 pop 移除，不传给 server
                    quality = arguments.get("quality", 80)

                    # 根据格式生成文件名
                    filename = str(screenshots_dir / f"screenshot_{timestamp}.{img_format}")
                    arguments["filename"] = filename
                    arguments["quality"] = quality

                # record_audio 操作特殊处理 - 处理 audio=False 情况 + 格式选择
                if tool_name == "record_audio":
                    # 检查音频是否启用
                    if self._current_config and not self._current_config.audio:
                        return {
                            "content": [{"type": "text", "text": json.dumps({
                                "success": False,
                                "error": "Audio not enabled",
                                "hint": "Reconnect with audio=true to enable recording, or use ADB to record directly on device."
                            }, ensure_ascii=False)}]}
                    from datetime import datetime
                    from pathlib import Path
                    recordings_dir = Path("recordings")
                    recordings_dir.mkdir(exist_ok=True)
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

                    # 获取格式参数，默认 auto（透传原始格式）
                    audio_format = arguments.get("format", "auto")

                    # 根据格式确定文件扩展名和录制模式
                    # auto -> 透传原始 OPUS -> .ogg
                    # wav -> 解码为 PCM -> .wav
                    # opus -> 解码后重新编码 -> .opus
                    # mp3 -> 解码后重新编码 -> .mp3
                    format_to_ext = {
                        "auto": "ogg",   # 透传模式：OPUS -> OGG 容器
                        "wav": "wav",    # 解码模式：PCM WAV
                        "opus": "opus",  # 转码模式：重新编码为 OPUS
                        "mp3": "mp3",    # 转码模式：重新编码为 MP3
                    }

                    ext = format_to_ext.get(audio_format, "ogg")
                    filename = str(recordings_dir / f"recording_{timestamp}.{ext}")
                    arguments["filename"] = filename

                    # 添加录制模式标记
                    if audio_format == "auto":
                        arguments["passthrough"] = True
                        arguments["auto_convert_to"] = None
                    elif audio_format == "wav":
                        arguments["passthrough"] = False
                        arguments["auto_convert_to"] = None
                    else:
                        # opus 或 mp3：解码后重新编码
                        arguments["passthrough"] = False
                        arguments["auto_convert_to"] = audio_format

                # record_video 操作特殊处理 - 自动生成文件名
                if tool_name == "record_video":
                    from datetime import datetime
                    from pathlib import Path
                    recordings_dir = Path("recordings")
                    recordings_dir.mkdir(exist_ok=True)
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    # Use MKV format if audio is enabled (supports OPUS passthrough)
                    audio_enabled = self._current_config and self._current_config.audio
                    ext = "mkv" if audio_enabled else "mp4"
                    filename = str(recordings_dir / f"video_{timestamp}.{ext}")
                    arguments["filename"] = filename

                # 其他工具需要确保已连接
                server = self._ensure_connected()
                if server is None:
                    return {
                        "content": [{"type": "text", "text": json.dumps({
                            "error": "Not connected to device",
                            "hint": "CRITICAL: Call discover_devices() FIRST to scan local network for devices, then use connect(device_id='IP:PORT') to connect."
                        }, ensure_ascii=False)}]}

                # 需要服务端存活检查的操作列表
                # 这些操作在服务端停止后不应该返回假的成功
                server_required_tools = {
                    'screenshot', 'get_clipboard', 'set_clipboard',
                    'tap', 'swipe', 'long_press', 'scroll', 'pinch',
                    'press_back', 'press_home', 'press_menu', 'press_power',
                    'volume_up', 'volume_down', 'wake_up', 'inject_keycode',
                    'type_text', 'start_app', 'stop_app',
                    'record_audio', 'stop_audio_recording', 'is_recording_audio',
                    'record_video', 'stop_video_recording', 'is_recording_video',
                    'get_screen_power_state'
                }

                # 对于需要服务端存活的操作，进行存活检查
                if tool_name in server_required_tools:
                    alive_error = self._ensure_server_alive_for_operation(tool_name)
                    if alive_error:
                        return {"content": [{"type": "text", "text": json.dumps(alive_error, ensure_ascii=False)}]}

                # ==================== 文件传输工具处理 ====================
                # 文件传输支持两种模式：
                # - ADB 模式：使用 adb push/pull/shell 命令
                # - 网络模式：使用独立文件通道

                if tool_name == "list_dir":
                    path = arguments.get("path", "/sdcard")
                    try:
                        # Debug: check client and state
                        if self._client is None:
                            return {"content": [{"type": "text", "text": json.dumps({
                                "success": False, "error": "No client connected"
                            }, ensure_ascii=False)}]}
                        logger.info(f"list_dir: _client={self._client}, _client.state={self._client.state}")
                        logger.info(f"list_dir: network_mode={self._client.state.network_mode}, connected={self._client.state.connected}")
                        entries = self._client.list_dir(path)
                        # Debug: log network_mode value
                        logger.debug(f"list_dir: network_mode={self._client.state.network_mode}")
                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": True,
                            "path": path,
                            "entries": entries,
                            "count": len(entries),
                            "mode": "network" if self._client.state.network_mode else "adb"
                        }, ensure_ascii=False)}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": False,
                            "error": str(e)
                        }, ensure_ascii=False)}]}

                if tool_name == "pull_file":
                    device_path = arguments.get("device_path")
                    local_path = arguments.get("local_path")
                    if not device_path or not local_path:
                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": False,
                            "error": "Both device_path and local_path are required"
                        }, ensure_ascii=False)}]}

                    try:
                        self._client.pull_file(device_path, local_path)

                        from pathlib import Path
                        local_file = Path(local_path)
                        size = local_file.stat().st_size if local_file.exists() else 0

                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": True,
                            "device_path": device_path,
                            "local_path": local_path,
                            "size": size,
                            "mode": "network" if self._client.state.network_mode else "adb"
                        }, ensure_ascii=False)}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": False,
                            "error": str(e)
                        }, ensure_ascii=False)}]}

                if tool_name == "push_file":
                    local_path = arguments.get("local_path")
                    device_path = arguments.get("device_path")
                    if not local_path or not device_path:
                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": False,
                            "error": "Both local_path and device_path are required"
                        }, ensure_ascii=False)}]}

                    try:
                        from pathlib import Path
                        local_file = Path(local_path)
                        if not local_file.exists():
                            return {"content": [{"type": "text", "text": json.dumps({
                                "success": False,
                                "error": f"Local file not found: {local_path}"
                            }, ensure_ascii=False)}]}

                        self._client.push_file(local_path, device_path)

                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": True,
                            "local_path": local_path,
                            "device_path": device_path,
                            "size": local_file.stat().st_size,
                            "mode": "network" if self._client.state.network_mode else "adb"
                        }, ensure_ascii=False)}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": False,
                            "error": str(e)
                        }, ensure_ascii=False)}]}

                if tool_name == "delete_file":
                    device_path = arguments.get("device_path")
                    if not device_path:
                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": False,
                            "error": "device_path is required"
                        }, ensure_ascii=False)}]}

                    try:
                        success = self._client.delete_file(device_path)
                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": success,
                            "device_path": device_path,
                            "mode": "network" if self._client.state.network_mode else "adb"
                        }, ensure_ascii=False)}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": False,
                            "error": str(e)
                        }, ensure_ascii=False)}]}

                if tool_name == "make_dir":
                    device_path = arguments.get("device_path")
                    if not device_path:
                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": False,
                            "error": "device_path is required"
                        }, ensure_ascii=False)}]}

                    try:
                        success = self._client.make_dir(device_path)
                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": success,
                            "device_path": device_path,
                            "mode": "network" if self._client.state.network_mode else "adb"
                        }, ensure_ascii=False)}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": False,
                            "error": str(e)
                        }, ensure_ascii=False)}]}

                if tool_name == "file_stat":
                    device_path = arguments.get("device_path")
                    if not device_path:
                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": False,
                            "error": "device_path is required"
                        }, ensure_ascii=False)}]}

                    try:
                        info = self._client.file_stat(device_path)
                        if info is None:
                            return {"content": [{"type": "text", "text": json.dumps({
                                "success": True,
                                "exists": False,
                                "path": device_path
                            }, ensure_ascii=False)}]}

                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": True,
                            "exists": True,
                            "path": device_path,
                            "type": info.get("type"),
                            "size": info.get("size"),
                            "mtime": info.get("mtime"),
                            "mode": "network" if self._client.state.network_mode else "adb"
                        }, ensure_ascii=False)}]}
                    except Exception as e:
                        return {"content": [{"type": "text", "text": json.dumps({
                            "success": False,
                            "error": str(e)
                        }, ensure_ascii=False)}]}

                # ==================== screenshot 处理 ====================
                # screenshot: 根据 video 配置和连接模式选择方法
                if tool_name == "screenshot":
                    video_enabled = self._current_config and self._current_config.video
                    connection_mode = getattr(self._current_config, 'connection_mode', 'adb_tunnel') if self._current_config else 'adb_tunnel'

                    if video_enabled:
                        # 模式一：实时预览模式 - 从视频流截取当前帧 (~16ms)
                        logger.info("Screenshot from video stream (video=true)")
                        result = server.screenshot(**arguments)
                        if result.get("success"):
                            result["method"] = "video_stream"
                            # 检查是否可能息屏
                            screen_warning = self._check_screen_off()
                            if screen_warning:
                                result["warning"] = screen_warning
                                logger.warning(f"Screen off detected during screenshot: {screen_warning}")
                    elif connection_mode == "adb_tunnel":
                        # ADB 隧道模式 + video=False：直接用 ADB screencap (~300ms)
                        logger.info("Screenshot via ADB screencap (video=false)")
                        try:
                            import subprocess
                            from pathlib import Path
                            from PIL import Image
                            import io

                            device_serial = getattr(self._client.state, 'device_serial', None) if self._client else None

                            # 构建 ADB 命令
                            adb_cmd = ['adb']
                            if device_serial:
                                adb_cmd.extend(['-s', device_serial])
                            adb_cmd.extend(['exec-out', 'screencap', '-p'])

                            # 执行截图
                            proc = subprocess.run(adb_cmd, capture_output=True, timeout=10)
                            if proc.returncode != 0:
                                raise Exception(f"ADB screencap failed: {proc.stderr.decode()}")

                            # 获取格式和质量参数
                            img_format = arguments.get('format', 'jpg') if 'format' in arguments else 'jpg'
                            quality = arguments.get('quality', 80)

                            # 打开图片
                            img = Image.open(io.BytesIO(proc.stdout))
                            width, height = img.size

                            # 根据格式保存
                            timestamp = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                            filename = str(Path("screenshots") / f"screenshot_{timestamp}.{img_format}")
                            Path("screenshots").mkdir(exist_ok=True)

                            if img_format.lower() in ('jpg', 'jpeg'):
                                # JPEG with quality
                                if img.mode == 'RGBA':
                                    img = img.convert('RGB')  # JPEG doesn't support alpha
                                img.save(filename, 'JPEG', quality=quality)
                            else:
                                # PNG (lossless)
                                img.save(filename, 'PNG')

                            result = {
                                "success": True,
                                "filename": filename,
                                "width": width,
                                "height": height,
                                "orientation": "portrait" if height > width else "landscape",
                                "format": img_format,
                                "quality": quality if img_format.lower() in ('jpg', 'jpeg') else None,
                                "method": "adb_screencap"
                            }
                        except Exception as e:
                            logger.warning(f"ADB screencap failed: {e}")
                            result = {
                                "success": False,
                                "error": str(e),
                                "hint": "Check ADB connection"
                            }
                    else:
                        # 网络模式 + video=False：使用 REQUEST_VIDEO_FRAME 控制消息
                        logger.info("Screenshot via REQUEST_VIDEO_FRAME (network mode, video=false)")
                        try:
                            server._client.request_video_frame()
                            import time
                            time.sleep(0.5)
                            result = server.screenshot(**arguments)
                        except Exception as e:
                            logger.warning(f"request_video_frame failed: {e}")
                            result = {
                                "success": False,
                                "error": f"Screenshot failed: {e}",
                                "hint": "Reconnect with video=true"
                            }
                else:
                    # 调用 MCP 服务器的方法
                    method = getattr(server, tool_name, None)
                    if method is None:
                        return {
                            "content": [{"type": "text", "text": json.dumps({"error": f"Unknown tool: {tool_name}"}, ensure_ascii=False)}]}

                    # 执行方法
                    result = method(**arguments)

                # 如果是 screenshot，转换为绝对路径返回，并添加明确的尺寸信息
                if tool_name == "screenshot" and result.get("success") and "filename" in result:
                    result["filepath"] = str(Path(result["filename"]).absolute())
                    result["message"] = f"Screenshot saved to: {result['filepath']}"

                    # 添加明确的尺寸信息（避免 AI 混淆）
                    if "shape" in result:
                        # numpy shape 格式: (height, width, channels)
                        height = result["shape"][0]
                        width = result["shape"][1]
                        result["width"] = width
                        result["height"] = height
                        result["orientation"] = "portrait" if height > width else "landscape"
                        # 移除 shape 字段，避免混淆
                        del result["shape"]

                # 如果是录音工具，转换为绝对路径返回
                if tool_name in ("record_audio", "stop_audio_recording") and result.get("success") and "filename" in result:
                    result["filepath"] = str(Path(result["filename"]).absolute())
                    result["message"] = f"Audio recording saved to: {result['filepath']}"

                # 如果是视频录制，转换为绝对路径返回
                if tool_name == "record_video" and result.get("success") and "filename" in result:
                    result["filepath"] = str(Path(result["filename"]).absolute())
                    result["message"] = f"Video recording saved to: {result['filepath']}"

                # 如果是 get_state，添加明确的尺寸信息
                if tool_name == "get_state" and result.get("connected") and "device_size" in result:
                    device_size = result["device_size"]
                    if len(device_size) >= 2:
                        # device_size 格式: [width, height]
                        width = device_size[0]
                        height = device_size[1]
                        result["width"] = width
                        result["height"] = height
                        result["orientation"] = "portrait" if height > width else "landscape"

                # 格式化返回结果
                result_text = json.dumps(result, ensure_ascii=False, indent=2)
                return {
                    "content": [{"type": "text", "text": result_text}]
                }

            except Exception as e:
                logger.exception(f"调用工具 {tool_name} 时出错")
                return {
                    "content": [{"type": "text", "text": json.dumps({"error": str(e)}, ensure_ascii=False)}]
                }


# 全局处理器
handler = ScrcpyMCPHandler()


async def handle_mcp_request(request: Request) -> JSONResponse:
    """处理 MCP JSON-RPC 请求"""
    body = {}  # 预先初始化，防止异常处理时 UnboundLocalError
    try:
        body = await request.json()
        logger.debug(f"收到请求: {json.dumps(body, ensure_ascii=False)[:200]}")

        request_method = body.get("method")
        request_id = body.get("id")
        params = body.get("params", {})

        if request_method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "serverInfo": SERVER_INFO,
                    "capabilities": {
                        "tools": {},
                        "resources": {},
                        "prompts": {}
                    }
                }
            }
        elif request_method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": TOOLS
                }
            }
        elif request_method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})

            logger.info(f"调用工具: {tool_name} 参数: {tool_args}")
            result = handler.call_tool(tool_name, tool_args)
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result
            }
        elif request_method == "resources/list":
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "resources": RESOURCES
                }
            }
        elif request_method == "prompts/list":
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "prompts": PROMPTS
                }
            }
        else:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {request_method}"
                }
            }

        logger.debug(f"响应: {json.dumps(response, ensure_ascii=False)[:200]}")
        return JSONResponse(response)

    except Exception as e:
        logger.exception("处理请求时出错")
        response = {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {
                "code": -32603,
                "message": f"Internal error: {str(e)}"
            }
        }
        return JSONResponse(response, status_code=500)


async def health_check(request: Request) -> JSONResponse:
    """健康检查端点"""
    is_connected = handler._is_client_connected()

    device_info = {}
    if is_connected:
        device_info = {
            "name": handler._client.device_name,
            "size": handler._client.device_size,
            "codec": handler._client.state.codec_id
        }

    return JSONResponse({
        "status": "healthy",
        "service": "scrcpy-http-mcp-server",
        "connected": is_connected,
        "device": device_info
    })


# 定义路由
routes = [
    Route("/mcp", handle_mcp_request, methods=["POST"]),
    Route("/mcp/", handle_mcp_request, methods=["POST"]),  # 支持尾部斜杠
    Route("/health", health_check, methods=["GET"]),
]


# Auto-connect settings (set by command line args)
_auto_connect = False
_auto_preview = False
_network_device = None
_network_push_device = None  # --network-push mode
_enable_video = True


async def on_startup():
    """Auto-connect to device on server startup (if configured)."""
    import asyncio
    import time

    if not _auto_connect and not _network_device and not _network_push_device:
        return

    # Wait a bit for server to be fully ready
    await asyncio.sleep(0.5)

    logger.info("=" * 50)
    logger.info("[AUTO_CONNECT] Starting auto-connect sequence...")
    logger.info("=" * 50)

    try:
        if _network_push_device:
            # Network mode with USB push: Push server via USB first, then connect by IP
            device_ip = _network_push_device
            logger.info(f"[AUTO_CONNECT] Network+Push mode: will push via USB then connect to {device_ip}")
            print(f"\n[AUTO_CONNECT] Network+Push mode: {device_ip}")

            # Step 1: Detect USB device
            logger.info("[AUTO_CONNECT] Step 1: Detecting USB device...")
            print("[AUTO_CONNECT] Step 1: Detecting USB device...")
            list_result = await _execute_tool("list_devices", {})
            devices = list_result.get("devices", [])

            if not devices:
                # Try to restart ADB and retry
                logger.warning("[AUTO_CONNECT] No devices found, attempting ADB restart...")
                print("[AUTO_CONNECT] No devices found, attempting ADB restart...")

                restart_result = await _execute_tool("restart_adb", {})
                if restart_result.get("success"):
                    # Retry device detection after ADB restart
                    await asyncio.sleep(1.0)
                    list_result = await _execute_tool("list_devices", {})
                    devices = list_result.get("devices", [])

                if not devices:
                    logger.error("[AUTO_CONNECT] No USB devices found! Connect device via USB first.")
                    print("[AUTO_CONNECT] No USB devices found! Connect device via USB first.")
                    return

            device = devices[0]
            device_id = device.get("id") or device.get("serial")
            device_name = device.get("name", device_id)
            logger.info(f"[AUTO_CONNECT] Found USB device: {device_name} ({device_id})")
            print(f"[AUTO_CONNECT] Found USB device: {device_name}")

            # Step 2: Push server via USB (one-time mode)
            logger.info("[AUTO_CONNECT] Step 2: Pushing server via USB...")
            print("[AUTO_CONNECT] Step 2: Pushing server via USB...")
            push_params = {
                "device_id": device_id,
                "video": _enable_video,
                "audio": DEFAULT_AUDIO_ENABLED,
                "audio_dup": DEFAULT_AUDIO_DUP,
                "auto_connect": False,  # We'll connect manually via network
            }
            push_result = await _execute_tool("push_server_onetime", push_params)

            if not push_result.get("success"):
                logger.error(f"[AUTO_CONNECT] Push failed: {push_result.get('error')}")
                print(f"[AUTO_CONNECT] Push failed: {push_result.get('error')}")
                return

            logger.info("[AUTO_CONNECT] Server pushed successfully!")
            print("[AUTO_CONNECT] Server pushed successfully!")

            # Get the actual codec used by the server
            start_info = push_result.get("start", {})
            video_info = start_info.get("video", {})
            actual_codec = video_info.get("codec", "h265")  # Default to h265 if not found
            logger.info(f"[AUTO_CONNECT] Server started with codec: {actual_codec}")

            # Step 3: Wait a bit for server to start
            await asyncio.sleep(1.0)

            # Step 4: Connect via network
            logger.info(f"[AUTO_CONNECT] Step 3: Connecting via network to {device_ip}...")
            print(f"[AUTO_CONNECT] Step 3: Connecting via network to {device_ip}...")
            connect_params = {
                "connection_mode": "network",
                "device_id": device_ip,
                "video": _enable_video,
                "audio": DEFAULT_AUDIO_ENABLED,
                "audio_dup": DEFAULT_AUDIO_DUP,
                "codec": actual_codec,  # Use the same codec as the server
            }
            result = await _execute_tool("connect", connect_params)

            if result.get("success"):
                logger.info(f"[AUTO_CONNECT] Connected successfully!")
                print(f"[AUTO_CONNECT] Connected to {device_ip} via network!")

                # Auto-start preview
                await asyncio.sleep(0.5)
                logger.info("[AUTO_CONNECT] Starting preview window...")
                print("[AUTO_CONNECT] Starting preview window...")
                preview_result = await _execute_tool("start_preview", {})
                if preview_result.get("success"):
                    logger.info("[AUTO_CONNECT] Preview window started!")
                    print("[AUTO_CONNECT] Preview window started!")
                else:
                    logger.warning(f"[AUTO_CONNECT] Preview failed: {preview_result.get('error')}")
                    print(f"[AUTO_CONNECT] Preview failed: {preview_result.get('error')}")
            else:
                logger.error(f"[AUTO_CONNECT] Connection failed: {result.get('error')}")
                print(f"[AUTO_CONNECT] Connection failed: {result.get('error')}")

        elif _network_device:
            # Network mode: Connect to device by IP
            logger.info(f"[AUTO_CONNECT] Network mode: connecting to {_network_device}")
            print(f"\n[AUTO_CONNECT] Network mode: connecting to {_network_device}...")

            # Parse connection params
            params = {
                "connection_mode": "network",
                "device_id": _network_device,
                "video": _enable_video,
                "audio": DEFAULT_AUDIO_ENABLED,
                "audio_dup": DEFAULT_AUDIO_DUP,
            }

            # Call connect tool
            result = await _execute_tool("connect", params)

            if result.get("success"):
                logger.info(f"[AUTO_CONNECT] Connected successfully!")
                print(f"[AUTO_CONNECT] Connected to {_network_device}!")

                # Auto-start preview if requested
                if _auto_preview or _network_device:  # --network implies preview
                    await asyncio.sleep(0.5)  # Wait for connection to stabilize
                    logger.info("[AUTO_CONNECT] Starting preview window...")
                    print("[AUTO_CONNECT] Starting preview window...")
                    preview_result = await _execute_tool("start_preview", {})
                    if preview_result.get("success"):
                        logger.info("[AUTO_CONNECT] Preview window started!")
                        print("[AUTO_CONNECT] Preview window started!")
                    else:
                        logger.warning(f"[AUTO_CONNECT] Preview failed: {preview_result.get('error')}")
                        print(f"[AUTO_CONNECT] Preview failed: {preview_result.get('error')}")
            else:
                logger.error(f"[AUTO_CONNECT] Connection failed: {result.get('error')}")
                print(f"[AUTO_CONNECT] Connection failed: {result.get('error')}")

        elif _auto_connect:
            # USB mode: Auto-connect to first available device
            logger.info("[AUTO_CONNECT] USB mode: detecting devices...")
            print("\n[AUTO_CONNECT] Detecting USB devices...")

            # First, list devices
            list_result = await _execute_tool("list_devices", {})
            devices = list_result.get("devices", [])

            if not devices:
                # Try to restart ADB and retry
                logger.warning("[AUTO_CONNECT] No devices found, attempting ADB restart...")
                print("[AUTO_CONNECT] No devices found, attempting ADB restart...")

                restart_result = await _execute_tool("restart_adb", {})
                if restart_result.get("success"):
                    # Retry device detection after ADB restart
                    await asyncio.sleep(1.0)
                    list_result = await _execute_tool("list_devices", {})
                    devices = list_result.get("devices", [])

                if not devices:
                    logger.error("[AUTO_CONNECT] No devices found!")
                    print("[AUTO_CONNECT] No devices found!")
                    return

            # Get first device
            device = devices[0]
            device_id = device.get("id") or device.get("serial")
            device_name = device.get("name", device_id)

            logger.info(f"[AUTO_CONNECT] Found device: {device_name} ({device_id})")
            print(f"[AUTO_CONNECT] Found device: {device_name}")

            # Connect
            params = {
                "connection_mode": "adb_tunnel",
                "device_id": device_id,
                "video": _enable_video,
                "audio": DEFAULT_AUDIO_ENABLED,
                "audio_dup": DEFAULT_AUDIO_DUP,
            }

            result = await _execute_tool("connect", params)

            if result.get("success"):
                logger.info(f"[AUTO_CONNECT] Connected successfully!")
                print(f"[AUTO_CONNECT] Connected to {device_name}!")

                # Auto-start preview if requested
                if _auto_preview:
                    await asyncio.sleep(0.5)  # Wait for connection to stabilize
                    logger.info("[AUTO_CONNECT] Starting preview window...")
                    print("[AUTO_CONNECT] Starting preview window...")
                    preview_result = await _execute_tool("start_preview", {})
                    if preview_result.get("success"):
                        logger.info("[AUTO_CONNECT] Preview window started!")
                        print("[AUTO_CONNECT] Preview window started!")
                    else:
                        logger.warning(f"[AUTO_CONNECT] Preview failed: {preview_result.get('error')}")
                        print(f"[AUTO_CONNECT] Preview failed: {preview_result.get('error')}")
            else:
                logger.error(f"[AUTO_CONNECT] Connection failed: {result.get('error')}")
                print(f"[AUTO_CONNECT] Connection failed: {result.get('error')}")

    except Exception as e:
        logger.exception(f"[AUTO_CONNECT] Error: {e}")
        print(f"[AUTO_CONNECT] Error: {e}")

    logger.info("=" * 50)


async def _execute_tool(tool_name: str, params: dict) -> dict:
    """Execute a tool internally (without HTTP)."""
    global handler

    try:
        # Use the global handler instance
        if handler is None:
            return {"success": False, "error": "Handler not initialized"}

        # Execute the tool using the handler's call_tool method
        result = handler.call_tool(tool_name, params)

        # Parse result - it's in MCP format {"content": [{"type": "text", "text": "..."}]}
        if result and "content" in result:
            for item in result["content"]:
                if item.get("type") == "text":
                    text = item.get("text", "{}")
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"success": False, "error": f"Invalid JSON response: {text[:100]}"}

        return {"success": False, "error": "No valid response from tool"}
    except Exception as e:
        logger.error(f"[AUTO_CONNECT] Tool execution error: {e}")
        return {"success": False, "error": str(e)}


def on_shutdown():
    """Cleanup on server shutdown."""
    import threading
    import sys
    import multiprocessing as mp

    logger.info("Server shutting down, cleaning up resources...")

    # 打印所有活跃线程
    logger.info(f"Active threads: {threading.active_count()}")
    for thread in threading.enumerate():
        logger.info(f"  Thread: {thread.name} (daemon={thread.daemon}, alive={thread.is_alive()})")

    # 打印活跃的子进程
    logger.info(f"Active children processes: {len(mp.active_children())}")
    for child in mp.active_children():
        logger.info(f"  Process: {child.name} (alive={child.is_alive()})")

    # Stop preview FIRST (before disconnecting client)
    try:
        if handler._preview_manager is not None:
            logger.info("Stopping preview...")
            handler._preview_manager.stop()
            handler._preview_manager = None
            logger.info("Preview stopped")
    except Exception as e:
        logger.warning(f"Error stopping preview: {e}")

    # 打印线程状态
    logger.info(f"After preview stop - Active threads: {threading.active_count()}")
    for thread in threading.enumerate():
        logger.info(f"  Thread: {thread.name} (daemon={thread.daemon}, alive={thread.is_alive()})")

    # 打印活跃的子进程
    logger.info(f"After preview stop - Active children: {len(mp.active_children())}")
    for child in mp.active_children():
        logger.info(f"  Process: {child.name} (alive={child.is_alive()})")

    # Disconnect client
    try:
        if handler._client is not None:
            logger.info("Disconnecting client...")
            handler._client.disconnect()
            handler._client = None
            logger.info("Client disconnected")
    except Exception as e:
        logger.warning(f"Error during client disconnect: {e}")

    # 打印线程状态
    logger.info(f"After client disconnect - Active threads: {threading.active_count()}")
    for thread in threading.enumerate():
        logger.info(f"  Thread: {thread.name} (daemon={thread.daemon}, alive={thread.is_alive()})")

    # 打印活跃的子进程
    logger.info(f"After client disconnect - Active children: {len(mp.active_children())}")
    for child in mp.active_children():
        logger.info(f"  Process: {child.name} (alive={child.is_alive()})")

    # Also try server disconnect
    try:
        if handler._server is not None:
            handler._server.disconnect()
            handler._server = None
    except Exception as e:
        logger.debug(f"Server disconnect: {e}")

    logger.info("Cleanup complete")
    logger.info(f"Final state - Threads: {threading.active_count()}, Children: {len(mp.active_children())}")

    # Force exit to bypass lingering QueueFeederThread issues
    # These daemon threads shouldn't block exit, but sometimes do on Windows
    import os
    logger.info("Forcing exit to ensure clean shutdown...")
    os._exit(0)


# Simple app without lifespan events (lifespan="off" in uvicorn)
# Auto-connect is handled in main() via background thread
app = Starlette(
    debug=False,
    routes=routes
)


def main():
    """主入口"""
    if not STARLETTE_AVAILABLE:
        print("错误: 缺少依赖")
        print("请安装: pip install starlette uvicorn")
        sys.exit(1)

    import argparse
    parser = argparse.ArgumentParser(description="Scrcpy HTTP MCP Server")
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=3359,
        help="Server port (default: 3359)"
    )
    parser.add_argument(
        "--audio",
        action="store_true",
        default=False,
        help="Enable audio streaming by default (for recording)"
    )
    parser.add_argument(
        "--audio-dup",
        action="store_true",
        default=False,
        help="Duplicate audio: play on both device and computer (Android 11+)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Server host (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--connect", "-c",
        action="store_true",
        default=False,
        help="Auto-connect to first available device on startup"
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        default=False,
        help="Auto-start preview window after connection (requires --connect)"
    )
    parser.add_argument(
        "--network",
        type=str,
        default=None,
        help="Network mode: connect to device by IP (e.g., --network 192.168.1.100). Requires server already running on device. Implies --preview"
    )
    parser.add_argument(
        "--network-push",
        type=str,
        default=None,
        dest="network_push",
        help="Network mode with USB push: push server via USB first, then connect by IP. Use this for first-time setup. Implies --preview"
    )
    parser.add_argument(
        "--video",
        action="store_true",
        default=True,
        help="Enable video streaming (default: True)"
    )
    args = parser.parse_args()

    # 保存默认音频配置到全局变量
    global DEFAULT_AUDIO_ENABLED, DEFAULT_AUDIO_DUP
    DEFAULT_AUDIO_ENABLED = args.audio
    DEFAULT_AUDIO_DUP = args.audio_dup

    # 设置自动连接参数
    global _auto_connect, _auto_preview, _network_device, _network_push_device, _enable_video
    _enable_video = args.video

    if args.network_push:
        # --network-push: 先通过 USB 推送服务器，然后通过网络连接
        _network_push_device = args.network_push
        _auto_connect = False  # 不使用普通 USB 连接
        _auto_preview = True
        logger.info(f"[AUTO_CONNECT] Network-push mode enabled: will push via USB, then connect to {args.network_push}")
    elif args.network:
        # --network 暗示 --connect 和 --preview
        _network_device = args.network
        _auto_connect = False  # 不需要 USB 模式
        _auto_preview = True
        logger.info(f"[AUTO_CONNECT] Network mode enabled: {args.network}")
    elif args.connect:
        _auto_connect = True
        _auto_preview = args.preview
        if args.preview:
            logger.info("[AUTO_CONNECT] Auto-connect with preview enabled")

    port = args.port
    host = args.host

    logger.info("=" * 70)
    logger.info(f"启动 Scrcpy HTTP MCP 服务器")
    logger.info(f"端口: {port}")
    logger.info(f"MCP 端点: http://{host}:{port}/mcp")
    logger.info(f"健康检查: http://{host}:{port}/health")
    logger.info(f"默认音频: {'启用' if DEFAULT_AUDIO_ENABLED else '禁用'}")
    logger.info("=" * 70)

    print("")
    print("=" * 70)
    print("[Scrcpy HTTP MCP Server]")
    print("[模式] 标准 HTTP POST (JSON-RPC)")
    print(f"[端点] http://{host}:{port}/mcp")
    print(f"[健康检查] http://{host}:{port}/health")
    print(f"[默认音频] {'[ON] 启用' if DEFAULT_AUDIO_ENABLED else '[OFF] 禁用'}")
    # 显示自动连接状态
    if _network_device:
        print(f"[自动连接] 网络 -> {_network_device} + 预览窗口")
    elif _auto_connect:
        mode = "USB + 预览" if _auto_preview else "USB"
        print(f"[自动连接] {mode} (检测首个设备)")
    print("")
    print("[Claude Code 配置]")
    print('{')
    print('  "mcpServers": {')
    print('    "scrcpy-http": {')
    print(f'      "url": "http://{host}:{port}/mcp"')
    print('    }')
    print('  }')
    print('}')
    print("=" * 70)
    print("")

    # 设置信号处理器 - Ctrl+C 退出
    import signal
    import os

    def signal_handler(signum, frame):
        """Handle Ctrl+C - graceful shutdown."""
        print("\n收到退出信号，正在关闭...")
        try:
            on_shutdown()
        except Exception as e:
            logger.error(f"Shutdown error: {e}")
        os._exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # Start auto-connect in background thread (since lifespan is disabled)
    if _auto_connect or _network_device or _network_push_device:
        import threading
        import asyncio

        def run_auto_connect():
            """Run auto_connect in a separate thread with its own event loop."""
            # Wait for server to start
            import time
            time.sleep(1.0)

            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(on_startup())
            except Exception as e:
                logger.error(f"Auto-connect error: {e}")
            finally:
                loop.close()

        auto_connect_thread = threading.Thread(target=run_auto_connect, daemon=True)
        auto_connect_thread.start()
        logger.info("Auto-connect thread started")

    # 启动服务器 - 禁用 lifespan 以避免 Windows 上的兼容性问题
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        loop="asyncio",
        lifespan="off"
    )


if __name__ == "__main__":
    main()
