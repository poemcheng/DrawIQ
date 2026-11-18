@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "DRAWIQ_LOG=%~dp0launcher.log"
set "DRAWIQ_PY=python.exe"
set "DRAWIQ_PY_ARGS="
if exist "%~dp0.venv\Scripts\python.exe" set "DRAWIQ_PY=%~dp0.venv\Scripts\python.exe"
echo DrawIQ is starting. Checking Python and desktop dependencies...
echo Startup log: "%DRAWIQ_LOG%"
>"%DRAWIQ_LOG%" echo [launcher] Checking the preferred Python runtime.
call :check_runtime
if not errorlevel 1 goto run_application
>>"%DRAWIQ_LOG%" echo [launcher] Preferred runtime failed. Trying the Python launcher: py -3.
set "DRAWIQ_PY=py.exe"
set "DRAWIQ_PY_ARGS=-3"
call :check_runtime
if not errorlevel 1 goto run_application
type "%DRAWIQ_LOG%"
echo DrawIQ could not find a Python runtime with the required desktop dependencies.
echo Please share the startup log shown above to diagnose this environment.
pause
exit /b 1

:run_application
type "%DRAWIQ_LOG%"
echo Starting DrawIQ. The sign-in window should appear shortly for desktop use.
"%DRAWIQ_PY%" %DRAWIQ_PY_ARGS% -u app.py %* >>"%DRAWIQ_LOG%" 2>&1
set "DRAWIQ_EXIT=%errorlevel%"
if not "%DRAWIQ_EXIT%"=="0" (
  type "%DRAWIQ_LOG%"
  pause
)
exit /b %DRAWIQ_EXIT%

:check_runtime
"%DRAWIQ_PY%" %DRAWIQ_PY_ARGS% -u -c "import sys; print('[launcher] Python: '+sys.executable,flush=True); from PyQt6 import QtCore,QtWidgets; import fitz,PIL,numpy,openpyxl,jsonschema; import workbench.ui; print('[launcher] Desktop imports passed.',flush=True)" >>"%DRAWIQ_LOG%" 2>&1
exit /b %errorlevel%
