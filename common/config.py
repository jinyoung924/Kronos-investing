"""YAML config loading, dotted-key access, config hashing and required-value checks."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """A required config value is missing or null."""


def load_config(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"config {path} did not parse to a mapping")
    return cfg


def cfg_get(cfg: dict, key: str, default: Any = None) -> Any:
    """Dotted access: cfg_get(cfg, "infer.profiles.base.lookback")."""
    node: Any = cfg
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def cfg_override(cfg: dict, overrides: dict[str, Any]) -> dict:
    """Return a deep copy with dotted keys overridden (None values are ignored)."""
    out = copy.deepcopy(cfg)
    for key, value in overrides.items():
        if value is None:
            continue
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def require(cfg: dict, key: str) -> Any:
    """Return cfg[key]; raise ConfigError when the key is absent or null (a `[사용자]` value not filled in)."""
    value = cfg_get(cfg, key, None)
    if value is None:
        raise ConfigError(f"configs value '{key}' is null or missing: fill it in configs/base.yaml (see docs/spec.md)")
    return value


def config_hash(cfg: dict, length: int = 12) -> str:
    """sha256 of the config serialised as JSON with sorted keys: key order never changes the hash."""
    blob = json.dumps(cfg, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:length]


def dump_config(cfg: dict, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
