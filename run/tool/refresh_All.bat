@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0../../"
call venv\Scripts\activate.bat
echo =========================================================
echo   StockTool - Complete Data Pipeline Rebuild (Step 3)
echo =========================================================
echo.

:: Prompt for Parquet Master cleaning
set /p CLEAN_PARQUET="Clean Parquet Master Cold Cache for pure rebuild? (y/N) [default: N]: "
if /i "%CLEAN_PARQUET%"=="y" (
    echo Cleaning Parquet Master Cache...
    if exist data\parquet_master (
        rd /s /q data\parquet_master
    )
    echo Clean completed.
    echo.
)

python backend/scripts/update_pipeline.py --re-calculate
pause
