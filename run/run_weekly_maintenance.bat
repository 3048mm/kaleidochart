@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

REM ASCII ONLY - cmd loses its byte offset in a UTF-8 .bat under "chcp 65001"
REM and can silently skip the following line. The line below is the one that
REM kills leftover pipeline processes, so skipping it is not harmless.
REM Kill dangling update_pipeline.py / weekly_maintenance.py processes.
powershell -Command "try { Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*update_pipeline.py*' -or $_.CommandLine -like '*weekly_maintenance.py*' } | ForEach-Object { Write-Host 'Terminating dangling pipeline process ID:' $_.ProcessId; Stop-Process -Id $_.ProcessId -Force } } catch {}"

cd /d "%~dp0.."
call venv\Scripts\activate.bat
python backend\scripts\weekly_maintenance.py --fix
