@echo off
:: Move to the frontend directory
cd /d "%~dp0"

start /min "Backend" .\KickBackend.bat

timeout /t 3 /nobreak >nul

start /min "Frontend" .\KickFrontend.bat
