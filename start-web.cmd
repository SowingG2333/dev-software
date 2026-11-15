@echo off
cd /d "%~dp0"
python scripts\launch_web.py
if errorlevel 1 pause
