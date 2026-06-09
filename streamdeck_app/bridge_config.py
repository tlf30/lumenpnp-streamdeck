from __future__ import annotations

import json
import os
from pathlib import Path


def load_bridge_endpoint(
    config: dict | None = None,
    config_path: Path | None = None,
) -> tuple[str, int]:
    host = "127.0.0.1"
    port = 64738

    if config:
        host = config.get("bridge_host", host)
        port = int(config.get("bridge_port", port))

    info_path = Path.home() / ".openpnp2" / "openpnp-bridge.json"
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            host = info.get("host", host)
            port = int(info.get("port", port))
        except (OSError, ValueError, TypeError):
            pass

    return host, port