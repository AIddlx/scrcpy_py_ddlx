# 数据格式约定

> **版本**: 1.0
> **最后更新**: 2026-02-17
> **适用范围**: 整个 scrcpy-py-ddlx 项目

---

## 概述

本文档定义了项目中所有数据格式的约定，确保各模块之间数据交换的一致性。
**所有开发者（包括 AI 助手）在修改涉及数据格式的代码前，必须先阅读本文档。**

---

## 1. 图像/帧格式

### 1.1 视频帧格式

```
┌─────────────────────────────────────────────────────────────┐
│                     视频帧数据流                             │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Server (H.264/H.265)                                       │
│       │                                                      │
│       ▼                                                      │
│  VideoDemuxer (packet)                                      │
│       │                                                      │
│       ▼                                                      │
│  VideoDecoder._decode_packet()                              │
│       │                                                      │
│       ▼                                                      │
│  VideoDecoder._frame_to_bgr()  ─────────────────────────┐   │
│       │                                                  │   │
│       │  输出: RGB24 格式                                │   │
│       │  (R-G-B 顺序)                                    │   │
│       │  匹配 QImage.Format_RGB888                       │   │
│       │                                                  │   │
│       ▼                                                  │   │
│  DelayBuffer (_frame_buffer)                            │   │
│       │                                                  │   │
│       ▼                                                  │   │
│  消费者:                                                 │   │
│  - Screen (Qt 窗口)                                      │   │
│  - PreviewManager (分离进程预览)                          │   │
│  - screenshot() 截图                                     │   │
│                                                          │   │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 帧格式规范

| 属性 | 值 | 说明 |
|------|-----|------|
| **格式** | RGB24 | 不是 BGR！ |
| **通道顺序** | R-G-B | Red, Green, Blue |
| **数据类型** | numpy.ndarray | uint8 |
| **形状** | (height, width, 3) | HWC 格式 |
| **内存布局** | C-contiguous | 连续内存 |

### 1.3 重要说明

```python
# ✅ 正确: 帧已经是 RGB 格式，直接使用
frame = decoder.get_frame()
q_img = QImage(frame.data, w, h, w * 3, QImage.Format_RGB888)

# ❌ 错误: 不要再做 BGR->RGB 转换
rgb = frame[:, :, ::-1]  # 错！会导致颜色错乱（蓝色变紫色）
```

### 1.4 为什么叫 `_frame_to_bgr` 但返回 RGB？

这是历史遗留问题。原始 scrcpy 使用 OpenCV，OpenCV 默认使用 BGR 格式。
但本项目使用 PyAV + Qt，Qt 的 `QImage.Format_RGB888` 需要 RGB 格式。

**方法名保留 `_frame_to_bgr` 是为了兼容性，但实际返回 RGB。**

---

## 2. 坐标系统

### 2.1 设备坐标

```
┌──────────────────┐
│ (0,0)            │
│                  │
│      设备屏幕     │
│                  │
│            (W-1, │
│              H-1)│
└──────────────────┘

- 原点: 左上角
- X 轴: 向右增加
- Y 轴: 向下增加
- 单位: 像素
```

### 2.2 坐标转换

当在缩放的预览窗口中点击时，需要转换坐标：

```python
# 预览窗口坐标 -> 设备坐标
def widget_to_device(widget_x, widget_y, widget_size, device_size, frame_shape):
    h, w = frame_shape[:2]
    scale = min(widget_size.width() / w, widget_size.height() / h)

    img_w = int(w * scale)
    img_h = int(h * scale)
    offset_x = (widget_size.width() - img_w) // 2
    offset_y = (widget_size.height() - img_h) // 2

    device_x = int((widget_x - offset_x) / scale)
    device_y = int((widget_y - offset_y) / scale)

    return device_x, device_y
```

---

## 3. 控制消息格式

### 3.1 预览窗口控制事件

预览窗口发送的控制事件格式：

| 事件类型 | 格式 | 说明 |
|---------|------|------|
| 触摸 | `('tap', x, y)` | x, y 是设备坐标 |
| 按键 | `('key', action)` | action: 'back', 'home', 'menu', 'enter' |

### 3.2 客户端控制消息

客户端使用的控制消息通过 `ControlMessage` 类：

```python
# 触摸事件
msg = ControlMessage(ControlMessageType.INJECT_TOUCH)
msg.set_touch(x, y, action)

# 按键事件
msg = ControlMessage(ControlMessageType.INJECT_KEYCODE)
msg.set_keycode(keycode, action)
```

---

## 4. 数据包格式

### 4.1 Scrcpy 数据包头 (12 字节)

```
┌─────────────┬─────────────┬─────────────┐
│  PTS/Flags  │    Size     │    Type     │
│  (8 bytes)  │  (4 bytes)  │  (隐含)     │
└─────────────┴─────────────┴─────────────┘

- PTS/Flags: 64 位，低 62 位是 PTS，高位是标志
  - Bit 63: 配置包标志
  - Bit 62: 关键帧标志
- Size: 32 位，负载数据长度
```

### 4.2 UDP 数据包头 (16 字节)

详见 `docs/PROTOCOL_SPEC.md`

---

## 5. 常见错误案例

### 5.1 颜色错乱（蓝色变紫色）

**症状**: 图像显示时蓝色变成紫色

**原因**: RGB 和 BGR 格式混淆

**排查**:
1. 检查帧数据来源是否已经是 RGB
2. 不要重复做 `[:, :, ::-1]` 转换

### 5.2 坐标偏移

**症状**: 点击位置与实际触发位置不一致

**原因**: 没有正确处理窗口缩放和居中偏移

**排查**:
1. 确保计算了缩放比例
2. 确保减去了居中偏移量

### 5.3 关键帧丢失导致花屏

**症状**: 恢复视频后画面花屏或黄色

**原因**: 解码器没有收到关键帧就开始解码 P 帧

**解决**: 调用 `reset_video()` 请求关键帧

**原理**: `reset_video()` 发送 `RESET_VIDEO` 控制消息，服务器会：
1. 调用 `MediaCodec.setParameters(PARAMETER_KEY_REQUEST_SYNC_FRAME)` 请求立即 I 帧
2. 触发捕获重置

这比等待下一个计划的 I 帧要快得多（I 帧间隔默认 10 秒）。

---

## 6. 检查清单

修改涉及以下内容时，必须检查本文档：

- [ ] 视频帧处理代码
- [ ] 图像显示代码
- [ ] 截图功能
- [ ] 预览窗口
- [ ] 触摸/点击事件
- [ ] 数据包解析

---

## 7. 相关文档

- `docs/PROTOCOL_SPEC.md` - 通信协议规范
- `docs/development/mcp_http_server/MCP_HTTP_SERVER_SPEC.md` - MCP 服务器开发规范
- `docs/development/mcp_http_server/GUI_WINDOW_ISSUES.md` - 预览窗口问题

---

**维护者**: 请在修改数据格式相关代码后更新此文档。
