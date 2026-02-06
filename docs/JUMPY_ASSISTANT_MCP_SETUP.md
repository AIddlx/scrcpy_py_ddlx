# 阶跃桌面助手 - 添加 MCP 工具指南

本文档介绍如何在阶跃桌面助手中添加外部 MCP 工具（如 scrcpy-py-ddlx）。

## 前置要求

- 已安装阶跃桌面助手
- Python 3.10+
- Git

## 准备工作

### 1. 创建工作目录并克隆项目

```bash
# 创建工作目录
mkdir ddlx
cd ddlx

# 克隆项目（替换为实际的仓库地址）
git clone https://github.com/yourusername/scrcpy-py-ddlx.git
cd scrcpy-py-ddlx
```

### 2. 创建虚拟环境

```bash
# 在工作目录中创建虚拟环境
python -m venv venv

# 激活虚拟环境
venv\Scripts\activate
```

### 3. 安装依赖

```bash
# 安装项目依赖
pip install -r requirements.txt
```

**依赖说明：**
- `av` - 视频/音频编解码
- `numpy` - 数组操作
- `PySide6` - Qt6 GUI
- `PyOpenGL` - GPU 加速渲染
- `sounddevice` - 音频播放
- `starlette` / `uvicorn` - HTTP MCP 服务器

### 4. 启动 MCP 服务器

```bash
# 确保虚拟环境已激活（命令行前缀显示 (venv)）
# 启动 MCP 服务器（启用音频）
python scrcpy_http_mcp_server.py --audio
```

服务器默认运行在 `http://localhost:3359/mcp`

**保持此窗口运行**，不要关闭。

## 添加步骤

### 步骤 1: 显示主窗口

在电脑右下角找到"小跃"图标，**右键点击** → 选择"显示主窗口"

![步骤1](../image/1.png)

### 步骤 2: 进入设置页面

在主窗口**右上角**，**左键点击**圆形图标 → 进入设置页面

![步骤2](../image/2.png)

### 步骤 3: 找到工具箱

在设置页面中，找到并点击"**工具箱**"选项

![步骤3](../image/3.png)

### 步骤 4: 添加外部工具

在工具箱页面，点击"**添加外部工具**"

![步骤4](../image/4.png)

### 步骤 5: 配置 MCP 工具

选择"**添加外部 MCP 工具**"，填写以下信息：



填写完成后，点击"**确认**"或"**添加**"按钮完成 MCP 服务器添加。

![步骤5](../image/5.png)

### MCP 配置说明

**配置参数：**

| 参数 | 值 | 说明 |
|------|---|------|
| 名称 | `scrcpy` | 自定义名称，用于识别工具 |
| URL | `http://127.0.0.1:3359/mcp` | MCP 服务器地址 |

**注意：**
- URL 使用 `127.0.0.1` 而非 `localhost`，确保兼容性
- 端口号 `3359` 是 scrcpy HTTP MCP 服务器的默认端口
- 确保启动服务器时使用了 `--audio` 参数以支持录音功能

**配置文件示例（供参考）：**

```json
{
  "mcpServers": {
    "scrcpy": {
      "url": "http://127.0.0.1:3359/mcp"
    }
  }
}
```

---

## 验证连接

添加完成后，在阶跃桌面助手中尝试使用 scrcpy 功能：

```
// 连接设备
连接到 Android 设备 192.168.5.3:5555

// 截图
截取屏幕截图

// 录音
录制 10 秒音频
```

## 常见问题

### 1. 连接失败

- 确保 scrcpy_http_mcp_server.py 已启动
- 检查端口号 3359 是否被占用
- 确认 URL 格式正确：`http://localhost:3359/mcp`

### 2. 设备未发现

- 确保 Android 设备已启用 USB 调试
- 检查设备是否在同一网络
- 尝试使用 USB 线连接一次

### 3. 音频功能不可用

- 启动服务器时使用 `--audio` 参数：
  ```bash
  python scrcpy_http_mcp_server.py --audio
  ```

## 启动 MCP 服务器

```bash
# 进入项目目录
cd scrcpy-py-ddlx

# 启动 MCP 服务器（启用音频）
python scrcpy_http_mcp_server.py --audio
```

服务器默认运行在 `http://localhost:3359/mcp`

## 可用功能

| 功能 | 说明 |
|------|------|
| 连接设备 | 连接到 Android 设备 |
| 截图 | 截取屏幕截图 |
| 点击/滑动 | 控制设备触控 |
| 录音 | 录制设备音频（异步） |
| 应用列表 | 列出已安装应用 |
| 剪贴板 | 同步剪贴板内容 |

## 使用示例

添加 MCP 工具后，你可以在阶跃桌面助手中直接控制 Android 设备。

**示例：打开哔哩哔哩应用**

![使用示例](../image/6.png)

**执行流程：**
1. AI 连接到手机设备
2. 获取已安装应用列表
3. 找到哔哩哔哩（包名：tv.danmaku.bili）
4. 打开应用

你也可以尝试其他任务：
- "截取屏幕截图"
- "打开设置"
- "录制 10 秒音频"
- "点击屏幕中央"
- "滑动查看更多"
