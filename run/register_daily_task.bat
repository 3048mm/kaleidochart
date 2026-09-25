@echo off
REM Registers the daily update task in Task Scheduler.
REM
REM ASCII ONLY - see the header of register_weekly_maintenance.bat for why.
REM
REM Production layout (matches the task created by hand on 2026-05-28):
REM a single task with two weekly Tue-Sat triggers, 07:00 and 13:00.
REM Tue-Sat because there is no US session to fetch on Sun/Mon mornings.
REM schtasks /create cannot put two triggers on one task, so this calls
REM Register-ScheduledTask. SCRIPT_PATH is passed through the environment
REM to avoid quoting problems with spaces in the path.
setlocal

set "TASK_NAME=KaleidoChart_DailyUpdate"
set "SCRIPT_PATH=%~dp0run_daily_update.bat"

echo Registering Daily KaleidoChart Update Task...

if not defined SCRIPT_PATH goto :err_no_path
if "%SCRIPT_PATH%"=="" goto :err_no_path
if not exist "%SCRIPT_PATH%" goto :err_no_file

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $days='Tuesday','Wednesday','Thursday','Friday','Saturday'; $t=@((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At '07:00'),(New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At '13:00')); $a=New-ScheduledTaskAction -Execute ('\"' + $env:SCRIPT_PATH + '\"'); Register-ScheduledTask -TaskName $env:TASK_NAME -Trigger $t -Action $a -Force | Out-Null"
if errorlevel 1 goto :err_create

REM Verify by reading the command back - the exit code alone hid an empty
REM command once (see register_weekly_maintenance.bat).
schtasks /query /tn "%TASK_NAME%" /fo LIST /v | findstr /i /c:"run_daily_update.bat" >nul
if errorlevel 1 goto :err_verify

REM Only now remove tasks left over from the old project name (StockTool) and
REM from the previous version of this file - deleting them before a failed
REM registration would leave no daily task at all. Leaving them would run the
REM update twice per slot.
for %%T in ("StockTool_DailyUpdate" "StockTool_DailyUpdate_0700" "StockTool_DailyUpdate_1300") do (
    schtasks /delete /tn %%T /f >nul 2>&1
)

echo.
echo Task Registration Completed and verified:
schtasks /query /tn "%TASK_NAME%" /fo LIST /v | findstr /i /c:"run_daily_update.bat"
powershell -NoProfile -Command "(Get-ScheduledTask -TaskName $env:TASK_NAME).Triggers | ForEach-Object { $_.StartBoundary + '  ' + $_.DaysOfWeek }"
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
echo [ERROR] Register-ScheduledTask failed for "%TASK_NAME%".
pause
exit /b 1

:err_verify
echo [ERROR] The task was created but its command is not "%SCRIPT_PATH%".
echo         Inspect it with:
echo             schtasks /query /tn "%TASK_NAME%" /fo LIST /v
pause
exit /b 1
