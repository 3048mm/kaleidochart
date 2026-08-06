@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

:: Norton 等のセキュリティ製品が HTTPS を傍受していると curl_cffi（yfinance が
:: cookie/crumb 取得に使う）の証明書検証が失敗し、全銘柄が "possibly delisted" に
:: なる（2026-08-06 に全期間再構築が全滅）。傍受用のルート証明書があれば curl に教える。
:: 該当ファイルが無い環境では何もしない。
if not defined CURL_CA_BUNDLE if exist "C:\ProgramData\Norton\Antivirus\wscert.pem" set "CURL_CA_BUNDLE=C:\ProgramData\Norton\Antivirus\wscert.pem"
cd /d "%~dp0../../"
call venv\Scripts\activate.bat

echo =========================================================
echo   StockTool - Complete Data Pipeline Rebuild (Step 3)
echo   Running Safely in Sandbox (stocktool_restoring.db)
echo =========================================================
echo.

:: 1. Prompt for Parquet Master cleaning
::    ARCHIVES the Parquet Master instead of deleting it. A full rebuild refetches
::    from Yahoo, so any symbol whose series was truncated upstream is lost forever
::    if the old generations were deleted (17 symbols hit this on 2026-08-02).
::    Details and recovery: backend/scripts/archive_parquet_master.py
set /p CLEAN_PARQUET="Archive Parquet Master Cold Cache for pure rebuild? (y/N) [default: N]: "
if /i "%CLEAN_PARQUET%"=="y" (
    python backend\scripts\archive_parquet_master.py
    if errorlevel 1 (
        echo Aborting rebuild to avoid data loss.
        pause
        exit /b 1
    )
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
