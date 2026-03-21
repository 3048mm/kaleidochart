@echo off
chcp 65001 > nul
echo Registering Daily StockTool Update Task...
set SCRIPT_PATH=%~dp0run_daily_update.bat
schtasks /create /tn "StockTool_DailyUpdate" /tr "\"%SCRIPT_PATH%\"" /sc daily /st 08:00 /f
echo Task Registration Completed.
pause
