"""Load Stream Deck controller settings from layered YAML config files."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SYSTEM_CONFIG_PATH = Path("/etc/streamdeck/config.yaml")
USER_CONFIG_PATH = Path.home() / ".config" / "streamdeck" / "config.yaml"
DEV_CONFIG_PATH = PACKAGE_ROOT / "config.yaml"

DEFAULT_CONFIG: dict[str, Any] = {
    "bridge_host": "127.0.0.1",
    "bridge_port": 64738,
    "notifications_enabled": True,
    "default_locked": True,
    "lock_idle_timeout_sec": 120,
    "lock_idle_warning_sec": 10,
    "jog_step_mm": 1.0,
    "default_speed_pct": 100.0,
    "poll_interval_ms": 250,
    "event_poll_interval_ms": 16,
    "deck_input_timeout_ms": 50,
    "dial_jog_coalesce_ms": 30,
    "vacuum_poll_interval_ms": 1000,
    "touchscreen_min_interval_ms": 500,
    "brightness": 60,
    "dial_default_step_mm": 1.0,
    "dial_xy_default_step_mm": 10.0,
    "dial_step_sizes_mm": [1.0, 0.1, 0.01],
    "dial_xy_step_sizes_mm": [10.0, 1.0, 0.1, 0.01],
    "jog_increment_dial_index": 4,
    "speed_dial_index": 5,
    "dial_unlock_timeout_sec": 10,
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config file must be a mapping: {path}")
    return data


def config_search_paths() -> list[Path]:
    paths: list[Path] = []
    if SYSTEM_CONFIG_PATH.exists():
        paths.append(SYSTEM_CONFIG_PATH)
    if DEV_CONFIG_PATH.exists():
        paths.append(DEV_CONFIG_PATH)
    override = os.environ.get("STREAMDECK_CONFIG")
    if override:
        paths.append(Path(override).expanduser())
    if USER_CONFIG_PATH.exists():
        paths.append(USER_CONFIG_PATH)
    return paths


def load_config() -> dict[str, Any]:
    """Merge defaults with system, development, and user config files."""
    config = copy.deepcopy(DEFAULT_CONFIG)
    for path in config_search_paths():
        config = _deep_merge(config, _read_yaml(path))
    return config


def ensure_user_config() -> Path:
    """Create ~/.config/streamdeck/config.yaml from the packaged example if missing."""
    USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if USER_CONFIG_PATH.exists():
        return USER_CONFIG_PATH

    for candidate in (
        Path("/usr/share/streamdeck/config.yaml.example"),
        PACKAGE_ROOT / "packaging" / "config.yaml.example",
    ):
        if candidate.exists():
            USER_CONFIG_PATH.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")
            return USER_CONFIG_PATH

    USER_CONFIG_PATH.write_text(
        "notifications_enabled: true\n"
        "lock_idle_timeout_sec: 120\n"
        "lock_idle_warning_sec: 10\n",
        encoding="utf-8",
    )
    return USER_CONFIG_PATH