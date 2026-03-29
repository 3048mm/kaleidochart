@echo off
chcp 65001 > nul
cd /d "%~dp0\.."

echo =========================================================
echo  StockTool - Optuna Dashboard (Port 8080)
echo =========================================================
echo.

set PYTHONPATH=%cd%\backend;%cd%\backend\backtest
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

if not exist "data\optimization_trials.db" (
    echo Error: The database 'data\optimization_trials.db' does not exist.
    echo Please run 'run_optimization.bat' at least once to create the database before starting the dashboard.
    echo.
    pause
    exit /b 1
)

echo Starting Optuna Dashboard...
echo Open your browser and navigate to: http://127.0.0.1:8080
echo (Press Ctrl+C to stop the dashboard)
echo.

.\venv\Scripts\optuna-dashboard sqlite:///data/optimization_trials.db --port 8080 --host 0.0.0.0

echo.
pause
