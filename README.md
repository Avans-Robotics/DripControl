# Thermal Leak Detector

Simple cross-platform tool to detect irrigation leaks from thermal GeoTIFFs.

## Setup
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Building a Windows .exe

On **Windows**, from the project root:

1. Activate venv on Windows:
   
   1a. Run PowerShell as your user (not admin), then:
   ```bash
   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
   ```
   1b. Activate the same window:
   ```bash
   venv\Scripts\Activate.ps1
   ```

2. Install dependencies and PyInstaller:
   ```bash
   pip install -r requirements.txt
   pip install pyinstaller
   ```
3. Build:
   ```bash
   python -m Pyinstaller --noconfirm thermal_leak_detector.spec
   ```
   Or double‑click `build_exe.bat`.

4. Run the app from: `dist\Thermal Leak Detector\Thermal Leak Detector.exe`  
   You can zip/copy the whole `Thermal Leak Detector` folder to distribute it; no Python install is needed on the target PC.

**Note:** The first run may be slow while Windows indexes the folder. Rasterio/GDAL may require [Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vscpp-redistributable) on clean Windows machines if you see DLL errors.
