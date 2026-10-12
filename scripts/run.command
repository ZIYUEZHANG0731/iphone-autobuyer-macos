#!/bin/zsh
set -e
cd "$(dirname "$0")/.."
.venv/bin/python scripts/build_accessibility.py
exec .venv/bin/python -m app.main
