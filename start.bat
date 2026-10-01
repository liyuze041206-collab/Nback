@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
set "GSPM_EXIT_CODE=%ERRORLEVEL%"
if not "%GSPM_EXIT_CODE%"=="0" pause
exit /b %GSPM_EXIT_CODE%
