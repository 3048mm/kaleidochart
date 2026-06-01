@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0../../"
call venv\Scripts\activate.bat
python backend/scripts/update_pipeline.py --rebuild-from T5 --skip-fetch
pause

