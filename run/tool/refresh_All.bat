@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0../../"
call venv\Scripts\activate.bat

echo =========================================================
echo   StockTool - Complete Data Pipeline Rebuild (Step 3)
echo   Running Safely in Sandbox (stocktool_restoring.db)
echo =========================================================
echo.

:: 1. Prompt for Parquet Master cleaning
set /p CLEAN_PARQUET="Clean Parquet Master Cold Cache for pure rebuild? (y/N) [default: N]: "
if /i "%CLEAN_PARQUET%"=="y" (
    echo Cleaning Parquet Master Cache...
    if exist data\parquet_master (
        rd /s /q data\parquet_master
    )
    echo Clean completed.
    echo.
)

:: 2. Redirect SQLite connection to Sandbox DB to prevent production corruption
echo Step 1: Diverting active database to Sandbox...
set STOCKTOOL_DB_PATH=data/stocktool_restoring.db

:: 3. Run full calculation (8.8 years history) safely inside Sandbox DB
echo Step 2: Running pipeline calculations (re-calculate full history)...
python backend/scripts/update_pipeline.py --re-calculate

:: 4. Clean up Sandbox DB files to reclaim disk space immediately
echo.
echo Step 3: Cleaning up temporary Sandbox DB files...
if exist data\stocktool_restoring.db (
    del /f /q data\stocktool_restoring.db
)
if exist data\stocktool_restoring.db-journal (
    del /f /q data\stocktool_restoring.db-journal
)
if exist data\stocktool_restoring.db-wal (
    del /f /q data\stocktool_restoring.db-wal
)
if exist data\stocktool_restoring.db-shm (
    del /f /q data\stocktool_restoring.db-shm
)

:: 5. Reset path to Production and restore fresh lightweight 2-year cache
set STOCKTOOL_DB_PATH=
echo.
echo Step 4: Restoring production stocktool.db with fresh 2-year cache...
python backend/scripts/run_production_restore.py

echo.
echo === SANDBOX REBUILD & RESTORE COMPLETED SUCCESSFULLY! ===
pause
