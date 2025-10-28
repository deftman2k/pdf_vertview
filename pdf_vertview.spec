# -*- mode: python ; coding: utf-8 -*-

block_cipher = None


a = Analysis(
    ["pdf_vertview.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        "fitz",
        "fitz.fitz",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt5.QtSql",
        "PyQt5.QtTest",
        "PyQt5.QtQml",
        "PyQt5.QtQuick",
        "PyQt5.QtOpenGL",
        "PyQt5.QtWebEngineWidgets",
        "PyQt5.QtWebSockets",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    [],
    [],
    exclude_binaries=True,
    name="pdf_vertview",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon="icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="pdf_vertview",
)
