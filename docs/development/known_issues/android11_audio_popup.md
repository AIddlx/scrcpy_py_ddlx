# Android 11 音频录制弹窗

## 概述

在 Android 11 设备上启用音频录制功能时，连接过程中会出现一个短暂的弹窗（HeapDumpActivity）。

## 影响版本

- Android 11 (API 30) 仅限

## 优先级

低（正常行为，非 Bug）

## 状态

✅ 已知限制 / 无需修复

---

## 问题描述

### 现象

在网络模式连接手机时，如果启用了音频功能 (`audio=true`)，手机屏幕上会出现一个一闪而过的弹窗。

通过 ADB 日志可以观察到：

```
com.android.shell/com.android.shell.HeapDumpActivity
```

### 复现步骤

1. 使用 Android 11 设备
2. 推送服务端时启用音频：`push_server_persistent(video=true, audio=true)`
3. 连接设备
4. 观察到弹窗一闪而过

### 根本原因

**Android 安全限制**：

Android 11 要求录制音频的应用必须处于"前台"状态，这是为了防止恶意应用在后台偷偷录音。

**scrcpy 的特殊情况**：

```
scrcpy 服务端 ≠ 普通 App
    ↓
通过 app_process 从 shell 启动
    ↓
用户 ID = 2000 (shell 用户)
    ↓
不在"前台" → 无法录音
```

**Workaround 实现**：

scrcpy 服务端会短暂启动 `com.android.shell.HeapDumpActivity`：

```java
// AudioDirectCapture.java
private static void startWorkaroundAndroid11() {
    Intent intent = new Intent(Intent.ACTION_MAIN);
    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
    intent.addCategory(Intent.CATEGORY_LAUNCHER);
    intent.setComponent(new ComponentName(FakeContext.PACKAGE_NAME,
        "com.android.shell.HeapDumpActivity"));
    ServiceManager.getActivityManager().startActivity(intent);
}
```

流程：
1. 启动 HeapDumpActivity → shell 用户"进入前台"
2. 开始录音
3. 立即关闭 Activity

### 相关代码

**文件**: `scrcpy/server/src/main/java/com/genymobile/scrcpy/audio/AudioDirectCapture.java`

```java
@Override
public void start() throws AudioCaptureException {
    if (Build.VERSION.SDK_INT == AndroidVersions.API_30_ANDROID_11) {
        startWorkaroundAndroid11();  // 只有 Android 11 执行
        try {
            tryStartRecording(5, 100);
        } finally {
            stopWorkaroundAndroid11();
        }
    } else {
        startRecording();  // Android 12+ 直接录音
    }
}
```

---

## 影响分析

### 用户影响

- 轻微视觉干扰（弹窗持续约 100-200ms）
- 不影响功能正常使用

### 功能影响

- 仅影响 Android 11 设备
- 仅在启用音频时出现
- 不影响视频捕获（使用不同的权限通道）

---

## 为什么播放音视频没有这个问题？

| 操作 | 权限要求 | Android 限制 |
|-----|---------|-------------|
| **播放音频** | 无 | 任何应用都可以播放 |
| **录制音频** | `RECORD_AUDIO` | Android 11 要求必须在"前台" |
| **捕获视频** | `MediaProjection` | 需要用户授权一次，无需前台状态 |

视频捕获使用 `MediaProjection` API，用户授权后就不需要"前台"状态，所以没有这个问题。

---

## 版本差异

| Android 版本 | 是否有弹窗 | 说明 |
|-------------|-----------|------|
| Android 10 及以下 | 不支持音频 | 录音功能不可用 |
| **Android 11** | ✅ 有弹窗 | 需要 HeapDumpActivity workaround |
| Android 12+ | ❌ 无弹窗 | Shell 进程可直接录音 |

Android 12 放宽了这个限制，shell 进程可以直接录制音频。

---

## 规避方案

如果不想看到这个弹窗：

1. **不启用音频**（推荐）
   ```python
   # 默认就是 audio=false
   push_server_persistent(video=true)  # audio 默认为 false
   ```

2. **升级到 Android 12+**
   - Android 12 及以上版本没有此限制

3. **仅在需要录音时启用音频**
   - 一般控制操作不需要音频
   - 只有录音需求时才设置 `audio=true`

---

## 相关文件

- `scrcpy/server/src/main/java/com/genymobile/scrcpy/audio/AudioDirectCapture.java` - Workaround 实现

## 相关文档

- [ADB 隧道模式](../../ADB_TUNNEL_MODE.md)
- [音频卡顿修复](../AUDIO_CHOPPY_FIX.md)

## 历史

- 2026-02-18: 问题识别并记录
