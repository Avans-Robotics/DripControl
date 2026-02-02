# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Thermal Leak Detector (Windows .exe)
# Output: dist/Thermal Leak Detector/Thermal Leak Detector.exe

import os
import rasterio

block_cipher = None

# Bundle ui logos so _ui_dir() / "logos" works when frozen
# Bundle rasterio's proj_data (contains proj.db) so PROJ can resolve CRS / EPSG when frozen
_proj_data_src = os.path.join(os.path.dirname(rasterio.__file__), 'proj_data')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('ui/logos', 'ui/logos'),
        (_proj_data_src, 'rasterio/proj_data'),
    ],
    hiddenimports=[
        'numpy', 'cv2', 'PIL', 'PySide6.QtSvg',
        'rasterio', 'rasterio.sample', 'rasterio._shim', 'rasterio.control',
        'rasterio.crs', 'rasterio.vrt', 'rasterio.warp', 'rasterio.enums',
        'rasterio._features', 'rasterio.features',
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
    name='Thermal Leak Detector',
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Thermal Leak Detector',
)
