from __future__ import annotations
import os
from pathlib import Path
from app.platforms import default_data_dir
from app.platforms.files import private_directory

APP_NAME = "iPhone AutoBuyer"

def data_dir() -> Path:
    root = Path(os.environ.get("AUTOBUYER_DATA_DIR") or default_data_dir()).expanduser().absolute()
    return private_directory(root)

def resource_path(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / name
