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
| [MCP 截图参数](user/MCP_SCREENSHOT_PARAMS.md) | 截图格式、质量和推荐配置 |
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
| **[开发流程规范](development/DEVELOPMENT_WORKFLOW.md)** | **必读** - 修改代码前的流程规范 |
| [编译 Server](development/build.md) | 编译 scrcpy-server |
| [测试脚本指南](TEST_NETWORK_DIRECT_GUIDE.md) | test_network_direct.py 完整参数和用法 |
| [服务端代码分析](development/SERVER_CODE_ANALYSIS.md) | 代码结构分析（热连接基础） |
| [架构设计](development/ARCHITECTURE_ANALYSIS.md) | 设计文档 |
| [开发经验](development/lessons.md) | 开发经验总结 |
| [Claude Code 集成](development/CLAUDE_CODE_INTEGRATION.md) | MCP 服务器设置 |
| [MCP 服务器](development/MCP_SERVER.md) | MCP 服务器文档 |
| [能力协商协议](development/CAPABILITY_NEGOTIATION.md) | 编码器协商和动态配置 |
| [编解码能力检测](development/CODEC_CAPABILITY_DETECTION.md) | 手机/PC编解码器检测经验 |
| [热连接实现方案](development/HOT_CONNECTION_IMPL_PLAN.md) | 持久服务端 + 客户端唤醒 |
| [网络模式管线](development/NETWORK_PIPELINE.md) | TCP控制+UDP媒体数据流 |
| [视频/音频管线](development/VIDEO_AUDIO_PIPELINE.md) | 数据流动路径与录制设计 |
| [硬件解码器优先级](development/HARDWARE_DECODER_PRIORITY.md) | 解码器选择策略和编解码协商 |
| [预览窗口处理](development/PREVIEW_WINDOW_HANDLING.md) | 横竖屏切换、坐标转换、窗口调整 |
| [窗口缩放设计规范](development/WINDOW_RESIZE_DESIGN.md) | **必读** - 窗口缩放设计原则和代码规范 |
| [窗口缩放问题修复](development/WINDOW_RESIZE_FIXES.md) | 窗口缩放问题发现和修复记录 |
| [GUI MCP 服务器设计](development/GUI_MCP_SERVER_DESIGN.md) | GUI 控制台设计（已暂停） |
| [改进计划](development/IMPROVEMENTS.md) | Companion、热链接、服务器停止等 |

### HTTP MCP Server

| 文档 | 说明 |
|------|------|
| [文档目录](development/mcp_http_server/README.md) | 文档索引和使用指南 |
| [架构与流程](development/mcp_http_server/ARCHITECTURE.md) | **必读** - 技术栈、系统架构、数据流、安全设计 |
| [差距分析](development/mcp_http_server/GAP_ANALYSIS.md) | **必读** - 当前实现与理想设计的差距 |
| [开发规范](development/mcp_http_server/MCP_HTTP_SERVER_SPEC.md) | **必读** - 编码规范、工具定义、错误处理 |
| [工作逻辑](development/mcp_http_server/MCP_HTTP_SERVER_LOGIC.md) | 请求处理、连接管理、状态控制 |
| [升级计划](development/mcp_http_server/MCP_HTTP_SERVER_UPGRADE.md) | 功能改进计划 |

### 重要修复

| 文档 | 说明 |
|------|------|
| [视频冻结修复](development/VIDEO_FREEZING_FIX.md) | 短视频 app 修复 |
| [流式 Demuxer 重构](development/STREAMING_DEMUXER_REFACTOR.md) | 流式重构 |
| [GET_APP_LIST 实现](development/GET_APP_LIST_IMPLEMENTATION.md) | 应用列表功能 |

### 已知问题与待改进

| 文档 | 说明 | 状态 |
|------|------|------|
| [索引](development/known_issues/README.md) | 已知问题列表 |
| [屏幕旋转修复](development/known_issues/SCREEN_ROTATION_FIX.md) | CONFIG 包合并和 skipFrames 问题 | ✅ 已修复 |
| [视频录制功能](development/known_issues/video_recording.md) | MCP 动态录制视频（带音频） | ⚠️ 需重新设计 |
| [录音时长问题](development/known_issues/audio_recording_duration.md) | 录音时长可能少于设定时间 | 待改进 |

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
