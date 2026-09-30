@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_github.ps1" %*
if errorlevel 1 (
  echo.
  echo Upload failed. Your local files and commits have been kept.
  pause
  exit /b 1
)
echo.
echo Update complete.
pause
