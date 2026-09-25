@echo off
REM Double-click this, or run:  start.bat -Source sim
REM A .ps1 opened from cmd / Explorer / Git Bash is treated as a document
REM and Windows asks how to open it. This file hands it to PowerShell.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
