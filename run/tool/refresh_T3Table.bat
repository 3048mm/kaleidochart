@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0../../"
call venv\Scripts\activate.bat

echo =========================================================
echo   StockTool - Rebuild T3 (Indicators) Table
echo   Recomputes T3/T4 on the Parquet master, then T5
echo =========================================================
echo.
echo   NOTE: Stop the API server and the daily task first.
echo   The pipeline lock will refuse to run alongside them.
echo.

:: update_pipeline.py --rebuild-from T3 that does everything:
::   1. recompute T3 on Parquet (full history per symbol)
::   2. recompute T4 on Parquet
::   3. restore the SQLite hot cache from Parquet
::   4. recompute T5 and rotate
::
:: The previous version of this file ran run_production_restore.py first and
:: then --rebuild-from T3. That is no longer needed: T3 now reads the Parquet
:: master directly, so it always sees each symbol's full history
:: (ETFs from 2010-04, individual names from 2018-04).
python backend\scripts\update_pipeline.py --rebuild-from T3 --skip-fetch

echo.
echo Running health check...
python tools\db_health_check.py --all

echo.
echo === T3 REBUILD COMPLETED ===
pause
