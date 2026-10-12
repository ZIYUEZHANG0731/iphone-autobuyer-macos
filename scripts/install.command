#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
task_app_destination="$HOME/Applications/iPhone AutoBuyer.app"
mkdir -p "$HOME/Applications"
if [[ -d "$task_app_destination" ]]; then
  mv "$task_app_destination" "$HOME/Applications/iPhone AutoBuyer.previous-$(date +%Y%m%d-%H%M%S).app"
fi
ditto 'dist/iPhone AutoBuyer.app' "$task_app_destination"
chflags -R nohidden "$task_app_destination"
xattr -dr com.apple.FinderInfo "$task_app_destination" 2>/dev/null || true
xattr -dr com.apple.ResourceFork "$task_app_destination" 2>/dev/null || true
codesign --force --deep --sign - "$task_app_destination"
codesign --verify --deep --strict "$task_app_destination"
echo "已安装到 $task_app_destination"
