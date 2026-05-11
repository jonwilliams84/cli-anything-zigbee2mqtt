"""Connection profile for cli-anything-zigbee2mqtt.

Stores MQTT broker creds, topic prefix, kubectl target (for restart / external
converter mgmt), and frontend URL. Lives at ~/.config/cli-anything-zigbee2mqtt.json.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "cli-anything-zigbee2mqtt.json"

DEFAULTS: dict[str, Any] = {
    # MQTT broker
    "mqtt_host": None,
    "mqtt_port": 1883,
    "mqtt_username": None,
    "mqtt_password": None,
    # z2m base topic (`zigbee2mqtt` is upstream default, but many setups
    # use `z2m` — we expose both `base_topic` for telemetry and an
    # explicit `bridge_base_topic` for the bridge request/response surface
    # because z2m sometimes uses a different prefix for that).
    "base_topic": "zigbee2mqtt",
    # Optional frontend HTTP UI (some queries are easier from REST)
    "frontend_url": None,
    "request_timeout": 15,
    # Kubernetes — used by `bridge restart` and `converter` file mgmt
    "k8s_namespace": "zigbee2mqtt",
    "k8s_deployment": "zigbee2mqtt",
    "k8s_container": "zigbee2mqtt",
    "k8s_data_path": "/app/data",
}


def load_config(path: Optional[Path] = None) -> dict:
    p = path or DEFAULT_CONFIG_PATH
    out = dict(DEFAULTS)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                out.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    for k in list(out.keys()):
        env = "CLI_Z2M_" + k.upper()
        if env in os.environ:
            v = os.environ[env]
            if isinstance(DEFAULTS.get(k), bool):
                out[k] = v.lower() in ("1", "true", "yes", "on")
            elif isinstance(DEFAULTS.get(k), int):
                try:
                    out[k] = int(v)
                except ValueError:
                    out[k] = v
            else:
                out[k] = v
    return out


def save_config(cfg: dict, path: Optional[Path] = None) -> Path:
    p = path or DEFAULT_CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    return p


def merge_cli_overrides(cfg: dict, **kwargs) -> dict:
    out = dict(cfg)
    for k, v in kwargs.items():
        if v is not None:
            out[k] = v
    return out
