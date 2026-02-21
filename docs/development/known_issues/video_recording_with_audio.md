# 带音频的视频录制 - 已知问题（未解决）

## 状态: ❌ 失败

带音频的视频录制功能经过多次尝试仍未成功实现。此功能暂时隐藏。

---

## 问题描述

### 核心难点

scrcpy 发送的视频数据是 **Annex B 格式**（带起始码 0x00000001），而 MKV/MP4 容器需要 **长度前缀格式**（HVCC）。两者格式不兼容导致视频无法正常播放。

### 症状

1. **视频文件可以创建，但无法播放**
2. **VLC 播放出现马赛克、变色**
3. **PotPlayer 无法播放**
4. **FFmpeg 报错**: "No ref lists in the SPS", "PPS changed between slices"
5. **DTS 不单调递增错误**

---

## 尝试过的方案

### 方案 1: 直接设置 Annex B 格式为 extradata

```python
# 像原始 scrcpy 一样直接使用
self._video_stream.codec_context.extradata = config_data  # 85 bytes Annex B
```

**结果**: ❌ 失败
- PyAV 自动将 85 字节转换为 2449 字节（HVCC 格式）
- 但帧数据仍是 Annex B 格式
- 格式不匹配导致解码错误

---

### 方案 2: 配置包作为第一个视频帧写入

```python
# 配置包不设置 extradata，直接作为帧写入
av_packet = av.Packet(config_data)
av_packet.pts = None  # NOPTS
self._output.mux(av_packet)
```

**结果**: ❌ 失败
- DTS 不单调递增（配置包和帧的 DTS 冲突）
- FFmpeg 报 "non monotonically increasing dts" 错误

---

### 方案 3: 手动转换 Annex B 为长度前缀格式

```python
def _convert_annexb_to_length_prefixed(self, data: bytes) -> bytes:
    # 将 [start code][NAL] 转换为 [4-byte size][NAL]
    result = bytearray()
    for nal_unit in find_nal_units(data):
        result.extend(struct.pack('>I', len(nal_unit)))
        result.extend(nal_unit)
    return bytes(result)

# 配置包和帧都转换
converted_config = self._convert_annexb_to_length_prefixed(config_data)
self._video_stream.codec_context.extradata = converted_config

# 帧也转换
frame_data = self._convert_annexb_to_length_prefixed(frame.data)
```

**结果**: ❌ 失败
- 视频文件只有 5KB（几乎没有帧数据）
- 可能是转换过程破坏了数据

---

### 方案 4: 配置包缓存 + 附加到关键帧

```python
# 缓存配置，附加到每个关键帧
if packet.header.is_config:
    self._config_data = packet.data
    return None  # 不返回配置包

if packet.header.is_key_frame and self._config_data:
    merged_data = self._config_data + packet.data
    return VideoPacket(data=merged_data, ...)
```

**结果**: ❌ 失败
- 视频仍然无法播放
- 可能是 Annex B 格式问题

---

## 技术背景

### scrcpy 视频数据格式

- **编码**: H.265 (HEVC)
- **格式**: Annex B byte stream format
- **配置包**: VPS + SPS + PPS (带起始码)
- **帧数据**: NAL units (带起始码)

### MKV/MP4 容器要求

- **extradata**: HVCC 格式（长度前缀，无起始码）
- **帧数据**: 长度前缀格式

### 原始 scrcpy 的实现

```c
// scrcpy/app/src/recorder.c
static bool sc_recorder_set_extradata(AVStream *ostream, const AVPacket *packet) {
    // 直接复制 Annex B 数据到 extradata
    memcpy(extradata, packet->data, packet->size);
    ostream->codecpar->extradata = extradata;
    ostream->codecpar->extradata_size = packet->size;
}
```

原始 scrcpy 使用 FFmpeg C API，FFmpeg 会自动处理格式转换。但 PyAV 的行为可能不同。

---

## 可能的解决方案（未尝试）

1. **使用 ffmpeg subprocess**
   - 通过管道传输数据到 ffmpeg 进程
   - ffmpeg 自动处理所有格式转换
   - 最可靠但需要额外进程

2. **使用 MPEG-TS 容器**
   - TS 格式原生支持 Annex B
   - 不需要 extradata
   - 但音频需要是 AAC（OPUS 需要转码）

3. **使用 PyAV bitstream filter**（如果支持）
   - `hevc_mp4toannexb` 的逆操作
   - PyAV 目前不支持

4. **使用 raw H.265 流**
   - 输出 .h265 文件
   - 后期用 ffmpeg 添加音频

---

## 相关文件

- `scrcpy_py_ddlx/core/av_player.py` - Recorder 类
- `scrcpy_py_ddlx/core/demuxer/video.py` - StreamingVideoDemuxer
- `scrcpy_py_ddlx/core/packet_tee.py` - RecordingManager
- `scrcpy_py_ddlx/mcp_server.py` - record_video 方法

---

## 参考

- [H.265 Annex B vs HVCC](https://stackoverflow.com/questions/24884827/what-are-the-differences-between-h-264-annex-b-and-avcc/)
- [FFmpeg Bitstream Filters](https://ffmpeg.org/ffmpeg-bitstream-filters.html)
- [scrcpy recorder.c](https://github.com/Genymobile/scrcpy/blob/master/app/src/recorder.c)
