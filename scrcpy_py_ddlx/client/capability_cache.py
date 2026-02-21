"""
Capability cache for automatic codec selection.

This module provides automatic detection and caching of device/PC codec capabilities,
enabling optimal codec selection on first use.

Features:
- Per-device caching (by serial number)
- Automatic device detection
- 30-day cache expiry
- PC capability caching

Usage:
    from scrcpy_py_ddlx.client.capability_cache import CapabilityCache

    # Get or create cache (auto-detects on first run)
    cache = CapabilityCache.get_instance()

    # Get optimal configuration for specific device
    config = cache.get_optimal_config(device_serial="abc123")

    # Auto-detect connected device
    config = cache.get_optimal_config()  # Uses first connected device
"""

import json
import os
import platform
import subprocess
import logging
import time
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

# Cache file location
CACHE_DIR = Path.home() / ".cache" / "scrcpy-py-ddlx"
CACHE_FILE = CACHE_DIR / "capability_cache.json"

# Cache policy: permanent (hardware capabilities don't change)
# Use --refresh to force re-detection if needed


@dataclass
class DeviceCapability:
    """Android device capability info (hardware encoders only)."""
    device_model: str = ""
    android_version: str = ""
    video_encoders: Dict[str, List[str]] = field(default_factory=dict)  # Only hardware encoders
    last_updated: float = 0.0

    def has_hardware_encoder(self, codec: str) -> bool:
        """Check if device has hardware encoder for codec."""
        codec = codec.lower().replace('.', '')
        return len(self.video_encoders.get(codec, [])) > 0

    def get_hardware_encoder(self, codec: str) -> Optional[str]:
        """Get first hardware encoder name for codec."""
        codec = codec.lower().replace('.', '')
        encoders = self.video_encoders.get(codec, [])
        return encoders[0] if encoders else None


@dataclass
class PCCapability:
    """PC capability info (hardware codecs only)."""
    os: str = ""
    nvidia_cuda: bool = False
    nvidia_nvenc: bool = False
    nvidia_nvdec: bool = False
    intel_qsv: bool = False
    amd_amf: bool = False
    decoders: Dict[str, List[str]] = field(default_factory=dict)  # Only hardware decoders
    encoders: Dict[str, List[str]] = field(default_factory=dict)  # Only hardware encoders
    last_updated: float = 0.0

    def has_hardware_decoder(self, codec: str) -> bool:
        """Check if PC has hardware decoder for codec."""
        codec = codec.lower().replace('.', '')
        return len(self.decoders.get(codec, [])) > 0

    def has_hardware_encoder(self, codec: str) -> bool:
        """Check if PC has hardware encoder for codec."""
        codec = codec.lower().replace('.', '')
        return len(self.encoders.get(codec, [])) > 0

    def get_hardware_decoder(self, codec: str) -> Optional[str]:
        """Get first hardware decoder name for codec."""
        codec = codec.lower().replace('.', '')
        decoders = self.decoders.get(codec, [])
        return decoders[0] if decoders else None

    def get_hardware_encoder(self, codec: str) -> Optional[str]:
        """Get first hardware encoder name for codec."""
        codec = codec.lower().replace('.', '')
        encoders = self.encoders.get(codec, [])
        return encoders[0] if encoders else None


@dataclass
class OptimalConfig:
    """Optimal configuration result."""
    codec: str = "h264"  # Selected video codec
    use_hardware: bool = True  # Use hardware encoding
    encoder_name: Optional[str] = None  # Encoder name (if hardware)
    pc_decoder: Optional[str] = None  # PC decoder for playback
    pc_encoder: Optional[str] = None  # PC encoder for recording
    confidence: str = "high"  # high, medium, low


class CapabilityCache:
    """
    Singleton class for capability caching and optimal config selection.

    Caching Strategy:
    - PC capabilities: Single cache entry (one PC)
    - Device capabilities: Per-serial cache entries
    - Cache key format: device_{serial} (e.g., "device_abc123")
    """
    _instance: Optional['CapabilityCache'] = None

    def __init__(self):
        self._cache: Dict = {}
        self._loaded = False
        self._pc_capability: Optional[PCCapability] = None
        self._device_capabilities: Dict[str, DeviceCapability] = {}  # Serial -> Capability

    @classmethod
    def get_instance(cls) -> 'CapabilityCache':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_connected_device_serial(self) -> Optional[str]:
        """Get the serial of the first connected ADB device."""
        try:
            result = subprocess.run(
                ['adb', 'devices'],
                capture_output=True,
                text=True,
                timeout=5
            )
            lines = result.stdout.strip().split('\n')
            for line in lines[1:]:  # Skip header "List of devices attached"
                parts = line.split('\t')
                if len(parts) >= 2 and parts[1] == 'device':
                    return parts[0]  # Return first connected device serial
        except Exception as e:
            logger.debug(f"Failed to get connected devices: {e}")
        return None

    def _normalize_serial(self, serial: Optional[str]) -> str:
        """Normalize device serial for cache key."""
        if serial:
            # Remove colons and spaces for cleaner keys
            return serial.replace(':', '_').replace(' ', '_')
        return 'auto'  # For auto-detect mode

    def load_cache(self) -> bool:
        """Load cache from file."""
        if self._loaded:
            return True

        try:
            if CACHE_FILE.exists():
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    self._cache = json.load(f)
                logger.debug(f"Loaded capability cache from {CACHE_FILE}")
        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
            self._cache = {}

        self._loaded = True
        return True

    def save_cache(self) -> bool:
        """Save cache to file."""
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, indent=2, ensure_ascii=False)
            logger.debug(f"Saved capability cache to {CACHE_FILE}")
            return True
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")
            return False

    def _run_adb_command(self, cmd: str, device_serial: Optional[str] = None, timeout: int = 10) -> str:
        """Run ADB command and return output."""
        try:
            adb_cmd = ["adb"]
            if device_serial:
                adb_cmd.extend(["-s", device_serial])
            adb_cmd.extend(["shell", cmd])

            result = subprocess.run(
                adb_cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.stdout
        except Exception as e:
            logger.debug(f"ADB command failed: {e}")
            return ""

    def _get_device_model(self, device_serial: Optional[str] = None) -> str:
        """Get device model."""
        brand = self._run_adb_command("getprop ro.product.brand", device_serial).strip()
        model = self._run_adb_command("getprop ro.product.model", device_serial).strip()
        return f"{brand} {model}".strip() or "unknown"

    def _get_android_version(self, device_serial: Optional[str] = None) -> str:
        """Get Android version."""
        return self._run_adb_command("getprop ro.build.version.release", device_serial).strip() or "unknown"

    def _is_hardware_encoder(self, name: str) -> bool:
        """Check if encoder name indicates hardware encoding.

        Hardware encoder naming conventions by vendor:
        - Qualcomm: OMX.qcom.*, c2.qti.*
        - MediaTek: OMX.MTK.*, c2.mtk.*
        - Samsung Exynos: OMX.Exynos.*, OMX.sec.*, c2.exynos.*
        - HiSilicon (Huawei): OMX.hisi.*, c2.hisi.*
        - Intel: OMX.Intel.*
        - NVIDIA: OMX.NVIDIA.*
        - Amlogic: OMX.amlogic.*, c2.amlogic.*
        - Rockchip: OMX.rk.*, c2.rk.*
        - Allwinner: OMX.allwinner.*
        - Vivante: OMX.VIV.*

        Software encoders (to exclude):
        - Google: OMX.google.*
        - Android default: c2.android.*
        - Some vendor software: contains 'sw', 'soft', 'software'
        """
        # Hardware encoder prefixes (OMX and C2/Codec2 formats)
        hardware_prefixes = [
            # Qualcomm (Snapdragon)
            'OMX.qcom.', 'c2.qti.',
            # MediaTek (Dimensity, Helio)
            'OMX.MTK.', 'c2.mtk.',
            # Samsung (Exynos)
            'OMX.Exynos.', 'OMX.sec.', 'c2.exynos.', 'c2.sec.',
            # HiSilicon (Huawei/Kirin)
            'OMX.hisi.', 'c2.hisi.',
            # Intel
            'OMX.Intel.',
            # NVIDIA (Tegra)
            'OMX.NVIDIA.',
            # Amlogic (TV boxes)
            'OMX.amlogic.', 'c2.amlogic.',
            # Rockchip
            'OMX.rk.', 'c2.rk.',
            # Allwinner
            'OMX.allwinner.',
            # Vivante
            'OMX.VIV.',
            # Spreadtrum/UNISOC
            'OMX.sp.', 'c2.sp.',
            # Google Pixel (some use hardware)
            'OMX.Image.',
        ]

        # Software encoder prefixes (always exclude)
        software_prefixes = [
            'OMX.google.',      # Google software
            'c2.android.',      # Android default software
            'c2.vivo.',         # Vivo (often software)
            'c2.oppo.',         # OPPO (often software)
            'c2.xiaomi.',       # Xiaomi software wrapper
            'OMX.FS.',          # FSL (Freescale) software
        ]

        # Software keywords (middle of name)
        software_keywords = [
            'google', 'android', 'sw', 'soft', 'software',
            'ffmpeg', 'x264', 'x265', 'openh264',
        ]

        name_lower = name.lower()

        # First check if explicitly software
        for prefix in software_prefixes:
            if name_lower.startswith(prefix.lower()):
                return False

        # Check for software keywords
        for keyword in software_keywords:
            if keyword in name_lower:
                return False

        # Check for hardware prefixes
        for prefix in hardware_prefixes:
            if name_lower.startswith(prefix.lower()):
                return True

        # Unknown encoder - assume hardware if it looks like hardware
        # Hardware encoders typically have vendor prefixes
        return any(vendor in name_lower for vendor in [
            'qcom', 'qti', 'mtk', 'exynos', 'hisi', 'sec',
            'intel', 'nvidia', 'amlogic', 'rk', 'rockchip',
        ])

    def _parse_codec_type(self, mime_type: str) -> Optional[str]:
        """Parse MIME type to codec string."""
        mime_map = {
            'video/avc': 'h264', 'video/h264': 'h264',
            'video/hevc': 'h265', 'video/h265': 'h265',
            'video/av01': 'av1', 'video/av1': 'av1',
        }
        return mime_map.get(mime_type.lower())

    def _query_android_capabilities(self, device_serial: Optional[str] = None) -> DeviceCapability:
        """Query Android device capabilities (hardware encoders only)."""
        import re

        device_model = self._get_device_model(device_serial)
        android_version = self._get_android_version(device_serial)

        logger.info(f"Querying device capabilities: {device_model} (Android {android_version})")

        # Only store hardware encoders
        video_encoders = {
            'h264': [],
            'h265': [],
            'av1': [],
        }

        # Try dumpsys media.player (Android 10+)
        output = self._run_adb_command("dumpsys media.player 2>/dev/null", device_serial, timeout=15)

        if output and "Encoder" in output:
            current_media_type = None
            lines = output.split('\n')

            for line in lines:
                media_match = re.match(r"Media type '([^']+)'", line)
                if media_match:
                    current_media_type = media_match.group(1)
                    continue

                codec_match = re.match(r'\s+Encoder "([^"]+)"', line)
                if codec_match and current_media_type:
                    codec_name = codec_match.group(1)
                    codec_type = self._parse_codec_type(current_media_type)

                    if codec_type and codec_type in video_encoders:
                        # Only store hardware encoders
                        if self._is_hardware_encoder(codec_name):
                            if codec_name not in video_encoders[codec_type]:
                                video_encoders[codec_type].append(codec_name)

        # Fallback to dumpsys media.codec if needed
        if not any(video_encoders.values()):
            output = self._run_adb_command("dumpsys media.codec 2>/dev/null", device_serial, timeout=15)

            if output and "OMX" in output:
                current_codec = None
                current_type = None

                for line in output.split('\n'):
                    codec_match = re.match(r'\s*(OMX[\.\w]+|c2[\.\w]+)$', line.strip())
                    if codec_match:
                        current_codec = codec_match.group(1)
                        current_type = None
                        continue

                    type_match = re.match(r'\s*type:\s*(.+)', line)
                    if type_match and current_codec:
                        current_type = type_match.group(1).strip()
                        codec_type = self._parse_codec_type(current_type)

                        if codec_type and codec_type in video_encoders:
                            is_encoder = 'encoder' in current_codec.lower()
                            if is_encoder and self._is_hardware_encoder(current_codec):
                                if current_codec not in video_encoders[codec_type]:
                                    video_encoders[codec_type].append(current_codec)

        return DeviceCapability(
            device_model=device_model,
            android_version=android_version,
            video_encoders=video_encoders,
            last_updated=time.time()
        )

    def _query_pc_capabilities(self) -> PCCapability:
        """Query PC capabilities (hardware codecs only)."""
        logger.info("Querying PC capabilities...")

        os_info = f"{platform.system()} {platform.release()}"

        # Check NVIDIA
        nvidia_cuda = False
        nvidia_nvenc = False
        nvidia_nvdec = False

        try:
            result = subprocess.run(['nvidia-smi'], capture_output=True, timeout=5)
            if result.returncode == 0:
                nvidia_cuda = True
                nvidia_nvenc = True  # Assume NVENC available if nvidia-smi works
                nvidia_nvdec = True
        except:
            pass

        # Check Intel QSV
        intel_qsv = False
        try:
            result = subprocess.run(
                ['wmic', 'path', 'win32_VideoController', 'get', 'name'],
                capture_output=True, text=True, timeout=5
            )
            if 'Intel' in result.stdout:
                intel_qsv = True
        except:
            pass

        # Check AMD AMF
        amd_amf = False
        try:
            result = subprocess.run(
                ['wmic', 'path', 'win32_VideoController', 'get', 'name'],
                capture_output=True, text=True, timeout=5
            )
            if 'AMD' in result.stdout or 'Radeon' in result.stdout:
                amd_amf = True
        except:
            pass

        # Build hardware decoder/encoder lists only
        decoders = {'h264': [], 'h265': [], 'av1': []}
        encoders = {'h264': [], 'h265': [], 'av1': []}

        if nvidia_cuda:
            decoders['h264'].append('h264_cuvid')
            decoders['h265'].append('hevc_cuvid')
            decoders['av1'].append('av1_cuvid')
            encoders['h264'].append('h264_nvenc')
            encoders['h265'].append('hevc_nvenc')
            encoders['av1'].append('av1_nvenc')

        if intel_qsv:
            decoders['h264'].append('h264_qsv')
            decoders['h265'].append('hevc_qsv')
            encoders['h264'].append('h264_qsv')
            encoders['h265'].append('hevc_qsv')

        if amd_amf:
            encoders['h264'].append('h264_amf')
            encoders['h265'].append('hevc_amf')

        return PCCapability(
            os=os_info,
            nvidia_cuda=nvidia_cuda,
            nvidia_nvenc=nvidia_nvenc,
            nvidia_nvdec=nvidia_nvdec,
            intel_qsv=intel_qsv,
            amd_amf=amd_amf,
            decoders=decoders,
            encoders=encoders,
            last_updated=time.time()
        )

    def get_device_capability(self, device_serial: Optional[str] = None, force_refresh: bool = False) -> DeviceCapability:
        """
        Get device capability (from cache or query).

        Args:
            device_serial: Device serial number (None = auto-detect first device)
            force_refresh: Force refresh even if cache exists

        Returns:
            DeviceCapability object
        """
        self.load_cache()

        # Auto-detect device serial if not provided
        actual_serial = device_serial
        if actual_serial is None:
            actual_serial = self._get_connected_device_serial()
            if actual_serial is None:
                logger.warning("No ADB device connected, returning empty capability")
                return DeviceCapability(
                    device_model="unknown",
                    android_version="unknown",
                    video_encoders={'h264': [], 'h265': [], 'av1': []},
                    last_updated=0
                )
            logger.info(f"Auto-detected device: {actual_serial}")

        # Generate cache key
        cache_key = f"device_{self._normalize_serial(actual_serial)}"

        # Check memory cache first (device cache is permanent)
        if not force_refresh and cache_key in self._device_capabilities:
            cached_cap = self._device_capabilities[cache_key]
            logger.debug(f"Using memory-cached device capability for {actual_serial}")
            return cached_cap

        # Check file cache (device cache is permanent, never expires)
        cached = self._cache.get(cache_key, {})
        if not force_refresh and cached and cached.get('device_model'):
            logger.debug(f"Using file-cached device capability for {actual_serial} (permanent cache)")
            capability = DeviceCapability(
                device_model=cached.get('device_model', ''),
                android_version=cached.get('android_version', ''),
                video_encoders=cached.get('video_encoders', {}),  # List[str] per codec
                last_updated=cached.get('last_updated', 0)
            )
            self._device_capabilities[cache_key] = capability
            return capability

        # Query fresh
        logger.info(f"Querying capabilities for device: {actual_serial}")
        capability = self._query_android_capabilities(actual_serial)

        # Cache it (both memory and file)
        self._device_capabilities[cache_key] = capability
        self._cache[cache_key] = asdict(capability)
        self.save_cache()

        return capability

    def get_pc_capability(self, force_refresh: bool = False) -> PCCapability:
        """
        Get PC capability (from cache or query).

        Args:
            force_refresh: Force refresh even if cache exists

        Returns:
            PCCapability object
        """
        self.load_cache()

        # Check memory cache
        if not force_refresh and self._pc_capability is not None:
            return self._pc_capability

        # Check file cache (permanent, use --refresh to force update)
        if not force_refresh:
            cached = self._cache.get('pc_capability', {})
            if cached and cached.get('os'):
                logger.debug("Using cached PC capability")
                self._pc_capability = PCCapability(
                    os=cached.get('os', ''),
                    nvidia_cuda=cached.get('nvidia_cuda', False),
                    nvidia_nvenc=cached.get('nvidia_nvenc', False),
                    nvidia_nvdec=cached.get('nvidia_nvdec', False),
                    intel_qsv=cached.get('intel_qsv', False),
                    amd_amf=cached.get('amd_amf', False),
                    decoders=cached.get('decoders', {}),
                    encoders=cached.get('encoders', {}),
                    last_updated=cached.get('last_updated', 0)
                )
                return self._pc_capability

        # Query fresh
        self._pc_capability = self._query_pc_capabilities()
        self._cache['pc_capability'] = asdict(self._pc_capability)
        self.save_cache()

        return self._pc_capability

    def get_optimal_config(self, device_serial: Optional[str] = None) -> OptimalConfig:
        """
        Get optimal configuration for device + PC combination.

        Selection priority (strict hardware first):
        1. H.265 hardware encoder (device) + H.265 hardware decoder (PC)
        2. H.264 hardware encoder (device) + H.264 hardware decoder (PC)
        3. H.265 hardware encoder (device) only (PC uses software)
        4. H.264 hardware encoder (device) only (PC uses software)
        5. Fallback to H.264 software

        Args:
            device_serial: Device serial number

        Returns:
            OptimalConfig with recommended settings
        """
        device = self.get_device_capability(device_serial)
        pc = self.get_pc_capability()

        logger.info(f"Selecting optimal config for {device.device_model}")

        # Priority list: AV1 → H.265 → H.264
        # (codec, require_both_hardware)
        priorities = [
            ('av1', True),    # AV1 both hardware (best)
            ('h265', True),   # H.265 both hardware
            ('h264', True),   # H.264 both hardware
            ('av1', False),   # AV1 device hardware only
            ('h265', False),  # H.265 device hardware only
            ('h264', False),  # H.264 device hardware only
        ]

        for codec, require_both_hw in priorities:
            device_has_hw = device.has_hardware_encoder(codec)
            pc_has_hw_decoder = pc.has_hardware_decoder(codec)
            pc_has_hw_encoder = pc.has_hardware_encoder(codec)

            # Must have device hardware encoder
            if not device_has_hw:
                continue

            # If requiring both hardware, check PC
            if require_both_hw and not pc_has_hw_decoder:
                continue

            # Found a match!
            encoder_name = device.get_hardware_encoder(codec)
            pc_decoder = pc.get_hardware_decoder(codec)  # May be None
            pc_encoder = pc.get_hardware_encoder(codec)  # May be None

            confidence = "high" if (device_has_hw and pc_has_hw_decoder) else "medium"

            logger.info(f"Selected {codec.upper()} hardware: device={encoder_name}, pc_decoder={pc_decoder}")

            return OptimalConfig(
                codec=codec,
                use_hardware=True,
                encoder_name=encoder_name,
                pc_decoder=pc_decoder,
                pc_encoder=pc_encoder,
                confidence=confidence
            )

        # Fallback to H.264 software
        logger.warning("No hardware encoder found, falling back to H.264 software")
        return OptimalConfig(
            codec='h264',
            use_hardware=False,
            encoder_name=None,
            pc_decoder=None,
            pc_encoder=None,
            confidence='low'
        )

    def get_cache_info(self) -> Dict:
        """Get cache information."""
        self.load_cache()

        info = {
            'cache_file': str(CACHE_FILE),
            'cache_exists': CACHE_FILE.exists(),
            'entries': list(self._cache.keys()),
            'cached_devices': [],
        }

        # PC capability info
        if 'pc_capability' in self._cache:
            pc = self._cache['pc_capability']
            age_days = (time.time() - pc.get('last_updated', 0)) / (24 * 3600)
            info['pc_capability_age_days'] = round(age_days, 1)

        # Device capability info
        for key, value in self._cache.items():
            if key.startswith('device_'):
                serial = key[7:]  # Remove "device_" prefix
                model = value.get('device_model', 'unknown')
                android = value.get('android_version', 'unknown')
                age_days = (time.time() - value.get('last_updated', 0)) / (24 * 3600)
                info['cached_devices'].append({
                    'serial': serial,
                    'model': model,
                    'android_version': android,
                    'age_days': round(age_days, 1),
                })

        return info

    def clear_cache(self, device_serial: Optional[str] = None):
        """
        Clear the cache.

        Args:
            device_serial: Clear only this device's cache (None = clear all)
        """
        if device_serial:
            # Clear specific device
            cache_key = f"device_{self._normalize_serial(device_serial)}"
            if cache_key in self._cache:
                del self._cache[cache_key]
            if cache_key in self._device_capabilities:
                del self._device_capabilities[cache_key]
            logger.info(f"Cleared cache for device: {device_serial}")
        else:
            # Clear all
            self._cache = {}
            self._pc_capability = None
            self._device_capabilities = {}
            logger.info("Cleared all capability cache")

        # Save to file
        if self._cache:
            self.save_cache()
        elif CACHE_FILE.exists():
            CACHE_FILE.unlink()

    def list_cached_devices(self) -> List[Dict]:
        """List all cached devices."""
        info = self.get_cache_info()
        return info.get('cached_devices', [])


# Convenience function
def get_optimal_codec(device_serial: Optional[str] = None) -> str:
    """
    Get optimal codec string for device.

    Args:
        device_serial: Device serial number (None = auto-detect)

    Returns:
        Codec string: "h264", "h265", or "av1"
    """
    cache = CapabilityCache.get_instance()
    config = cache.get_optimal_config(device_serial)
    return config.codec


def get_connected_device_serial() -> Optional[str]:
    """
    Get the serial of the first connected ADB device.

    Returns:
        Device serial or None if no device connected
    """
    cache = CapabilityCache.get_instance()
    return cache._get_connected_device_serial()


__all__ = [
    'CapabilityCache',
    'DeviceCapability',
    'PCCapability',
    'OptimalConfig',
    'get_optimal_codec',
    'get_connected_device_serial',
]
