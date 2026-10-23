@echo off
setlocal
call "%~dp0local-env.bat"
if errorlevel 1 goto fail
uv venv --python 3.12 .venv
if errorlevel 1 goto fail
uv pip install --python .venv\Scripts\python.exe -r requirements.txt pyinstaller
if errorlevel 1 goto fail
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onedir --windowed --name PixivWallpaper --collect-all pystray --icon=assets\app-icon.ico --add-data "assets\app-icon.png;assets" app.py
if errorlevel 1 goto fail
echo Built: dist\PixivWallpaper\PixivWallpaper.exe
echo Keep the entire PixivWallpaper directory together.
pause
exit /b 0
:fail
echo Build failed. See the error above.
pause
exit /b 1
