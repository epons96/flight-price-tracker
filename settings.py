"""Tracker settings: read from the TRACKER_CONFIG environment variable (a JSON string; in GitHub
Actions it comes from a secret so the route is never published) or, failing that, from config.json."""

import json
import os
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"


def load():
    text = os.environ.get("TRACKER_CONFIG")
    if not text and CONFIG_FILE.exists():
        text = CONFIG_FILE.read_text(encoding="utf-8")
    if not text:
        raise SystemExit("Missing settings: set TRACKER_CONFIG or copy config.example.json to config.json")
    cfg = json.loads(text)
    cfg.setdefault("timezone", "UTC")
    return cfg
