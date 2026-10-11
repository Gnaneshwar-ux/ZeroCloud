@echo off
setlocal
cd /d "%~dp0"
if not exist "dist\index.html" (
  echo Build the app with pnpm build first.
  pause
  exit /b 1
)
start "" "%~dp0dist\index.html"
