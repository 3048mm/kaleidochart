@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

:: 残留パイプラインプロセスのチェックと強制終了
powershell -Command "try { Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*update_pipeline.py*' -or $_.CommandLine -like '*weekly_maintenance.py*' } | ForEach-Object { Write-Host 'Terminating dangling pipeline process ID:' $_.ProcessId; Stop-Process -Id $_.ProcessId -Force } } catch {}"

cd /d "%~dp0.."
call venv\Scripts\activate.bat
python backend\scripts\weekly_maintenance.py --fix
