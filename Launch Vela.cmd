@echo off
setlocal
cd /d "%~dp0"
if exist "node_modules\electron\dist\electron.exe" if exist ".venv\Scripts\python.exe" (
  call "Launch Vela Desktop.cmd"
  exit /b
)
call "Launch Vela Browser.cmd"
