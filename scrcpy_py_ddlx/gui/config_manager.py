"""
Device configuration manager for scrcpy-py-ddlx GUI.

Manages multiple device configurations stored in JSON format.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict, field

logger = logging.getLogger(__name__)

# Default configuration directory
CONFIG_DIR = Path.home() / ".scrcpy-py-ddlx" / "configs"


@dataclass
class DeviceConfig:
    """Device configuration dataclass."""

    # Basic info
    name: str = "New Device"
    device_serial: str = ""

    # Connection settings
    connection_mode: str = "adb_tunnel"  # "adb_tunnel" or "network"
    host: str = ""
    control_port: int = 27184
    video_port: int = 27185
    audio_port: int = 27186
    stay_alive: bool = False

    # Video settings
    video_enabled: bool = True
    video_codec: str = "auto"  # "auto", "h264", "h265", "av1"
    video_bitrate: int = 2500000  # 2.5 Mbps (平衡画质和带宽)
    max_fps: int = 60
    bitrate_mode: str = "vbr"  # "vbr" or "cbr"
    i_frame_interval: float = 10.0

    # Audio settings
    audio_enabled: bool = False
    audio_codec: int = 3  # OPUS = 3

    # FEC settings
    video_fec_enabled: bool = False
    audio_fec_enabled: bool = False
    fec_group_size: int = 4
    fec_parity_count: int = 1

    # Server lifecycle
    push_server: bool = True
    reuse_server: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeviceConfig":
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class ConfigManager:
    """Manager for device configurations."""

    def __init__(self, config_dir: Optional[Path] = None):
        """
        Initialize config manager.

        Args:
            config_dir: Directory to store configurations. Defaults to ~/.scrcpy-py-ddlx/configs/
        """
        self.config_dir = config_dir or CONFIG_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._configs: Dict[str, DeviceConfig] = {}
        self._load_all_configs()

    def _load_all_configs(self):
        """Load all configurations from disk."""
        if not self.config_dir.exists():
            return

        for config_file in self.config_dir.glob("*.json"):
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    config = DeviceConfig.from_dict(data)
                    self._configs[config.name] = config
                    logger.debug(f"Loaded config: {config.name}")
            except Exception as e:
                logger.error(f"Failed to load config {config_file}: {e}")

        # Create default config if none exists
        if not self._configs:
            default_config = DeviceConfig(name="Default Device")
            self._configs[default_config.name] = default_config
            self.save_config(default_config.name)
            logger.info("Created default configuration")

    def get_config_names(self) -> List[str]:
        """Get list of configuration names."""
        return list(self._configs.keys())

    def get_config(self, name: str) -> Optional[DeviceConfig]:
        """Get configuration by name."""
        return self._configs.get(name)

    def save_config(self, name: str) -> bool:
        """
        Save configuration to disk.

        Args:
            name: Configuration name

        Returns:
            True if saved successfully
        """
        config = self._configs.get(name)
        if not config:
            logger.error(f"Config not found: {name}")
            return False

        config_file = self.config_dir / f"{name}.json"
        try:
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)
            logger.info(f"Saved config: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to save config {name}: {e}")
            return False

    def create_config(self, name: str) -> DeviceConfig:
        """
        Create a new configuration.

        Args:
            name: Configuration name

        Returns:
            New DeviceConfig instance
        """
        config = DeviceConfig(name=name)
        self._configs[name] = config
        self.save_config(name)
        return config

    def delete_config(self, name: str) -> bool:
        """
        Delete a configuration.

        Args:
            name: Configuration name

        Returns:
            True if deleted successfully
        """
        if name not in self._configs:
            logger.error(f"Config not found: {name}")
            return False

        # Remove from memory
        del self._configs[name]

        # Remove from disk
        config_file = self.config_dir / f"{name}.json"
        try:
            if config_file.exists():
                config_file.unlink()
            logger.info(f"Deleted config: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete config file {name}: {e}")
            return False

    def update_config(self, name: str, **kwargs) -> bool:
        """
        Update configuration fields.

        Args:
            name: Configuration name
            **kwargs: Fields to update

        Returns:
            True if updated successfully
        """
        config = self._configs.get(name)
        if not config:
            logger.error(f"Config not found: {name}")
            return False

        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)
            else:
                logger.warning(f"Unknown config field: {key}")

        return self.save_config(name)

    def rename_config(self, old_name: str, new_name: str) -> bool:
        """
        Rename a configuration.

        Args:
            old_name: Current name
            new_name: New name

        Returns:
            True if renamed successfully
        """
        if old_name not in self._configs:
            logger.error(f"Config not found: {old_name}")
            return False

        if new_name in self._configs:
            logger.error(f"Config already exists: {new_name}")
            return False

        config = self._configs[old_name]
        config.name = new_name
        self._configs[new_name] = config
        del self._configs[old_name]

        # Delete old file
        old_file = self.config_dir / f"{old_name}.json"
        if old_file.exists():
            old_file.unlink()

        # Save new file
        return self.save_config(new_name)


# Singleton instance
_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """Get the singleton ConfigManager instance."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager
