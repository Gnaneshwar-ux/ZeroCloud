@echo off
setlocal
cd /d "%~dp0"
if exist "node_modules\electron\dist\electron.exe" if exist ".venv\Scripts\python.exe" (
  start "" "node_modules\electron\dist\electron.exe" "desktop\main.cjs"
  exit /b
)
start "" "%~dp0dist\index.html"
