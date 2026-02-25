# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Thermal Leak Detector (Windows .exe)
# One-file build: output is dist/Thermal Leak Detector.exe

import os
import sys
import rasterio

block_cipher = None

# Bundle ui logos so _ui_dir() / "logos" works when frozen
# Bundle rasterio's proj_data (contains proj.db) so PROJ can resolve CRS / EPSG when frozen
_proj_data_src = os.path.join(os.path.dirname(rasterio.__file__), 'proj_data')
# UPX can break macOS code signing / notarization; disable on darwin
_upx = sys.platform != 'darwin'

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
        'rasterio.serde', 'rasterio._env', 'rasterio._base',
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    exclude_binaries=False,
    name='Thermal Leak Detector',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=_upx,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['ui/logos/app_logo.ico'],
)

app = BUNDLE(
    exe,
    name='Thermal Leak Detector.app',
    icon='ui/logos/app_logo.icns', 
    bundle_identifier='com.avans.thermal-leak-detector',
    info_plist={
        'CFBundleShortVersionString': '3.0.0',
        'CFBundleBundleName': 'Thermal Leak Detector',
        'NSHighResolutionCapable': 'True',
    },
)