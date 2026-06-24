@echo off
chcp 65001 > nul
echo Registering Run StockTool Server Tasks...
set SCRIPT_PATH=%~dp0run_server.bat

:: 旧タスクが残っていれば削除
schtasks /delete /tn "StockTool_RunServer" /f >nul 2>&1

:: PC 起動時にサーバーを起動
schtasks /create /tn "StockTool_RunServer" /tr "\"%SCRIPT_PATH%\"" /sc onstart /f


echo Task Registration Completed.
pause
