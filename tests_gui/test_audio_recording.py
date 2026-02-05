"""
测试音频录制功能

测试 PyAV 音频编码（WAV, Opus, MP3）
"""

import sys
import time
from pathlib import Path

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from scrcpy_py_ddlx import ScrcpyClient, ClientConfig


def test_audio_recording():
    """测试音频录制功能"""

    print("=" * 50)
    print("音频录制测试")
    print("=" * 50)

    # 配置
    config = ClientConfig(
        server_jar="scrcpy-server",
        audio=True,              # 启用音频流
        show_window=False,        # 不显示视频窗口
        lazy_decode=False,        # 禁用 lazy decode，保持音频流运行
    )

    # 录音时长（秒）
    duration = 5

    print(f"\n配置:")
    print(f"  音频流: 启用")
    print(f"  录音时长: {duration} 秒")
    print(f"  输出格式: WAV, Opus, MP3")

    # 连接设备
    print("\n正在连接设备...")
    client = ScrcpyClient(config)

    try:
        client.connect()
        print(f"✓ 设备已连接")

        # 创建输出目录
        output_dir = Path("test_audio_output")
        output_dir.mkdir(exist_ok=True)

        print(f"\n输出目录: {output_dir.absolute()}")

        # 测试 WAV 格式
        print("\n--- 测试 WAV 格式 ---")
        if client.start_audio_recording(str(output_dir / "test.wav"), max_duration=duration):
            print(f"录音中... ({duration} 秒)")
            time.sleep(duration)
            result = client.stop_audio_recording()
            if result:
                wav_size = (output_dir / "test.wav").stat().st_size / 1024
                print(f"✓ WAV 录音完成: {wav_size:.1f} KB")
            else:
                print(f"✗ WAV 录音失败")
        else:
            print(f"✗ 无法启动 WAV 录音")

        # 测试 Opus 格式（PyAV 编码）
        print("\n--- 测试 Opus 格式（PyAV 编码）---")
        if client.start_audio_recording(str(output_dir / "test.opus"), max_duration=duration):
            print(f"录音中... ({duration} 秒)")
            time.sleep(duration)
            result = client.stop_audio_recording()
            if result:
                opus_size = (output_dir / "test.opus").stat().st_size / 1024
                print(f"✓ Opus 录音完成: {opus_size:.1f} KB")
                if (output_dir / "test.wav").exists():
                    print(f"  压缩比: {opus_size / wav_size * 100:.1f}%")
            else:
                print(f"✗ Opus 录音失败")
        else:
            print(f"✗ 无法启动 Opus 录音")

        # 测试 MP3 格式（PyAV 编码）
        print("\n--- 测试 MP3 格式（PyAV 编码）---")
        if client.start_audio_recording(str(output_dir / "test.mp3"), max_duration=duration):
            print(f"录音中... ({duration} 秒)")
            time.sleep(duration)
            result = client.stop_audio_recording()
            if result:
                mp3_size = (output_dir / "test.mp3").stat().st_size / 1024
                print(f"✓ MP3 录音完成: {mp3_size:.1f} KB")
                if (output_dir / "test.wav").exists():
                    print(f"  压缩比: {mp3_size / wav_size * 100:.1f}%")
            else:
                print(f"✗ MP3 录音失败")
        else:
            print(f"✗ 无法启动 MP3 录音")

        # 总结
        print("\n" + "=" * 50)
        print("测试完成！")
        print("=" * 50)
        print(f"\n输出文件:")
        for f in output_dir.glob("*"):
            size = f.stat().st_size / 1024
            print(f"  {f.name} - {size:.1f} KB")
        print(f"\n可用播放器播放测试:")
        print(f"  - VLC Media Player")
        print(f"  - Windows Media Player")
        print(f"  - ffplay (如果有 FFmpeg)")

    except Exception as e:
        print(f"\n✗ 错误: {e}")
        import traceback
        traceback.print_exc()

    finally:
        print("\n断开连接...")
        client.disconnect()
        print("✓ 已断开")


if __name__ == "__main__":
    test_audio_recording()
