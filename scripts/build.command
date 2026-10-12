#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
# Build/sign outside the workspace: its ignored-file watcher continually adds
# FinderInfo to files under dist, which codesign rejects while signing.
task_build_root=$(mktemp -d /tmp/autobuyer-build.XXXXXX)
trap 'rm -rf "$task_build_root"' EXIT
chflags -R nohidden .venv/lib/python*/site-packages/PySide6/Qt/plugins
.venv/bin/python scripts/make_icon.py
.venv/bin/python -m PyInstaller --noconfirm --clean --distpath "$task_build_root/dist" --workpath "$task_build_root/work" AppleAutoBuyer.spec
task_built_app="$task_build_root/dist/iPhone AutoBuyer.app"
chflags -R nohidden "$task_built_app"
xattr -dr com.apple.FinderInfo "$task_built_app" 2>/dev/null || true
xattr -dr com.apple.ResourceFork "$task_built_app" 2>/dev/null || true
codesign --force --deep --sign - "$task_built_app"
codesign --verify --deep --strict "$task_built_app"
mkdir -p dist
.venv/bin/python - <<'PY'
from pathlib import Path
import plistlib
import shutil
app=Path('dist/iPhone AutoBuyer.app')
if app.exists():
    with (app/'Contents/Info.plist').open('rb') as f:
        assert plistlib.load(f)['CFBundleIdentifier']=='com.autobuyer.apple.desktop'
    shutil.rmtree(app)
PY
ditto "$task_built_app" 'dist/iPhone AutoBuyer.app'
echo '已生成 dist/iPhone AutoBuyer.app；双击 scripts/install.command 安装到本机 Applications'
