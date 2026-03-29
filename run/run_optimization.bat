@echo off
chcp 65001 > nul
cd /d "%~dp0\.."

echo =========================================================
echo  StockTool - Automated Parameter Optimization (Optuna)
echo =========================================================
echo.

set PYTHONPATH=%cd%\backend;%cd%\backend\backtest
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

:: Prompt for strategy
set /p STRATEGY="Enter strategy name to optimize (default: B_theme_momentum): "
if "%STRATEGY%"=="" set STRATEGY=B_theme_momentum

:: Prompt for trials
set /p TRIALS="Enter number of trials to run (default: 100): "
if "%TRIALS%"=="" set TRIALS=100

echo.
echo Starting optimization for strategy: %STRATEGY% with %TRIALS% trials...
echo (You can cancel anytime by pressing Ctrl+C. Progress is saved automatically.)
echo.

.\venv\Scripts\python.exe backend\optimization_runner.py --strategy %STRATEGY% --trials %TRIALS%

echo.
pause
