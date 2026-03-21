@echo off
:: Move to the project root directory (one level up from the 'run' folder)
cd /d "%~dp0.."

echo Starting virtual environment...
call .\venv\Scripts\activate.bat
if errorlevel 1 (
    echo.
    echo [ERROR] Virtual environment 'venv' not found or failed to activate.
    echo [RECOVERY] Please ensure you have run the setup scripts or manually created 'venv' in the project root.
    echo.
    pause
    exit /b 1
)

echo Starting FastAPI server...
uvicorn api.main:app --reload --port 8001
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to start API Server.
    echo [RECOVERY] Check if 'uvicorn' is installed in the venv and if port 8001 is already in use.
    echo.
    pause
    exit /b 1
)

pause
