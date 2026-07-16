@echo off
chcp 65001 > nul
echo Registering Daily StockTool Update Tasks...
set SCRIPT_PATH=%~dp0run_daily_update.bat

:: 旧タスクが残っていれば削除
schtasks /delete /tn "StockTool_DailyUpdate_0630" /f >nul 2>&1
schtasks /delete /tn "StockTool_DailyUpdate_1300" /f >nul 2>&1

:: 朝の 06:30 更新タスクの登録
schtasks /create /tn "StockTool_DailyUpdate_0630" /tr "\"%SCRIPT_PATH%\"" /sc daily /st 06:15 /f

:: 昼の 13:00 更新タスクの登録
schtasks /create /tn "StockTool_DailyUpdate_1300" /tr "\"%SCRIPT_PATH%\"" /sc daily /st 13:00 /f

echo Task Registration Completed.
pause
