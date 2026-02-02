@echo off
REM Build Thermal Leak Detector Windows .exe (run from this directory on Windows)
REM Requires: Python 3 with dependencies + PyInstaller

cd /d "%~dp0"

echo Installing build dependency...
pip install pyinstaller

echo Building executable...
pyinstaller --noconfirm thermal_leak_detector.spec

if %ERRORLEVEL% equ 0 (
    echo.
    echo Build complete. Run: dist\Thermal Leak Detector\Thermal Leak Detector.exe
) else (
    echo Build failed.
    exit /b 1
)
