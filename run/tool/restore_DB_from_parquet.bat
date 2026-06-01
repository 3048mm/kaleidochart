@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0../../"
call venv\Scripts\activate.bat
echo =========================================================
echo   Restore SQLite Cache from Parquet Master
echo =========================================================
python backend/scripts/run_production_restore.py
pause
