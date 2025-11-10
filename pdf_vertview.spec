# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

datas = collect_data_files("fitz", includes=["**/*"])

project_root = (
    Path(__file__).parent
    if "__file__" in globals()
    else Path.cwd()
)
release_notes_path = project_root / "RELEASE_NOTES.md"
if release_notes_path.exists():
    datas += [(str(release_notes_path), "RELEASE_NOTES.md")]


extra_binaries = []


a = Analysis(
    ["pdf_vertview.py"],
    pathex=[],
    binaries=extra_binaries,
    datas=datas,
    hiddenimports=[
        "fitz",
        "fitz.fitz",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    upx=True,
    upx_exclude=[],
    name="pdf_vertview",
)
