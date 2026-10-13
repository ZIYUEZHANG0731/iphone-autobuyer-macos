"""One atomic writer for private local JSON records on both desktop systems."""
import json
import os
from pathlib import Path
import tempfile
from app.platforms.files import protect_path


def write_private_bytes(path: Path, value: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        # mkstemp creates a new private file; do not follow a pre-existing link.
        with os.fdopen(descriptor, 'wb') as stream:
            protect_path(temporary)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_private_json(path: Path, value):
    write_private_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'))
