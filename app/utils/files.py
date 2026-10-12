"""One atomic writer for private local JSON records on both desktop systems."""
import json
import os
from pathlib import Path
import tempfile


def write_private_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        # mkstemp creates a new private file; do not follow a pre-existing link.
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            temporary.chmod(0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
