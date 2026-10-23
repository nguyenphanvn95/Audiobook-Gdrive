# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules('audiobookgdrive')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('audiobookgdrive\\resources\\icon.png', 'audiobookgdrive\\resources'), ('audiobookgdrive\\resources\\icon.ico', 'audiobookgdrive\\resources')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AudiobookGdrive',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['D:\\My Projects\\AudiobookGdrive\\AudiobookGdrive Source Code\\audiobookgdrive\\resources\\icon.ico'],
)
