@echo off
REM Registers the daily update tasks in Task Scheduler.
REM
REM ASCII ONLY - see the header of register_weekly_maintenance.bat for why.
REM
REM WARNING - this file does NOT match what is currently registered on the
REM machine. Production runs a single task "StockTool_DailyUpdate" with two
REM weekly Tue-Sat triggers (07:00 / 13:00), created by hand on 2026-05-28.
REM This file creates two /sc daily tasks under different names, so running it
REM ADDS tasks instead of replacing that one - the update would then run twice
REM per slot, and also on Sun/Mon when there is no US session to fetch.
REM Reconcile the two before using this file.
setlocal

set "TASK_MORNING=StockTool_DailyUpdate_0700"
set "TASK_NOON=StockTool_DailyUpdate_1300"
set "SCRIPT_PATH=%~dp0run_daily_update.bat"

echo Registering Daily StockTool Update Tasks...

if not defined SCRIPT_PATH goto :err_no_path
if "%SCRIPT_PATH%"=="" goto :err_no_path
if not exist "%SCRIPT_PATH%" goto :err_no_file

schtasks /delete /tn "%TASK_MORNING%" /f >nul 2>&1
schtasks /delete /tn "%TASK_NOON%" /f >nul 2>&1

schtasks /create /tn "%TASK_MORNING%" /tr "\"%SCRIPT_PATH%\"" /sc daily /st 07:00 /f
if errorlevel 1 goto :err_create
schtasks /create /tn "%TASK_NOON%" /tr "\"%SCRIPT_PATH%\"" /sc daily /st 13:00 /f
if errorlevel 1 goto :err_create

REM Verify by reading the commands back - schtasks reports SUCCESS even when the
REM command it stored is empty.
schtasks /query /tn "%TASK_MORNING%" /fo LIST /v | findstr /i /c:"run_daily_update.bat" >nul
if errorlevel 1 goto :err_verify
schtasks /query /tn "%TASK_NOON%" /fo LIST /v | findstr /i /c:"run_daily_update.bat" >nul
if errorlevel 1 goto :err_verify

echo.
echo Task Registration Completed and verified:
schtasks /query /tn "%TASK_MORNING%" /fo LIST /v | findstr /i /c:"run_daily_update.bat"
schtasks /query /tn "%TASK_NOON%" /fo LIST /v | findstr /i /c:"run_daily_update.bat"
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
echo [ERROR] schtasks /create failed.
pause
exit /b 1

:err_verify
echo [ERROR] A task was created but its command is not "%SCRIPT_PATH%".
echo         Inspect it with:
echo             schtasks /query /tn "%TASK_MORNING%" /fo LIST /v
echo             schtasks /query /tn "%TASK_NOON%" /fo LIST /v
pause
exit /b 1
