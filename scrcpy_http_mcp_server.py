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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 默认音频配置（可通过 --audio 命令行参数修改）
DEFAULT_AUDIO_ENABLED = False

# MCP 协议版本和服务器信息
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {
    "name": "scrcpy-http-mcp-server",
    "version": "1.0.0",
    "protocolVersion": MCP_PROTOCOL_VERSION
}

# 统一坐标系统描述（用于所有涉及坐标的工具）
COORDINATE_SYSTEM = """
COORDINATE SYSTEM:
- Origin (0, 0): Top-left corner of the screen
- X axis: Increases from left to right (0 to width-1)
- Y axis: Increases from top to bottom (0 to height-1)
- Example: For a 1080x2400 screen (portrait), coordinates are:
  - Top-left: (0, 0)
  - Top-right: (1079, 0)
  - Bottom-left: (0, 2399)
  - Bottom-right: (1079, 2399)
  - Center: (540, 1200)
"""

# Scrcpy MCP 工具定义（47 个工具）
TOOLS = [
    # 连接管理
    {
        "name": "connect",
        "description": "Connect to an Android device. IMPORTANT: After discover_devices() finds devices, use device_id='IP:PORT' (e.g., '192.168.1.100:5555') to connect. For USB devices, use device_id from list_devices().",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device ID: use 'IP:PORT' format (e.g., '192.168.1.100:5555') for wireless devices, or serial number for USB devices"},
                "audio": {"type": "boolean", "description": "Enable audio streaming", "default": False},
                "tcpip": {"type": "boolean", "description": "Enable TCP/IP wireless mode", "default": False},
                "stay_awake": {"type": "boolean", "description": "Keep device awake", "default": True}
            }
        }
    },
    {
        "name": "disconnect",
        "description": "Disconnect from the device",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_state",
        "description": "Get current device state (name, size, connection status). IMPORTANT: If this returns 'Not connected', first call discover_devices() or list_devices() to find available devices.",
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
        "description": "Enable wireless debugging on a USB-connected device. Use this when list_devices() shows USB devices but you need wireless connection.",
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
        "description": "Connect to a device via WiFi using its IP address. The device must have wireless debugging enabled first.",
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
        "description": "Disconnect from a wireless device",
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
        "name": "discover_devices",
        "description": "CRITICAL: Auto-discover devices on local network. ALWAYS call this FIRST when list_devices() returns empty or get_state() returns 'Not connected'. Scans for ADB wireless debugging (port 5555). Returns devices you can connect to using connect(device_id='ip:port').",
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
        "description": "Capture a screenshot from the device and save to file. Returns the file path.",
        "inputSchema": {
            "type": "object",
            "properties": {}
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
    # 录音
    {
        "name": "record_audio",
        "description": "Record audio to file for a specific duration",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Output filename. Extension determines format: .wav, .opus, .mp3"},
                "duration": {"type": "number", "description": "Recording duration in seconds"},
                "format": {"type": "string", "description": "Output format: 'wav', 'opus', 'mp3'", "enum": ["wav", "opus", "mp3"]}
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
        self._lock = threading.Lock()  # 线程安全锁

    def _is_client_connected(self) -> bool:
        """检查客户端是否已连接"""
        return self._client is not None and self._client.is_connected

    def _ensure_connected(self, audio: bool = None) -> Optional[Any]:
        """确保已连接到设备

        Args:
            audio: 是否启用音频流 (None 使用命令行配置，默认 False)
        """
        # 使用全局默认配置
        if audio is None:
            audio = DEFAULT_AUDIO_ENABLED
        # 如果客户端已连接，检查音频配置是否匹配
        if self._is_client_connected():
            # 检查音频配置是否匹配
            current_audio = self._client.config.audio if hasattr(self._client, 'config') else False
            if current_audio == audio:
                return self._server
            # 音频配置不匹配，需要重新连接
            logger.info(f"Audio config changed ({current_audio} -> {audio}), reconnecting...")
            try:
                self._server.disconnect()
            except Exception:
                pass
            self._server = None
            self._client = None

        # 清理旧连接（资源泄漏修复）
        if self._server is not None:
            try:
                self._server.disconnect()
            except Exception:
                pass
            self._server = None
            self._client = None

        # 尝试重新连接
        try:
            from scrcpy_py_ddlx import create_mcp_server, ClientConfig
            # MCP 服务器需要持续解码视频以支持随时截图
            # 默认启用音频以支持录音功能
            config = ClientConfig(
                show_window=False,
                control=True,
                audio=audio,
                bitrate=2000000,  # 2 Mbps 码率
                lazy_decode=False,  # 禁用懒加载，保持视频持续解码
            )
            self._server = create_mcp_server(default_config=config, log_file=None, enable_console_log=False)
            # 自动连接 - 显式传递 audio 参数
            result = self._server.connect(audio=audio)
            # "Already connected" 也是成功的
            if result.get("success") or "Already connected" in str(result.get("error", "")):
                self._client = self._server._client

                # 连接成功后，自动安装 YADB（支持中文输入）
                device_serial = getattr(self._client.state, "device_serial", None)
                if device_serial:
                    try:
                        check_and_install_yadb(device_serial)
                    except Exception as e:
                        logger.warning(f"[YADB] 自动安装失败（中文输入可能不可用）: {e}")

                return self._server
            return None
        except Exception as e:
            logger.error(f"连接失败: {e}")
            return None

    def call_tool(self, tool_name: str, arguments: Dict) -> Dict:
        """调用工具（线程安全）"""
        with self._lock:
            try:
                # connect 操作特殊处理
                if tool_name == "connect":
                    # 获取 audio 参数
                    audio = arguments.get("audio", False)
                    if self._is_client_connected():
                        # 检查音频配置是否匹配
                        current_audio = self._client.config.audio if hasattr(self._client, 'config') else False
                        if current_audio == audio:
                            # 已连接且配置匹配，返回成功状态
                            result = {
                                "success": True,
                                "already_connected": True,
                                "device_name": self._client.device_name,
                                "device_size": self._client.device_size,
                                "audio": current_audio
                            }
                        else:
                            # 音频配置不匹配，重新连接
                            server = self._ensure_connected(audio=audio)
                            if server is not None:
                                result = {
                                    "success": True,
                                    "device_name": self._client.device_name,
                                    "device_size": self._client.device_size,
                                    "audio": audio
                                }
                            else:
                                result = {"success": False, "error": "Failed to connect"}
                    else:
                        # 尝试连接
                        server = self._ensure_connected(audio=audio)
                        if server is not None:
                            result = {
                                "success": True,
                                "device_name": self._client.device_name,
                                "device_size": self._client.device_size,
                                "audio": audio
                            }
                        else:
                            result = {"success": False, "error": "Failed to connect"}
                    result_text = json.dumps(result, ensure_ascii=False, indent=2)
                    return {"content": [{"type": "text", "text": result_text}]}

                # disconnect 操作特殊处理 - 不需要先连接
                if tool_name == "disconnect":
                    if self._server is not None:
                        result = self._server.disconnect()
                        self._client = None
                        self._server = None
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

                # discover_devices 操作特殊处理 - 不需要先连接
                if tool_name == "discover_devices":
                    try:
                        import socket
                        import concurrent.futures

                        timeout_ms = arguments.get("timeout", 500)
                        timeout_sec = timeout_ms / 1000.0

                        # 获取本机 IP 和网段
                        def get_local_network():
                            try:
                                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                                s.connect(("8.8.8.8", 80))
                                local_ip = s.getsockname()[0]
                                s.close()
                                network_prefix = '.'.join(local_ip.split('.')[:3])
                                return network_prefix, local_ip
                            except Exception as e:
                                logger.error(f"Failed to get local network: {e}")
                                return None, None

                        def check_adb_port(ip):
                            """检查指定 IP 的 5555 端口是否开放"""
                            try:
                                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                sock.settimeout(timeout_sec)
                                result = sock.connect_ex((ip, 5555))
                                sock.close()
                                return ip if result == 0 else None
                            except Exception:
                                return None

                        network_prefix, local_ip = get_local_network()
                        if not network_prefix:
                            return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": "Failed to determine local network"}, ensure_ascii=False)}]}

                        logger.info(f"Scanning network segment: {network_prefix}.0/24")
                        found_ips = []

                        # 使用线程池并发扫描
                        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
                            futures = {}
                            for i in range(1, 255):
                                ip = f"{network_prefix}.{i}"
                                futures[executor.submit(check_adb_port, ip)] = ip

                            for future in concurrent.futures.as_completed(futures):
                                result = future.result()
                                if result:
                                    found_ips.append(result)
                                    logger.info(f"Found device: {result}:5555")

                        # 尝试连接找到的设备以验证
                        from scrcpy_py_ddlx.core.adb import ADBManager
                        adb = ADBManager()
                        verified_devices = []

                        for device_ip in found_ips:
                            try:
                                # 尝试连接
                                if adb.connect_tcpip(device_ip, port=5555, timeout=10.0):
                                    # 获取设备信息
                                    devices = adb.list_devices(long_format=True)
                                    for d in devices:
                                        if device_ip in d.serial or d.device_type.value == "tcpip":
                                            verified_devices.append({
                                                "serial": d.serial,
                                                "ip": device_ip,
                                                "state": d.state,
                                                "model": d.model,
                                                "ready": d.is_ready()
                                            })
                                            break
                            except Exception as e:
                                logger.debug(f"Failed to connect to {device_ip}: {e}")

                        result = {
                            "success": True,
                            "local_ip": local_ip,
                            "network": f"{network_prefix}.0/24",
                            "scanned": 254,
                            "found": len(verified_devices),
                            "devices": verified_devices
                        }
                        result_text = json.dumps(result, ensure_ascii=False, indent=2)
                        return {"content": [{"type": "text", "text": result_text}]}
                    except Exception as e:
                        import traceback
                        logger.exception("discover_devices error")
                        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": str(e), "traceback": traceback.format_exc()}, ensure_ascii=False)}]}

                # screenshot 操作特殊处理 - 自动生成文件名
                if tool_name == "screenshot":
                    from datetime import datetime
                    screenshots_dir = Path("screenshots")
                    screenshots_dir.mkdir(exist_ok=True)
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]  # 毫秒精度
                    filename = str(screenshots_dir / f"screenshot_{timestamp}.jpg")
                    arguments["filename"] = filename

                # 其他工具需要确保已连接
                server = self._ensure_connected()
                if server is None:
                    return {
                        "content": [{"type": "text", "text": json.dumps({
                            "error": "Not connected to device",
                            "hint": "CRITICAL: Call discover_devices() FIRST to scan local network for devices, then use connect(device_id='IP:PORT') to connect."
                        }, ensure_ascii=False)}]
                    }

                # 调用 MCP 服务器的方法
                method = getattr(server, tool_name, None)
                if method is None:
                    return {
                        "content": [{"type": "text", "text": json.dumps({"error": f"Unknown tool: {tool_name}"}, ensure_ascii=False)}]
                    }

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

app = Starlette(debug=False, routes=routes)


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
        "--host",
        type=str,
        default="127.0.0.1",
        help="Server host (default: 127.0.0.1)"
    )
    args = parser.parse_args()

    # 保存默认音频配置到全局变量
    global DEFAULT_AUDIO_ENABLED
    DEFAULT_AUDIO_ENABLED = args.audio

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
    print(f"[默认音频] {'✓ 启用' if DEFAULT_AUDIO_ENABLED else '✗ 禁用'}")
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

    # 启动服务器
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
