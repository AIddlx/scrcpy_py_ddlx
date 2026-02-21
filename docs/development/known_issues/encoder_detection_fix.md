# 编码器检测修复

## 问题描述

`--list-encoders` 命令错误地报告设备不支持 H265 编码：

```
[INFO] Device video encoder support:
  ✓ H264
  ✗ H265   # 错误！设备实际支持
  ✗ AV1
```

## 原因分析

### 原始实现
```python
# 只检查高通平台
result = subprocess.run(['adb', 'shell', 'getprop ro.board.platform'], ...)
platform = result.stdout.strip().lower()

if 'qcom' in platform or 'sm' in platform or 'msm' in platform:
    encoders['h265'] = True
```

### 问题
1. **平台检测不完整**：只识别高通平台（qcom/sm/msm）
2. **忽略其他厂商**：联发科、三星、华为等平台不被识别
3. **忽略高通代号**：vivo V2307A 使用高通 Snapdragon 8 Gen 3，代号 "pineapple"，不匹配旧规则
4. **编码器命名差异**：H264 编码器可能命名为 `avc` 而非 `h264`

## 修复方案

### 1. 直接查询 MediaCodec
```python
# 通过 dumpsys media.codec 直接查询编码器
# 注意：H264 编码器可能命名为 avc (Advanced Video Coding)
#       H265 编码器可能命名为 hevc
result = subprocess.run(
    ['adb', 'shell',
     'dumpsys media.codec | grep -A 100 "Video encoders:" | grep -E "OMX\\..*\\.(h264|avc|h265|hevc|av1)"'],
    capture_output=True, text=True, timeout=10,
    errors='ignore'  # 忽略非 UTF-8 字符
)
output = result.stdout.lower()

if 'h265' in output or 'hevc' in output:
    encoders['h265'] = True
```

### 2. 编码器命名对照表

| 编码格式 | 常见命名 | 说明 |
|----------|----------|------|
| H.264/AVC | h264, avc | AVC = Advanced Video Coding |
| H.265/HEVC | h265, hevc | HEVC = High Efficiency Video Coding |
| AV1 | av1 | AOMedia Video 1 |

### 3. 扩展平台回退检测
```python
# 联发科平台 (mt, apollo, cebus, k6833)
if 'mt' in platform or platform in ['apollo', 'cebus', 'k6833']:
    encoders['h265'] = True

# 高通代号 (pineapple = Snapdragon 8 Gen 3)
if platform in ['pineapple', 'lanai']:
    encoders['h265'] = True

# 三星 Exynos
if 'exynos' in platform or 'universal' in platform:
    encoders['h265'] = True

# 华为 HiSilicon
if 'kirin' in platform or 'hi' in platform:
    encoders['h265'] = True
```

## 修复后结果

```
[INFO] Device video encoder support:
  ✓ H264
  ✓ H265   # 正确！
  ✗ AV1
```

## 经验总结

### 教训
1. **不要假设平台**：Android 设备厂商众多，平台各异
2. **优先使用系统 API**：`dumpsys media.codec` 比猜测平台更可靠
3. **提供回退机制**：当主要方法失败时有备用方案

### Android 平台参考

| 厂商 | 平台标识示例 |
|------|-------------|
| 高通 | qcom, sm8550, msm8998 |
| 联发科 | mt6893, apollo, cebus, k6833 |
| 高通 (代号) | pineapple (Snapdragon 8 Gen 3), lanai |
| 三星 | exynos2100, universal2100 |
| 华为 | kirin9000, hi3660 |
| 紫光展锐 | ud710, tiger |

### 相关命令

```bash
# 查询平台
adb shell getprop ro.board.platform

# 查询编码器
adb shell dumpsys media.codec | grep -A 100 "Video encoders:"

# 查询所有 MediaCodec 组件
adb shell dumpsys media.codec
```

## 相关文件

- `tests_gui/test_network_direct.py` - `query_device_encoders()` 函数

---

**修复日期**：2026-02-20
**测试设备**：vivo V2307A (Android 16, Snapdragon 8 Gen 3, 代号 pineapple)
