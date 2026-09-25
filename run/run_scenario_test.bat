@echo off
chcp 65001 > nul
cd /d "%~dp0\.."

echo =========================================================
echo  KaleidoChart - Scenario Test Runner
echo =========================================================
echo.

set PYTHONPATH=%cd%\backend;%cd%\backend\backtest
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

:: Prompt for start date
set /p START_DATE="Enter start date (YYYY-MM-DD) [default: 2021-03-26]: "
if "%START_DATE%"=="" set START_DATE=2021-03-26

:: Prompt for end date
set /p END_DATE="Enter end date (YYYY-MM-DD)   [default: 2026-03-25]: "
if "%END_DATE%"=="" set END_DATE=2026-03-25

:: Prompt for config TOML
set /p CONFIG_PATH="Enter config TOML path      [default: data/screener_presets.toml]: "
if "%CONFIG_PATH%"=="" set CONFIG_PATH=data/screener_presets.toml

:: Prompt for min score
set /p MIN_SCORE="Enter min score (1 or 2)      [default: 1]: "
if "%MIN_SCORE%"=="" set MIN_SCORE=1

:: Prompt for output directory
set /p OUTPUT_DIR="Enter output directory       [default: output/scenario]: "
if "%OUTPUT_DIR%"=="" set OUTPUT_DIR=output/scenario

echo.
echo =========================================================
echo  Starting simulation with parameters:
echo   - Period:      %START_DATE% to %END_DATE%
echo   - Config TOML: %CONFIG_PATH%
echo   - Min Score:   %MIN_SCORE%
echo   - Output Dir:  %OUTPUT_DIR%
echo =========================================================
echo.

.\venv\Scripts\python.exe backend\backtest\scenario_runner.py ^
  --start-date "%START_DATE%" ^
  --end-date "%END_DATE%" ^
  --config-path "%CONFIG_PATH%" ^
  --min-score "%MIN_SCORE%" ^
  --output-dir "%OUTPUT_DIR%"

echo.
pause
