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
set /p STRATEGIES="Enter strategy names separated by comma, or all for every optimizable strategy [default A,B,D]: "
if "%STRATEGIES%"=="" set STRATEGIES=A,B,D

:: Remove spaces from input
set STRATEGIES=%STRATEGIES: =%

:: Prompt for trials
set /p TRIALS="Enter number of trials to run per strategy [default 100]: "
if "%TRIALS%"=="" set TRIALS=100

echo.
echo =========================================================
echo  Target strategies: %STRATEGIES%
echo  Trials per strategy: %TRIALS%
echo  You can cancel anytime by pressing Ctrl+C. Progress is saved automatically.
echo =========================================================
echo.

:: Loop through strategy names
for %%s in (%STRATEGIES%) do (
    echo.
    echo ---------------------------------------------------------
    echo  [Start] Optimizing Strategy: %%s
    echo ---------------------------------------------------------
    .\venv\Scripts\python.exe backend\optimization_runner.py --strategy %%s --trials %TRIALS%
    if errorlevel 1 (
        echo [Warning] Error occurred while running strategy %%s
    )
)

echo.
echo =========================================================
echo  Optimization process completed.
echo =========================================================
echo.
pause
