@echo off
cd /d "%~dp0"
python scripts\desktop.py
if errorlevel 1 pause
