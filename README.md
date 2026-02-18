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
   python -m PyInstaller --noconfirm thermal_leak_detector.spec
   ```
4. Run the app from: `dist\Thermal Leak Detector.exe`  
   
**Note:** The first run may be slow while Windows indexes the folder. Rasterio/GDAL may require [Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vscpp-redistributable) on clean Windows machines if you see DLL errors.

## Building via GitHub Actions (Windows + macOS)

The repo includes a workflow that builds a Windows `.exe` and a macOS binary in the cloud without a Mac or local build required.

1. **Trigger the build**
   - **Option A:** Push to `main` The workflow runs automatically.
   - **Option B:** Run it manually: open the **Actions** tab → select **Build Windows and macOS** → **Run workflow** → choose branch → **Run workflow**.

2. **Download the builds**
   - When the run has finished, open that run in the Actions tab.
   - At the bottom, under **Artifacts**, download **Thermal-Leak-Detector-Windows** (single `.exe`) and/or **Thermal-Leak-Detector-macOS** (single executable for Mac).
   - Share the file(s) as needed; no extra folder or Python install is required on the target machine.
