@echo off
chcp 65001 > nul
cd /d "%~dp0\.."

echo =========================================================
echo  StockTool - Scenario Batch Runner (3-Layer Parallel MC)
echo =========================================================
echo.

set PYTHONPATH=%cd%\backend;%cd%\backend\backtest
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo Starting parallel scenario batch run...
echo (6 strategies x 5 models x 10 MC runs = 300 runs)
echo.

.\venv\Scripts\python.exe backend\backtest\run_scenario_batch.py

echo.
pause
