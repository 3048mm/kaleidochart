@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0../../"
call venv\Scripts\activate.bat

echo =========================================================
echo   StockTool - Rebuild T3 (Indicators) Table
echo   Syncing from Parquet and Re-calculating T3-T5
echo =========================================================
echo.

:: 1. Sync SQL from Parquet Master to ensure starting from clean source
echo Step 1: Restoring clean 2-year cache from Parquet Master to SQL DB...
python backend/scripts/run_production_restore.py

echo.
:: 2. Re-calculate indicators (T3) and downstream (T4, T5)
echo Step 2: Re-calculating T3 Indicators (skipping fetch)...
python backend/scripts/update_pipeline.py --rebuild-from T3 --skip-fetch

echo.
echo === T3 REBUILD COMPLETED SUCCESSFULLY ===
pause
