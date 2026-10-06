@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-offline-wheels.ps1"
echo.
if errorlevel 1 (echo Install failed.) else (echo Install finished.)
pause
