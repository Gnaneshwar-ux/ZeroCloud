@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Install Python 3.12 or newer from python.org, then run this again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -r engine\requirements.txt
if errorlevel 1 exit /b 1
call pnpm install
if errorlevel 1 exit /b 1
node node_modules\electron\install.js
if errorlevel 1 exit /b 1
call pnpm build
if errorlevel 1 exit /b 1
call "Launch Vela Desktop.cmd"
