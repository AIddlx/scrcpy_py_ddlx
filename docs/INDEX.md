# 文档索引

scrcpy-py-ddlx 完整文档索引。

---

## 快速开始

1. **[README](../README.md)** - 项目概述和快速开始
2. **[快速开始](user/quickstart.md)** - 5 分钟上手
3. **[安装指南](user/installation.md)** - 完整安装说明

---

## 用户文档

### 使用模式

| 文档 | 说明 |
|------|------|
| [Python API](user/modes/python-api.md) | 作为 Python 库使用 |
| [MCP GUI](user/modes/mcp-gui.md) | Claude Code 可视化界面 |
| [HTTP MCP](user/modes/mcp-http.md) | HTTP MCP 服务器 |
| [Direct Test](user/modes/direct-test.md) | test_direct.py 使用 |

### 其他

| 文档 | 说明 |
|------|------|
| [故障排除](user/troubleshooting.md) | 常见问题解决 |

---

## API 文档

| 文档 | 说明 |
|------|------|
| [控制方法](api/control.md) | 所有控制方法及示例 |
| [协议说明](api/protocol.md) | 协议参考 |
| [功能状态](api/CONTROL_STATUS.md) | 功能支持状态 |

---

## 开发文档

| 文档 | 说明 |
|------|------|
| [编译 Server](development/build.md) | 编译 scrcpy-server |
| [架构设计](development/ARCHITECTURE_ANALYSIS.md) | 设计文档 |
| [开发经验](development/lessons.md) | 开发经验总结 |
| [Claude Code 集成](development/CLAUDE_CODE_INTEGRATION.md) | MCP 服务器设置 |
| [MCP 服务器](development/MCP_SERVER.md) | MCP 服务器文档 |

### 重要修复

| 文档 | 说明 |
|------|------|
| [视频冻结修复](development/VIDEO_FREEZING_FIX.md) | 短视频 app 修复 |
| [流式 Demuxer 重构](development/STREAMING_DEMUXER_REFACTOR.md) | 流式重构 |
| [GET_APP_LIST 实现](development/GET_APP_LIST_IMPLEMENTATION.md) | 应用列表功能 |

---

## 历史归档

历史文档，供参考但不再维护。

### 阶段记录

- `archive/phase/PHASE1_FIXES_SUMMARY.md` - 协议兼容性修复
- `archive/phase/PHASE2_FIXES_SUMMARY.md` - 架构改进
- `archive/phase/PHASE3_FIXES_SUMMARY.md` - 组件添加
- `archive/phase/REFACTORING_*.md` - 重构计划和总结
- `archive/phase/FIX_*.md` - 各种修复报告

### 报告

- `archive/reports/` - 历史测试和清理报告

### 调研

- `archive/research/AUDIO_*.md` - 音频子系统调研
- `archive/research/CLIPBOARD_*.md` - 剪贴板同步调研
- `archive/research/SCREENSHOT_*.md` - 截图功能调研
- `archive/research/SOUNDDEVICE_*.md` - 音频设备调研
- `archive/research/` - 其他调研文档

---

## 命名规范

**注意**：Python 包名使用下划线：

| 上下文 | 格式 | 示例 |
|--------|------|------|
| **Python 导入** | `scrcpy_py_ddlx` | `from scrcpy_py_ddlx import ...` |
| **模块路径** | `scrcpy_py_ddlx/` | `scrcpy_py_ddlx/core/control.py` |
| **文件路径** | `scrcpy-py-ddlx/` | `C:\...\scrcpy-py-ddlx\README.md` |
| **项目名称** | `scrcpy-py-ddlx` | 在文档标题中 |
