# 2026-03-03 修改记录

## 1. 默认视频码率调整

### 变更
默认视频码率从 8Mbps 调整为 3Mbps，平衡画质和带宽消耗。

### 修改文件
| 文件 | 修改 |
|------|------|
| `scrcpy_http_mcp_server.py` | 8 处默认值 8000000 → 3000000 |
| `scrcpy_mcp_gui.py` | 3 处默认值 8000000 → 3000000 |
| `tests_gui/test_direct.py` | 默认值 2500000 → 3000000 |
| `tests_gui/test_network_direct.py` | 帮助文本更新 |
| `tests_gui/test_seamless_wireless.py` | 默认值 8000000 → 3000000 |
| `scrcpy_py_ddlx/core/server_params.py` | 2 处函数默认参数 |
| `scrcpy/server/.../Options.java` | Java 服务端默认值 |

---

## 2. 日志级别控制优化

### 变更
- 环境变量控制: `SCRCPY_DEBUG=1` 强制 DEBUG, `SCRCPY_LOG_LEVEL` 指定级别
- 命令行参数: `--log-level`, `--log-keep`
- 默认级别: WARNING（普通用户最少日志）

### 修改文件
| 文件 | 修改 |
|------|------|
| `scrcpy_py_ddlx/core/logging_config.py` | 新增级别控制函数 |
| `scrcpy_http_mcp_server.py` | 新增命令行参数 |
| `tests_gui/test_direct.py` | 使用统一日志配置 |
| `tests_gui/test_network_direct.py` | 使用统一日志配置 |

### 相关文档
- [LOG_STATISTICS.md](LOG_STATISTICS.md) - 日志统计报告

---

## 3. Companion APK INTERNET 权限修复

### 问题
Companion 应用无法感知服务端状态，UDP discovery 失败: `SocketException: socket failed: EPERM (Operation not permitted)`

### 原因
缺少 `android.permission.INTERNET` 权限，Android 应用使用 UDP socket 必须声明此权限。

### 修复
| 文件 | 修改 |
|------|------|
| `scrcpy/companion/app/src/main/AndroidManifest.xml` | 添加 `<uses-permission android:name="android.permission.INTERNET" />` |
| `scrcpy/companion/build.sh` | 改进签名错误提示，Windows 上使用 `.bat` 扩展名 |
| `scrcpy/companion/build.cmd` | 添加 `--min-sdk-version 21 --target-sdk-version 34`，修复"旧版Android"警告 |

### 验证
```
ScrcpyUdp: Sent discover request to 127.0.0.1:27183
ScrcpyUdp: Discovery response: SCRCPY_HERE RMX1931 192.168.5.4 single
```

---

## 4. 用户目录统一

### 问题
项目使用多个不一致的用户目录存储数据：
- `~/.scrcpy-py-ddlx/` - GUI 配置（非标准）
- `~/.config/scrcpy-py-ddlx/` - 认证密钥（XDG 标准）
- `~/.cache/scrcpy-py-ddlx/` - 缓存
- 项目目录 `.auth/` - `deploy_server.sh` 错误创建

### 修复
| 文件 | 修改 |
|------|------|
| `scrcpy/release/deploy_server.sh` | 密钥路径从 `$PROJECT_ROOT/.auth/` 改为 `~/.config/scrcpy-py-ddlx/auth_keys/` |
| `scrcpy_py_ddlx/gui/config_manager.py` | 配置目录从 `~/.scrcpy-py-ddlx/configs/` 改为 `~/.config/scrcpy-py-ddlx/configs/` |
| `.gitignore` | 添加 `.auth/` 防止意外创建 |

### 统一后的目录结构
```
~/.config/scrcpy-py-ddlx/
├── auth_keys/           # 认证密钥 (按设备 serial 或 IP 命名)
│   ├── scrcpy-auth.key  # 主密钥 (deploy_server.sh)
│   ├── <serial>.key     # 设备密钥 (test_network_direct.py)
│   └── <ip>.key         # IP 别名
└── configs/             # GUI 配置
    └── Default Device.json

~/.cache/scrcpy-py-ddlx/
├── capability_cache.json   # 设备能力缓存
└── config.json             # 日志配置 (待迁移)

~/Documents/scrcpy-py-ddlx/  # 用户文件
├── screenshots/
└── recordings/
```

### 待办
- [ ] `~/.cache/scrcpy-py-ddlx/config.json` 迁移到 `~/.config/scrcpy-py-ddlx/`
- [ ] `~/.cache/scrcpy-py-ddlx/capability_cache.json` 考虑是否迁移

---

## 5. 清理测试文件

删除不再使用的测试文件：
- `tests/test_*.py` - 旧的多进程解码器测试
- `tests_gui/test_qopengl*.py` - 已整合到主代码
- `tests_gui/profile_*.py` - 临时性能测试
- `tests_gui/test_udp_*.py` - 已整合到 test_network_direct.py
