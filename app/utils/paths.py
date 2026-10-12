from __future__ import annotations
import os
from pathlib import Path
from app.platforms import default_data_dir

APP_NAME = "iPhone AutoBuyer"

def data_dir() -> Path:
    root = Path(os.environ.get("AUTOBUYER_DATA_DIR", default_data_dir()))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root

def resource_path(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / name
