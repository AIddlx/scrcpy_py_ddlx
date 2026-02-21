#!/usr/bin/env python3
"""
Test capability cache functionality.

This script tests:
1. First run: queries device and PC capabilities, caches them
2. Subsequent runs: loads from cache
3. Per-device caching (different devices have separate caches)
4. Optimal config selection

Usage:
    python test_capability_cache.py
    python test_capability_cache.py --refresh  # Force refresh current device
    python test_capability_cache.py --list     # List all cached devices
    python test_capability_cache.py --clear    # Clear all cache
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_capability_cache(force_refresh: bool = False):
    """Test capability cache."""
    from scrcpy_py_ddlx.client.capability_cache import (
        CapabilityCache,
        get_connected_device_serial
    )

    cache = CapabilityCache.get_instance()

    print("\n" + "=" * 60)
    print("Capability Cache Test")
    print("=" * 60)

    # Show cache info
    info = cache.get_cache_info()
    print(f"\nCache file: {info['cache_file']}")
    print(f"Cache exists: {info['cache_exists']}")

    # Show cached devices
    if info.get('cached_devices'):
        print(f"\nCached devices ({len(info['cached_devices'])}):")
        for dev in info['cached_devices']:
            print(f"  - {dev['model']} (Android {dev['android_version']})")
            print(f"    Serial: {dev['serial']}, Age: {dev['age_days']} days")

    # Get current device
    current_serial = get_connected_device_serial()
    if current_serial:
        print(f"\nCurrent device: {current_serial}")
    else:
        print("\nNo device connected!")

    # Get PC capability
    print("\n" + "-" * 40)
    print("PC Capability:")
    print("-" * 40)

    pc = cache.get_pc_capability(force_refresh=force_refresh)
    print(f"  OS: {pc.os}")
    print(f"  NVIDIA CUDA: {pc.nvidia_cuda}")
    print(f"  NVIDIA NVENC: {pc.nvidia_nvenc}")
    print(f"  NVIDIA NVDEC: {pc.nvidia_nvdec}")
    print(f"  Intel QSV: {pc.intel_qsv}")
    print(f"  AMD AMF: {pc.amd_amf}")

    print("\n  Hardware Decoders:")
    for codec in ['h264', 'h265', 'av1']:
        hw = pc.decoders.get(codec, [])
        hw_str = ', '.join(hw) if hw else 'None'
        print(f"    {codec.upper()}: [{hw_str}]")

    print("\n  Hardware Encoders:")
    for codec in ['h264', 'h265', 'av1']:
        hw = pc.encoders.get(codec, [])
        hw_str = ', '.join(hw) if hw else 'None'
        print(f"    {codec.upper()}: [{hw_str}]")

    # Get device capability (requires ADB)
    print("\n" + "-" * 40)
    print("Current Device Capability:")
    print("-" * 40)

    try:
        device = cache.get_device_capability(force_refresh=force_refresh)
        print(f"  Model: {device.device_model}")
        print(f"  Android: {device.android_version}")

        print("\n  Hardware Video Encoders:")
        for codec in ['h264', 'h265', 'av1']:
            hw = device.video_encoders.get(codec, [])
            hw_str = ', '.join(hw[:2]) + ('...' if len(hw) > 2 else '') if hw else 'None'
            print(f"    {codec.upper()}: [{hw_str}]")
    except Exception as e:
        print(f"  Error: {e}")
        print("  (Make sure ADB device is connected)")
        device = None

    # Get optimal config
    print("\n" + "-" * 40)
    print("Optimal Configuration:")
    print("-" * 40)

    config = cache.get_optimal_config()
    print(f"  Codec: {config.codec.upper()}")
    print(f"  Use Hardware: {config.use_hardware}")
    print(f"  Encoder: {config.encoder_name or 'N/A'}")
    print(f"  PC Decoder: {config.pc_decoder or 'N/A'}")
    print(f"  PC Encoder: {config.pc_encoder or 'N/A'}")
    print(f"  Confidence: {config.confidence}")

    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)

    if device and device.has_hardware_encoder('h265') and pc.has_hardware_decoder('h265'):
        print("  Recommended: H.265 hardware (best quality/efficiency)")
    elif device and device.has_hardware_encoder('h264') and pc.has_hardware_decoder('h264'):
        print("  Recommended: H.264 hardware (good compatibility)")
    else:
        print("  Recommended: H.264 software (fallback)")

    print("\n  For recording:")
    if pc.has_hardware_encoder('h264'):
        print("    Use h264_nvenc/h264_qsv for fast encoding")
    else:
        print("    Use libx264 for universal compatibility")

    print("\n" + "=" * 60)


def list_cached_devices():
    """List all cached devices."""
    from scrcpy_py_ddlx.client.capability_cache import CapabilityCache

    cache = CapabilityCache.get_instance()
    devices = cache.list_cached_devices()

    print("\n" + "=" * 60)
    print("Cached Devices")
    print("=" * 60)

    if not devices:
        print("\n  No devices cached yet.")
        print("  Run without --list to detect and cache current device.")
    else:
        print(f"\n  Total: {len(devices)} device(s)\n")
        for i, dev in enumerate(devices, 1):
            print(f"  {i}. {dev['model']} (Android {dev['android_version']})")
            print(f"     Serial: {dev['serial']}")
            print(f"     Cache age: {dev['age_days']} days")
            print()

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description='Test capability cache')
    parser.add_argument('--refresh', action='store_true', help='Force refresh current device cache')
    parser.add_argument('--clear', action='store_true', help='Clear all cache and exit')
    parser.add_argument('--list', action='store_true', help='List all cached devices')
    args = parser.parse_args()

    if args.clear:
        from scrcpy_py_ddlx.client.capability_cache import CapabilityCache
        cache = CapabilityCache.get_instance()
        cache.clear_cache()
        print("All cache cleared.")
        return

    if args.list:
        list_cached_devices()
        return

    test_capability_cache(force_refresh=args.refresh)


if __name__ == "__main__":
    main()
