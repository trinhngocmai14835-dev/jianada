@echo off
chcp 65001 > nul
cd /d "%~dp0"
title R2 Whitelist Manager

where python >nul 2>nul
if errorlevel 1 goto NO_PYTHON

python -X utf8 "%~dp0tools\account_whitelist_menu.py"
exit /b %errorlevel%

:NO_PYTHON
echo Python not found. Please install Python or check PATH.
pause
exit /b 1
