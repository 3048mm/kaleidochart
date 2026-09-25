@echo off
REM Registers the API server task (runs at PC startup) in Task Scheduler.
REM
REM ASCII ONLY - see the header of register_weekly_maintenance.bat for why.
setlocal

set "TASK_NAME=KaleidoChart_RunServer"
set "OLD_TASK_NAME=StockTool_RunServer"
set "SCRIPT_PATH=%~dp0run_server.bat"

echo Registering Run KaleidoChart Server Task...

if not defined SCRIPT_PATH goto :err_no_path
if "%SCRIPT_PATH%"=="" goto :err_no_path
if not exist "%SCRIPT_PATH%" goto :err_no_file

REM Remove the task registered under the old project name (StockTool).
schtasks /delete /tn "%OLD_TASK_NAME%" /f >nul 2>&1
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
schtasks /create /tn "%TASK_NAME%" /tr "\"%SCRIPT_PATH%\"" /sc onstart /f
if errorlevel 1 goto :err_create

REM Verify by reading the command back - schtasks reports SUCCESS even when the
REM command it stored is empty.
schtasks /query /tn "%TASK_NAME%" /fo LIST /v | findstr /i /c:"run_server.bat" >nul
if errorlevel 1 goto :err_verify

echo.
echo Task Registration Completed and verified:
schtasks /query /tn "%TASK_NAME%" /fo LIST /v | findstr /i /c:"run_server.bat"
pause
exit /b 0

:err_no_path
echo [ERROR] SCRIPT_PATH is empty.
echo         Run this file itself (double-click, or: cmd /c "%~f0").
echo         Do NOT paste its lines into PowerShell or Git Bash.
pause
exit /b 1

:err_no_file
echo [ERROR] Target script not found: %SCRIPT_PATH%
pause
exit /b 1

:err_create
echo [ERROR] schtasks /create failed for "%TASK_NAME%".
pause
exit /b 1

:err_verify
echo [ERROR] The task was created but its command is not "%SCRIPT_PATH%".
echo         Inspect it with:
echo             schtasks /query /tn "%TASK_NAME%" /fo LIST /v
pause
exit /b 1
