@echo off
chcp 65001 > nul
echo Registering Weekly StockTool Maintenance Task...
set SCRIPT_PATH=%~dp0run_weekly_maintenance.bat

:: 旧タスクが残っていれば削除
schtasks /delete /tn "StockTool_WeeklyMaintenance_Sunday_0200" /f >nul 2>&1

:: 毎週日曜日 朝 02:00 の週次メンテナンス登録
schtasks /create /tn "StockTool_WeeklyMaintenance_Sunday_0200" /tr "\"%SCRIPT_PATH%\"" /sc weekly /d SUN /st 02:00 /f

echo Weekly Task Registration Completed.
pause
