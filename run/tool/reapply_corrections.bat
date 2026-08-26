@echo off
REM Re-applies the manual price corrections recorded in data/price_corrections.toml.
REM
REM WHEN: right after a full rebuild (run/tool/refresh_All.bat). A rebuild refetches
REM       from Yahoo, so every hand-applied correction is rolled back. This is the
REM       same category as step 4 of the post-rebuild checklist in
REM       .claude/skills/parquet-data-quality/SKILL.md section 9.
REM
REM ASCII ONLY - cmd mis-tracks its byte offset in a UTF-8 .bat under "chcp 65001"
REM and can silently skip the next line. See doc/agent_execution_rules.md section 6.
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONPATH=backend

cd /d "%~dp0../../"
call venv\Scripts\activate.bat

REM Show what would happen first. --dry-run writes nothing.
python backend\scripts\reapply_corrections.py --dry-run
if errorlevel 1 (
    echo.
    echo [ERROR] The ledger could not be read. Nothing was applied.
    pause
    exit /b 1
)

echo.
set /p GO="Apply these corrections? (y/N) [default: N]: "
if /i not "%GO%"=="y" (
    echo Aborted. Nothing was written.
    pause
    exit /b 0
)

python backend\scripts\reapply_corrections.py --apply
if errorlevel 1 (
    echo.
    echo [ERROR] At least one correction failed. Check the output above.
)
pause
