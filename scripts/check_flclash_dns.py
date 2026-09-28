"""Read only DNS diagnostics; never print proxy credentials or subscription URLs."""

import json
import os
from pathlib import Path

import yaml

root = Path(os.environ["APPDATA"]) / "com.follow" / "clash"
active = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
dns = active.get("dns", {})
print(
    "Active DNS:",
    json.dumps(
        {
            key: dns.get(key)
            for key in ("enable", "enhanced-mode", "fake-ip-filter-mode", "fake-ip-filter")
        },
        ensure_ascii=False,
    ),
)
prefs = json.loads((root / "shared_preferences.json").read_text(encoding="utf-8"))
config = prefs.get("flutter.config", {})
if isinstance(config, str):
    config = json.loads(config)


def inspect(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}/{key}"
            if key.replace("-", "").lower() in {
                "overridedns",
                "fakeipfilter",
                "fakeipfiltermode",
                "enhancedmode",
            }:
                print(child_path, json.dumps(child, ensure_ascii=False))
            elif isinstance(child, (dict, list)):
                inspect(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, (dict, list)):
                inspect(child, f"{path}/{index}")


inspect(config)
