"""Build the small macOS workaround; Windows/Linux have no native dependency."""
from pathlib import Path
import subprocess
import sys


def build_cocoa_accessibility(root, destination=None):
    if sys.platform != 'darwin':
        return None
    source = Path(root) / 'app/platforms/native/cocoa_accessibility.m'
    output = Path(destination) if destination else source.with_name('libautobuyer_cocoa_accessibility.dylib')
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['xcrun', 'clang', '-dynamiclib', '-O2', '-fno-objc-arc',
                    '-mmacosx-version-min=13.0', '-framework', 'Foundation',
                    '-Wl,-install_name,@rpath/libautobuyer_cocoa_accessibility.dylib',
                    str(source), '-o', str(output)], check=True)
    return output


if __name__ == '__main__':
    build_cocoa_accessibility(Path(__file__).resolve().parents[1])
