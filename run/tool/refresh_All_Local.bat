@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0../../"
call venv\Scripts\activate.bat

echo =========================================================
echo   StockTool - Fast Local Full Rebuild (T2-T5)
echo   ZERO internet download! Rebuilding from Parquet Master.
echo =========================================================
echo.

python backend/scripts/run_local_rebuild.py
pause
