"""Read the KRX auth key: environment variable KRX_API_KEY, else .env holding either the bare
key on one line or KEY=VALUE (KRX_API_KEY / AUTH_KEY)."""
from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "KRX_API_KEY"


def load_api_key(root: str | Path = ".", env_file: str = ".env") -> str:
    key = os.environ.get(ENV_VAR, "").strip()
    if key:
        return key
    path = Path(root) / env_file
    if not path.exists():
        raise FileNotFoundError(f"{path} not found and {ENV_VAR} not set")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            _, value = line.split("=", 1)
            return value.strip().strip("'\"")
        return line.strip("'\"")
    raise ValueError(f"{path} is empty")
