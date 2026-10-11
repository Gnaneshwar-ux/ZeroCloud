@echo off
setlocal
cd /d "%~dp0"
if not exist "node_modules\electron\dist\electron.exe" (
  echo Desktop runtime not installed. Run Setup Vela.cmd first.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Local engine not installed. Run Setup Vela.cmd first.
  pause
  exit /b 1
)
start "" "node_modules\electron\dist\electron.exe" "desktop\main.cjs"
