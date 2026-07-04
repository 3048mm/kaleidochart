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

echo Building frontend for production...
call npm run build
if errorlevel 1 (
    echo.
    echo [ERROR] Frontend build failed.
    echo [RECOVERY] Please check the console output for TypeScript or Vite compiler errors.
    echo.
    pause
    exit /b 1
)

echo.
echo [SUCCESS] Frontend build completed successfully!
echo.
pause
