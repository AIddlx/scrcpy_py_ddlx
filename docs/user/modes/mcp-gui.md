# MCP GUI 模式

使用图形界面配置和运行 MCP 服务器。

---

## 什么是 MCP GUI？

MCP GUI 提供可视化界面来：
- 配置连接参数
- 实时查看视频画面
- 控制 MCP 服务器启动
- 测试 MCP 工具功能

---

## 启动

```bash
python scrcpy_mcp_gui.py
```

---

## 功能

### 1. 连接配置

- 设备选择（USB/无线）
- 视频质量设置
- 音频流开关

### 2. 内嵌视频显示

Android 设备的实时视频预览。

### 3. MCP 服务器控制

- 启动/停止 MCP 服务器（stdio 模式）
- 日志输出显示
- 工具测试界面

### 4. 设备状态

- 连接状态
- 设备信息（名称、分辨率）
- 帧率统计

---

## 与 Claude Code 配合使用

1. **启动 GUI**

   ```bash
   python scrcpy_mcp_gui.py
   ```

2. **配置连接**

   - 选择设备或自动检测
   - 如需调整设置
   - 点击"连接"

3. **启动 MCP 服务器**

   - 点击"启动 MCP 服务器"
   - GUI 切换到 MCP 模式

4. **配置 Claude Code**

   在 Claude Code 设置中：

   ```json
   {
     "mcpServers": {
       "scrcpy": {
         "command": "python",
         "args": ["/path/to/scrcpy_mcp_gui.py", "--mcp-only"]
       }
     }
   }
   ```

---

## 系统要求

- PySide6（GUI 依赖）
- scrcpy-server（已编译）

```bash
pip install PySide6
```

---

## 故障排除

### GUI 不显示

确保已安装 PySide6：

```bash
pip install PySide6
```

### MCP 服务器无法启动

查看 GUI 中的日志输出获取错误信息。

---

## 相关文档

- [HTTP MCP 模式](mcp-http.md) - HTTP 方式 MCP 服务器
- [Python API 模式](python-api.md) - 直接 Python 使用
