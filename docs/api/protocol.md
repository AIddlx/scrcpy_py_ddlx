# scrcpy Server 参数格式参考

## 问题分析

### 遇到的错误
```
NumberFormatException: For input string: "1251153844"
```

### 根本原因
scrcpy server (Java) 将 `scid` 参数解析为**十六进制**数字（base-16），但 Python 客户端传递的是**十进制**字符串。

**来自 scrcpy 源码的证据** (`server/src/main/java/com/genymobile/scrcpy/Options.java:315`):
```java
case "scid":
    int scid = Integer.parseInt(value, 0x10);  // 0x10 = radix 16 (十六进制!)
```

## 正确的参数格式

### scid 参数
- **格式**: 8 位十六进制，小写，零填充
- **示例**: `scid=12345678`
- **Python 格式化字符串**: `f"scid={scid_value:08x}"`

#### 示例：
| 十进制值 | 错误格式 | 正确格式 |
|---------|---------|---------|
| 305419896    | `scid=305419896` ❌ | `scid=12345678` ✓ |
| 1251153844   | `scid=1251153844` ❌ | `scid=4a8c4aa4` ✓ |
| 1            | `scid=1` ❌ | `scid=00000001` ✓ |

### log_level 参数
- **格式**: 小写文本
- **有效值**: `verbose`, `debug`, `info`, `warn`, `error`
- **示例**: `log_level=info`

### 布尔参数
- **格式**: 小写文本 `true` 或 `false`
- **不是** Python 的 `True`/`False`
- **示例**:
  - `video=true`
  - `audio=false`
  - `control=true`

### 数值参数
- **格式**: 十进制字符串
- **示例**:
  - `max_size=1920`
  - `video_bit_rate=8000000`
  - `max_fps=60`

## 完整参数示例

来自官方 scrcpy 源码 (`app/src/server.c:265`):
```c
ADD_PARAM("scid=%08x", params->scid);  // 8 位十六进制
ADD_PARAM("log_level=%s", log_level_to_server_string(params->log_level));
```

### 最小可用参数
```python
server_params = [
    f"scid={scid:08x}",      # 必需: 8 位十六进制 (例如 "scid=12345678")
    "log_level=info",        # 推荐: info, debug, verbose
    "video=true",            # 可选: 启用视频流
    "audio=false",           # 可选: 禁用音频
    "control=true",          # 可选: 启用控制通道
]
```

### 完整参数示例
```python
server_params = [
    f"scid={scid:08x}",              # 会话 ID (十六进制)
    "log_level=info",                # 日志级别
    "video=true",                    # 启用视频
    "audio=false",                   # 禁用音频
    "control=true",                  # 启用控制
    "video_codec=h264",              # 视频编解码器: h264, h265
    "video_bit_rate=8000000",        # 8 Mbps
    "max_size=1920",                 # 最大分辨率宽度
    "max_fps=60",                    # 最大帧率
    "tunnel_forward=false",          # 使用 adb reverse (而非 forward)
]
```

## Python 实现

### 正确的格式化字符串
```python
import random

# 生成随机 SCID (31 位非负整数)
scid = random.randint(0, 0x7FFFFFFF)

# 正确: 格式化为 8 位十六进制小写
scid_param = f"scid={scid:08x}"  # 例如 "scid=4a8c4aa4"

# 错误: 十进制格式
# scid_param = f"scid={scid}"  # 这会导致 NumberFormatException!
```

### 验证代码
```python
def validate_scid(scid_value: str) -> bool:
    """验证 scid 参数格式"""
    try:
        # 必须是 8 个十六进制数字
        if len(scid_value) != 8:
            return False
        # 必须能解析为十六进制
        int(scid_value, 16)
        return True
    except ValueError:
        return False

# 使用
scid_hex = f"{scid:08x}"
if not validate_scid(scid_hex):
    raise ValueError(f"Invalid scid format: {scid_hex}")
```

## 验证方法

### 1. 检查 Server 日志
启动 server 后，检查是否成功初始化：
```bash
adb shell ps | grep app_process  # 应该显示 server 正在运行
```

### 2. 验证命令构造
```python
def build_server_command(serial, classpath, params, version="3.3.4"):
    """构建并验证 server 命令"""
    cmd = [
        "adb",
        "-s", serial,
        "shell",
        f"CLASSPATH={classpath}",
        "app_process",
        "/",
        "com.genymobile.scrcpy.Server",
        version
    ]
    cmd.extend(params)

    # 记录用于调试
    print("完整命令:")
    print(" ".join(cmd[:9]) + " ...")  # 显示前 9 个参数

    # 验证 scid
    for param in params:
        if param.startswith("scid="):
            scid_val = param.split("=")[1]
            print(f"SCID 参数: {param}")
            print(f"  十进制: {int(scid_val, 16)}")
            print(f"  十六进制: 0x{scid_val}")

    return cmd
```

### 3. 使用最小参数测试
```python
# 最小测试
test_params = [
    f"scid={0x12345678:08x}",  # 测试用固定值
    "log_level=debug",
]

# 应该能够启动而不出现 NumberFormatException
process = adb.start_server_process(
    serial=device.serial,
    server_classpath="/data/local/tmp/scrcpy-server",
    params=test_params
)
```

## 常见错误

### ❌ 错误
```python
# 十进制格式 - 导致 NumberFormatException
params = [f"scid={scid}"]

# 缺少前导零
params = [f"scid={scid:x}"]  # 可能产生少于 8 位

# 大写十六进制（技术上可行但不是官方格式）
params = [f"scid={scid:08X}"]  # 大写
```

### ✓ 正确
```python
# 正确的 8 位小写十六进制，零填充
params = [f"scid={scid:08x}"]
```

## 参考资料

- **scrcpy Server 源码**: `server/src/main/java/com/genymobile/scrcpy/Options.java:315`
- **scrcpy Client 源码**: `app/src/server.c:265`
- **官方文档**: `doc/develop.md:138`

## 版本兼容性

此格式适用于 scrcpy 版本 **3.3.4** 及使用相同参数解析机制的后续版本。

始终确保 version 参数与 server jar 版本匹配：
```python
server_process = adb.start_server_process(
    serial=device.serial,
    server_classpath="/data/local/tmp/scrcpy-server",
    params=params,
    version="3.3.4"  # 必须与 server jar 版本匹配!
)
```
