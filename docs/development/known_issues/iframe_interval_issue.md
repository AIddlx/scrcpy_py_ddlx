# I-frame 间隔不稳定问题

## 问题描述

设置 `--i-frame-interval 2` 后，关键帧间隔仍然不均匀（3秒、7秒、2秒），动态画面恢复到清晰需要较长时间。

## 测试结果

### 测试 1：VBR + i-frame-interval=2
```
22:45:46,262 - KEY_FRAME #1
22:45:49,361 - KEY_FRAME #2  (间隔 3.1秒，设置值 2秒)
22:45:56,244 - KEY_FRAME #3  (间隔 6.9秒，设置值 2秒)
22:45:58,250 - KEY_FRAME #4  (间隔 2.0秒，设置值 2秒)
```

### 测试 2：CBR + i-frame-interval=1
```
23:04:33.002 - KEY_FRAME #1
23:04:36.027 - KEY_FRAME #2  (间隔 3秒，设置值 1秒)
23:04:40.926 - KEY_FRAME #3  (间隔 5秒，设置值 1秒)
```

**设置了 1 秒间隔，实际间隔却是 3 秒、5 秒！**

### CBR 模式观察
- ✅ 动态画面：更容易保持清晰（码率固定）
- ❌ 静止画面：如果起始模糊会保持模糊（无新关键帧刷新）

## 原因分析

### MediaCodec 限制

`KEY_I_FRAME_INTERVAL` 在 Android 硬件编码器上**不可靠**：

1. **颜色格式影响**：yuv420p 编码器忽略此参数，yuv420sp 才生效
2. **Surface 模式依赖**：需要通过 `createInputSurface()` + OpenGL 渲染才能有效控制
3. **厂商实现差异**：高通、联发科等厂商实现不同
4. **VBR 模式**：可变码率模式下编码器可能自主调整关键帧时机
5. **高通平台**：在本项目测试设备（vivo V2307A, Snapdragon 8 Gen 3, pineapple 代号）上也无效

### 参考资料

- 掘金文章：[Android多媒体框架之MediaCodec](https://juejin.cn/post/7294511567784558607)
  > "实际测试下来发现，必须通过 OpenGL 向 MediaCodec.createInputSurface 创建的 Surface 输入帧，才能达到控制关键帧数量的目的"

- CSDN文章：[Android原生编解码接口 MediaCodec 之——踩坑](https://devpress.csdn.net/v1/article/detail/117501158)
  > "发现当选择支持颜色格式为yuv420p的编码器时，KEY_I_FRAME_INTERVAL 设置无效"

## 可能的解决方案

### 方案 1：强制请求关键帧（推荐）

使用 `PARAMETER_KEY_REQUEST_SYNC_FRAME` 定时强制插入关键帧：

```java
// 服务端定期调用（如每2秒）
Bundle params = new Bundle();
params.putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0);
videoEncoder.setParameters(params);
```

**优点**：可靠，不依赖编码器对 KEY_I_FRAME_INTERVAL 的支持
**缺点**：需要修改服务端代码

### 方案 2：增加码率

给 P-frame 更多带宽，减少模糊：

```bash
python -X utf8 tests_gui/test_network_direct.py --bitrate 4000000
```

**优点**：无需代码修改
**缺点**：增加带宽消耗，不能根本解决问题

### 方案 3：客户端 PLI 请求

检测到质量下降时发送 PLI (Picture Loss Indication) 请求关键帧：

**优点**：按需请求，不浪费带宽
**缺点**：已有实现，但反应可能不够及时

### 方案 4：CBR 模式（部分有效）

CBR 在动态场景下画质更稳定，但无法解决关键帧问题。

## 相关文件

- `scrcpy/server/src/main/java/com/genymobile/scrcpy/video/SurfaceEncoder.java` - 编码器实现
- `scrcpy_py_ddlx/client/config.py` - 客户端配置
- `tests_gui/test_network_direct.py` - 测试脚本

## 后续工作

1. **实现定时强制关键帧机制**（最可靠）
   - 在 SurfaceEncoder 中添加定时器
   - 每隔 N 秒调用 `PARAMETER_KEY_REQUEST_SYNC_FRAME`
2. 优化 PLI 请求触发条件
3. 考虑客户端质量检测 + 自动请求关键帧

---

**发现日期**：2026-02-20
**测试设备**：vivo V2307A (Android 16, Snapdragon 8 Gen 3)
**状态**：待实现
