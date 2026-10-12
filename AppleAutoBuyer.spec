from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root=Path(SPECPATH)
datas=collect_data_files('playwright')+[
    (str(root/'app/gui/theme.qss'),'app/gui'),
    (str(root/'app/services/sim_store.html'),'app/services'),
    (str(root/'app/services/official_catalog.json'),'app/services'),
]
a=Analysis([str(root/'app/main.py')],pathex=[str(root)],binaries=[],datas=datas,
    hiddenimports=['keyring.backends.macOS','keyring.backends.macOS.api'],
    excludes=['PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtQml','PySide6.QtQuick'],
    noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='iPhone AutoBuyer',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    target_arch='arm64',codesign_identity=None,entitlements_file=None)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='iPhone AutoBuyer')
app=BUNDLE(coll,name='iPhone AutoBuyer.app',icon=str(root/'app/gui/icon.icns'),
    bundle_identifier='com.autobuyer.apple.desktop',info_plist={
    'CFBundleDisplayName':'iPhone AutoBuyer',
    'CFBundleShortVersionString':'0.4.0','CFBundleVersion':'8',
    'NSHighResolutionCapable':True,'LSMinimumSystemVersion':'13.0',
    'NSHumanReadableCopyright':'Apple AutoBuyer — local purchasing preparation tool',
    })
