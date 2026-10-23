@echo off
setlocal
call "%~dp0local-env.bat"
if errorlevel 1 goto fail
if not exist .venv\Scripts\python.exe (
  uv venv --python 3.12 .venv
  if errorlevel 1 goto fail
)
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
if errorlevel 1 goto fail
.venv\Scripts\python app.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo Launch failed. See the error above.
pause
exit /b 1
