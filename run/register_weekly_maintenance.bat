@echo off
REM Registers the weekly maintenance task (Sunday 02:00) in Task Scheduler.
REM
REM ASCII ONLY - do not put Japanese comments in this file. cmd mis-tracks its
REM byte offset in a UTF-8 batch file under "chcp 65001" and can silently skip
REM the next line. See doc/agent_execution_rules.md and the
REM upstream-data-diagnosis skill (a daily run fetched zero rows this way).
setlocal

set "TASK_NAME=StockTool_WeeklyMaintenance_Sunday_0200"
set "SCRIPT_PATH=%~dp0run_weekly_maintenance.bat"

echo Registering Weekly StockTool Maintenance Task...

REM Guard. On 2026-08-01 this task was registered with an EMPTY command
REM (<Command>""</Command>). schtasks still reported SUCCESS, so nobody noticed;
REM the task then fired every Sunday and died instantly with 0x80070057
REM (ERROR_INVALID_PARAMETER) - three missed runs (08-09 / 08-16 / 08-23).
if not defined SCRIPT_PATH goto :err_no_path
if "%SCRIPT_PATH%"=="" goto :err_no_path
if not exist "%SCRIPT_PATH%" goto :err_no_file

schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
schtasks /create /tn "%TASK_NAME%" /tr "\"%SCRIPT_PATH%\"" /sc weekly /d SUN /st 02:00 /f
if errorlevel 1 goto :err_create

REM Verify by reading the command back. The exit code alone is not enough -
REM that is exactly what hid the 2026-08-01 breakage.
schtasks /query /tn "%TASK_NAME%" /fo LIST /v | findstr /i /c:"run_weekly_maintenance.bat" >nul
if errorlevel 1 goto :err_verify

echo.
echo Weekly Task Registration Completed and verified:
schtasks /query /tn "%TASK_NAME%" /fo LIST /v | findstr /i /c:"run_weekly_maintenance.bat"
pause
exit /b 0

:err_no_path
echo [ERROR] SCRIPT_PATH is empty.
echo         Run this file itself (double-click, or: cmd /c "%~f0").
echo         Do NOT paste its lines into PowerShell or Git Bash - %%~dp0 and
echo         %%SCRIPT_PATH%% only expand inside cmd, and an empty expansion
echo         registers a task that can never run.
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
