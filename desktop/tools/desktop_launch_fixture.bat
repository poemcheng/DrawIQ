@echo off
if "%~1"=="" (
 call "%~dp0DrawIQ_Workbench_0.11.0\run.bat" --data "%~dp0DrawIQ_Workbench\data"
) else (
 call "%~dp0DrawIQ_Workbench_0.11.0\run.bat" %*
)
exit /b %errorlevel%
