@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-offline-wheels.ps1" -RuntimeOnly
echo.
if errorlevel 1 (echo Torch import still failed.) else (echo Torch 2.14.1 CPU import works.)
pause
