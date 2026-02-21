# MCP 截图参数指南

## 概述

本文档说明 MCP 服务器的截图功能参数，包括格式、质量和推荐配置。

## 截图参数

### format（格式）

| 值 | 说明 | 特点 |
|---|------|------|
| `jpg` | JPEG 格式（默认） | 文件小，有损压缩，适合大多数场景 |
| `png` | PNG 格式 | 文件大，无损压缩，适合需要精确像素的场景 |

### quality（质量）

仅对 JPEG 格式有效，范围 1-100。

| 值 | 文件大小 | 画质 | 适用场景 |
|---|---------|------|---------|
| 95 | ~400-600 KB | 高质量 | 需要高画质的场景 |
| **80** | ~200-300 KB | 良好 | **默认推荐**，平衡画质和大小 |
| 60 | ~100-150 KB | 可接受 | 存储空间有限 |
| **40** | ~50-80 KB | 基本可用 | **最低推荐**，省流量/存储 |

## 默认配置

| 参数 | 默认值 |
|-----|--------|
| 码率 (bitrate) | 4 Mbps |
| 帧率 (max_fps) | 60 |
| 截图格式 | jpg |
| 截图质量 | 80% |

## 使用示例

### 默认截图

```bash
curl -X POST http://localhost:3359/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"screenshot"}}'
```

### 指定格式和质量

```bash
# JPEG 质量 60%
curl -X POST http://localhost:3359/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"screenshot","arguments":{"format":"jpg","quality":60}}}'

# PNG 格式（无损）
curl -X POST http://localhost:3359/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"screenshot","arguments":{"format":"png"}}}'
```

### 最低推荐配置（省流量）

```bash
# 连接时使用低码率
curl -X POST http://localhost:3359/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"connect","arguments":{"bitrate":1000000,"max_fps":30}}}'

# 截图时使用低质量
curl -X POST http://localhost:3359/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"screenshot","arguments":{"quality":40}}}'
```

## 文件大小参考

基于 1080x2400 分辨率设备：

| 配置 | 预期文件大小 |
|-----|-------------|
| PNG | ~500-800 KB |
| JPEG 95% | ~400-600 KB |
| JPEG 80%（默认） | ~200-300 KB |
| JPEG 60% | ~100-150 KB |
| JPEG 40% | ~50-80 KB |

> 实际大小取决于画面内容复杂度。复杂画面压缩后更大。

## 推荐场景

| 场景 | 码率 | 帧率 | 截图质量 |
|-----|------|------|---------|
| 日常使用 | 4 Mbps | 60 | 80% |
| 高画质需求 | 8 Mbps | 60 | 95% |
| 低带宽/省流量 | 1 Mbps | 30 | 40% |
| 游戏/快速画面 | 8 Mbps | 90-120 | 80% |

## 注意事项

1. **Windows CMD 引号问题**: 使用双引号并转义内部双引号
   ```cmd
   # 正确
   -d "{\"jsonrpc\":\"2.0\",...}"

   # 错误（Windows CMD 不支持单引号）
   -d '{"jsonrpc":"2.0",...}'
   ```

2. **截图保存位置**: `screenshots/` 目录下，文件名格式 `screenshot_YYYYMMDD_HHMMSS_mmm.jpg`

3. **质量参数范围**: 1-100，超出范围会被限制

## 相关命令

| 功能 | 命令 |
|-----|------|
| 连接设备 | `connect` |
| 截图 | `screenshot` |
| 断开连接 | `disconnect` |
| 获取状态 | `get_state` |
| 启动预览 | `start_preview` |
| 停止预览 | `stop_preview` |
