# 屏幕旋转和 CONFIG 包合并问题修复

## 问题现象

1. **画面变色、破损、马赛克**
2. **横竖屏切换导致崩溃/卡死**
3. **服务端停止发送数据后客户端超时**

## 问题分析

### 根本原因

1. **CONFIG 数据与非关键帧合并**
   - 原始逻辑：CONFIG 数据被与第一个非 CONFIG 包合并
   - 问题：这个包可能不是关键帧，导致关键帧没有 CONFIG 数据
   - 结果：解码失败，画面损坏

2. **skipFrames 优化导致 EOS 被跳过**
   - skipFrames 优化在 peek 更新帧时，可能错误地跳过 EOS 标志
   - 导致 encode() 循环无法正常退出，服务端卡住

## 最终解决方案

### 修复 1: 只与关键帧合并 CONFIG 数据

**文件**: `scrcpy_py_ddlx/core/demuxer/udp_video.py`

```python
def _merge_config(self, packet: VideoPacket) -> Optional[VideoPacket]:
    if packet.header.is_config:
        self._config_data = packet.data
        return packet

    # 只与关键帧合并 CONFIG
    if packet.header.is_key_frame and self._config_data is not None:
        # 检查关键帧是否已包含 SPS/PPS
        if not has_sps_pps:
            merged_data = self._config_data + packet.data
        self._config_data = None
        return VideoPacket(header=packet.header, data=merged_data, ...)

    return packet
```

### 修复 2: 移除 skipFrames 优化

**文件**: `scrcpy/server/src/main/java/com/genymobile/scrcpy/video/SurfaceEncoder.java`

移除了整个 skipFrames 优化代码块，恢复简单的编码循环。

### 修复 3: 添加超时和停滞检测

**文件**: `scrcpy/server/src/main/java/com/genymobile/scrcpy/video/SurfaceEncoder.java`

```java
final long DEQUEUE_TIMEOUT_US = 100000; // 100ms timeout
int consecutiveTimeouts = 0;

int outputBufferId = codec.dequeueOutputBuffer(bufferInfo, DEQUEUE_TIMEOUT_US);
if (outputBufferId < 0) {
    consecutiveTimeouts++;
    if (consecutiveTimeouts >= 100) {  // 10 seconds
        Ln.w("Encoder stall detected: no output for 10 seconds");
    }
    continue;
}
```

## 测试结果

### 测试 3: 2026-02-20 20:50 ✅ 成功

**参数**: `--bitrate 4000000 --max-fps 60`

**服务端日志**:
```
Capture restarted: new size=2800x1264 (restart #1)
Capture restarted: new size=1264x2800 (restart #2)
Capture restarted: new size=2800x1264 (restart #3)
Capture restarted: new size=1264x2800 (restart #4)
```

**客户端日志**:
```
[CONFIG_MERGE] Key frame analysis: size=67283, first_nal_type=5, has_sps_pps=False
[CONFIG_MERGE] Merged 31 bytes config with key frame 67283 bytes -> 67314 bytes
```

**验证**:
- ✅ 多次屏幕旋转正常工作
- ✅ 无花屏、马赛克
- ✅ 无编码器停滞
- ✅ CONFIG 数据正确合并

## 修改的文件

| 文件 | 修改 |
|------|------|
| `SurfaceEncoder.java` | 移除 skipFrames 优化，添加超时和停滞检测 |
| `udp_video.py` | 只与关键帧合并 CONFIG 数据，添加 SPS/PPS 检测 |

## 修复历史

1. **修复 1**: 只将 CONFIG 与关键帧合并
2. **修复 2**: 检查关键帧是否已包含 SPS/PPS
3. **修复 3**: 服务端添加超时和日志
4. **修复 4**: 添加编码器停滞检测
5. **修复 5**: 尝试修复 skipFrames 的 EOS 问题（未成功）
6. **修复 6**: 移除 skipFrames 优化（最终解决方案）

## 相关文件

- `scrcpy_py_ddlx/core/demuxer/udp_video.py` - CONFIG 包合并逻辑
- `scrcpy_py_ddlx/core/decoder/video.py` - 视频解码器
- `scrcpy/server/src/main/java/com/genymobile/scrcpy/video/SurfaceEncoder.java` - 服务端编码器
