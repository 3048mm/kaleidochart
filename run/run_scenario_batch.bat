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

echo Usage: run_scenario_batch.bat [--jobs B1,B2] [--list-jobs]
echo   --jobs      Comma-separated job names to run (default: all jobs)
echo   --list-jobs List available job names and exit
echo.
echo Starting parallel scenario batch run...
echo.

.\venv\Scripts\python.exe backend\backtest\run_scenario_batch.py %*

echo.
pause
