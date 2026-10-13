from pathlib import Path
from runpy import run_path
import sys
from PyInstaller.utils.hooks import collect_data_files

root=Path(SPECPATH)
release=run_path(str(root/'app/__init__.py'))
native=run_path(str(root/'scripts/build_accessibility.py'))['build_cocoa_accessibility'](root)
datas=collect_data_files('playwright')+[
    (str(root/'app/gui/theme.qss'),'app/gui'),
    (str(root/'app/services/sim_store.html'),'app/services'),
    (str(root/'app/services/official_catalog.json'),'app/services'),
    (str(root/'app/services/official_stores.json'),'app/services'),
    (str(root/'app/services/daily_chrome_bridge.cjs'),'app/services'),
]
if sys.platform == 'win32':
    datas += collect_data_files('tzdata')
a=Analysis([str(root/'app/main.py')],pathex=[str(root)],binaries=[(str(native),'app/platforms/native')] if native else [],datas=datas,
    hiddenimports=['PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','cryptography.fernet'] + {
        'darwin':['keyring.backends.macOS','keyring.backends.macOS.api'],
        'win32':['keyring.backends.Windows','tzdata'],
    }.get(sys.platform,[]),
    # QtWebEngine's Windows DLL graph includes QtQuick/QML. Let the official
    # hooks collect its runtime rather than excluding those dependencies.
    excludes=[],
    noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='iPhone AutoBuyer',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    target_arch=None,codesign_identity=None,entitlements_file=None)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='iPhone AutoBuyer')
if sys.platform == 'darwin':
    app=BUNDLE(coll,name='iPhone AutoBuyer.app',icon=str(root/'app/gui/icon.icns'),
        bundle_identifier='com.autobuyer.apple.desktop',info_plist={
            'CFBundleDisplayName':'iPhone AutoBuyer',
            'CFBundleShortVersionString':release['__version__'],'CFBundleVersion':str(release['__build__']),
            'NSHighResolutionCapable':True,'LSMinimumSystemVersion':'13.0',
            'NSHumanReadableCopyright':'Apple AutoBuyer — local purchasing preparation tool',
        })
