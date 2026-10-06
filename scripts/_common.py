"""Small helpers shared by the setup scripts."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def yes(prompt: str, default: bool = True) -> bool:
    answer = ask(prompt + (" (Y/n)" if default else " (y/N)")).lower()
    return default if not answer else answer.startswith("y")


def load_or_create(path: Path, example: Path, role: str) -> dict:
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if raw.get("role", "kiosk") != role:
            sys.exit(f"{path} is set up for the '{raw.get('role')}' role. Use a separate "
                     "folder (copy of PAS) for each role, or delete config.yaml first.")
        return raw
    shutil.copy(example, path)
    print(f"Created {path.name} from {example.name}.")
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def save(path: Path, raw: dict) -> None:
    from backend.config import load_config

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# PAS configuration. See the config.*.example.yaml files for what each "
                 "setting does.\n")
        yaml.safe_dump(raw, fh, sort_keys=False, allow_unicode=True)
    load_config(path)  # validate what we wrote
    print(f"Saved {path}")
