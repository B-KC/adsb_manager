"""Remember last connection (never the password)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SETTINGS_DIR = Path.home() / ".adsb_manager"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

DEFAULTS: dict[str, Any] = {
    "ssh_host": "",
    "ssh_user": "pi",
    "web_host": "",
    "recent_hosts": [],
}


def load() -> dict[str, Any]:
    data = dict(DEFAULTS)
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data.update({k: raw[k] for k in DEFAULTS if k in raw})
    except (OSError, json.JSONDecodeError):
        pass
    if not isinstance(data["recent_hosts"], list):
        data["recent_hosts"] = []
    return data


def save(data: dict[str, Any]) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    current = load()
    current.update(data)
    host = (current.get("ssh_host") or "").strip()
    recent = [h for h in current.get("recent_hosts", []) if h and h != host]
    if host:
        recent.insert(0, host)
    current["recent_hosts"] = recent[:12]
    SETTINGS_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
