@echo off
cd /d "%~dp0"
python gen_license_gui.py
if errorlevel 1 pause
