@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 exit /b 1
set "PYTHONUTF8=1"
set "APP_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "APP_PYTHON=%~dp0.venv\Scripts\python.exe"
"%APP_PYTHON%" "%~dp0launcher.py" %*
set "APP_EXIT_CODE=%ERRORLEVEL%"
if not "%APP_EXIT_CODE%"=="0" (
  echo Startup failed. Read the message above.
  if not "%XUNPIN_NO_PAUSE%"=="1" pause
)
exit /b %APP_EXIT_CODE%
