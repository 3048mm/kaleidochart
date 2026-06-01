@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0../../"
call venv\Scripts\activate.bat
echo =========================================================
echo   Database and Parquet Master Health Check
echo =========================================================
python tools/db_health_check.py --parquet --all --check-nulls
pause
