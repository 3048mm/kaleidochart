@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0.."
call venv\Scripts\activate.bat
python backend\scripts\daily_sync_job.py
