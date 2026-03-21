@echo off
:: Move to the frontend directory
cd /d "%~dp0..\frontend"
if errorlevel 1 (
    echo.
    echo [ERROR] Frontend directory not found.
    echo [RECOVERY] Make sure the 'frontend' folder exists in the project root.
    echo.
    pause
    exit /b 1
)

echo Starting Vite development server...
call npm run dev
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to start frontend server.
    echo [RECOVERY] Please make sure Node.js is installed and you have run 'npm install' inside the 'frontend' folder.
    echo.
    pause
    exit /b 1
)

pause