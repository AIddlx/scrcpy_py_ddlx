# 已知问题与待改进

记录已识别但暂未修复的问题，以及潜在的改进方向。

---

## 已修复

| 文件 | 问题 | 状态 |
|------|------|------|
| [屏幕旋转修复](SCREEN_ROTATION_FIX.md) | 横竖屏切换导致花屏/马赛克/卡死 | ✅ 已修复 |
| [音频缓冲优化](audio_buffer_optimization.md) | 音频滞后/underrun 问题 | ✅ 已优化 |
| [编码器检测修复](encoder_detection_fix.md) | --list-encoders 无法识别 H265 | ✅ 已修复 |
| [QOpenGLWindow 输入修复](../preview_optimization/qopenglwindow_input_fix.md) | 预览窗口触摸/键盘/字符输入失效 | ✅ 已修复 |
| [VBR 静止画面兼容](vbr_static_frame_stall.md) | VBR 模式静止画面导致客户端断开 | ✅ 已修复 |
| [预览窗口黑屏修复](preview_black_screen_fix.md) | 预览窗口启动时黑屏，需点击才显示 | ✅ 已修复 |
| [Direct SHM 截图修复](direct_shm_screenshot_fix.md) | Direct SHM 模式截图返回 No frame available | ✅ 已修复 |
| [横屏触摸修复](landscape_touch_fix.md) | MCP 预览窗口横屏模式触摸失效 | ✅ 已修复 |
| [Windows 端口 3359](port_3359_windows.md) | Windows Hyper-V 保留端口导致服务器无法启动 | ✅ 已修复 |
| [SurfaceControl 截图限制](surfacecontrol_screenshot_limitation.md) | --push 模式 (video=false) 截图失败 | ✅ 已修复 |
| [Windows UTF-8 编码](windows_utf8_encoding.md) | CMD/PowerShell 中文文件名乱码 | ✅ 已修复 |
| [Stay-Alive USB 断开](stay_alive_usb_disconnect.md) | setsid 进程持久化 + `--no-auth` 完整控制 + hot-connect 自动发现 | ✅ 已修复 |
| [网络模式 USB 拔插](network_usb_disconnect.md) | 网络模式始终使用 setsid，避免 ADB 断开时服务终止 | ✅ 已修复 |
| [零拷贝 GPU 模式](zero_copy_gpu_status.md) | 零拷贝 GPU 模式需要环境变量启用 | 📖 信息 |
| [音频录制指南](../AUDIO_RECORDING_GUIDE.md) | 音频录制功能实现与常见问题 | 📖 指南 |
| [调试方法论](../DEBUG_METHODOLOGY.md) | 高效调试问题的通用方法 | 📖 指南 |

---

## 待处理

| 文件 | 问题 | 优先级 | 状态 |
|------|------|--------|------|
| [NV12 零拷贝优化](nv12_zero_copy_optimization.md) | DelayBuffer 路径使用 semi-planar 格式，预计 CPU 从 3-4% 降至 1.5% | 中 | ⏳ 待实现 |
| [I-frame 间隔不稳定](iframe_interval_issue.md) | KEY_I_FRAME_INTERVAL 参数不可靠 | 中 | ⏳ 待实现 |
| [视频录制功能](video_recording.md) | MCP 动态录制视频（带音频） | 中 | ✅ 已实现 |
| [录音时长问题](audio_recording_duration.md) | 录音时长可能少于设定时间 | 低 | 待改进 |
| [带音频视频录制](video_recording_with_audio.md) | 带音频的视频录制无法正常播放 | 高 | ❌ 失败/已隐藏 |
| [Android 11 音频弹窗](android11_audio_popup.md) | Android 11 录音时出现短暂弹窗 | 低 | ✅ 已知限制 |
| [Android 11+ 音频锁屏限制](audio_lock_screen_limitation.md) | 锁屏时启动音频失败，60秒内解锁可恢复 | 低 | ✅ 已知限制（有缓解措施） |
| [录音透传模式](audio_passthrough_recording.md) | 透传模式暂回退为转码 | 中 | ⏳ 待实现 |

---

## 优先级说明

- **高**: 影响核心功能，需要尽快修复
- **中**: 影响用户体验，计划在近期版本修复
- **低**: 边缘情况或体验优化，可延后处理

---

## 贡献指南

发现新问题时：

1. 在本目录创建新的 `.md` 文件
2. 使用模板：`issue_template.md`
3. 更新本索引文件
4. 在代码中添加 TODO 注释引用该问题
