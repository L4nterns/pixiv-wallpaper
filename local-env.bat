@echo off
cd /d "%~dp0"
set "UV_CACHE_DIR=%CD%\.cache\uv"
set "UV_PYTHON_INSTALL_DIR=%CD%\.python"
set "UV_PYTHON_BIN_DIR=%CD%\.python-bin"
set "UV_PROJECT_ENVIRONMENT=%CD%\.venv"
set "PYTHONPYCACHEPREFIX=%CD%\.cache\pycache"
set "PYINSTALLER_CONFIG_DIR=%CD%\.cache\pyinstaller"
set "TEMP=%CD%\.cache\tmp"
set "TMP=%TEMP%"
if not exist "%TEMP%" mkdir "%TEMP%"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv was not found. Install uv before running this script.
  exit /b 1
)
exit /b 0
