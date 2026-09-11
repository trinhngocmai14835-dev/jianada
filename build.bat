@echo off
chcp 65001 > nul
echo ============================================
echo   AutoBet Pro - build package
echo ============================================

echo.
echo [1/5] Install Python dependencies...
cd /d "%~dp0backend"
pip install -r requirements.txt -q
if errorlevel 1 (echo Python dependency install failed & exit /b 1)

echo [2/5] Install Playwright Chromium...
playwright install chromium
if errorlevel 1 echo Warning: Playwright Chromium install failed, continue packaging

echo [3/5] Build frontend...
cd /d "%~dp0frontend"
call npm install --silent
if errorlevel 1 (echo npm install failed & exit /b 1)
call npm run build
if errorlevel 1 (echo frontend build failed & exit /b 1)
echo Frontend built into backend/static

echo [4/5] Obfuscate Python source with PyArmor...
cd /d "%~dp0backend"
rd /s /q "%~dp0backend\obf_build" 2>nul
pip install "pyarmor" -q
if errorlevel 1 (echo Warning: PyArmor install failed, packaging source directly & goto PACK)

pyarmor gen --output "%~dp0backend\obf_build" -r core services api main.py
if errorlevel 1 (
  echo Warning: PyArmor failed, packaging source directly
  rd /s /q "%~dp0backend\obf_build" 2>nul
  goto PACK
)
echo PyArmor obfuscation complete

:PACK
echo [5/5] Build EXE...
cd /d "%~dp0"
pip install pyinstaller -q

for /f "delims=" %%i in ('python -c "import ddddocr,os;print(os.path.dirname(ddddocr.__file__))"') do set DDDDOCR_DIR=%%i
for /f "delims=" %%i in ('python -c "import playwright,os;print(os.path.dirname(playwright.__file__))"') do set PW_PKG=%%i

set PYARMOR_RUNTIME=
for /f "delims=" %%i in ('python -c "import glob,os; r=glob.glob(os.path.join(os.path.abspath('backend'),'obf_build','pyarmor_runtime_*')); print(r[0] if r else '')"') do set PYARMOR_RUNTIME=%%i

set EXTRA_DATA=
if not "%PYARMOR_RUNTIME%"=="" (
  for %%d in ("%PYARMOR_RUNTIME%") do set EXTRA_DATA=--add-data "%%~d;%%~nd"
  echo PyArmor runtime detected: %PYARMOR_RUNTIME%
)

set MAIN_PY=main.py
if exist "%~dp0backend\obf_build\main.py" set MAIN_PY=%~dp0backend\obf_build\main.py

pyinstaller ^
  --onefile ^
  --noconsole ^
  --name "自动下单系统Pro" ^
  --add-data "backend\static;static" ^
  "--add-data=%DDDDOCR_DIR%;ddddocr" ^
  "--add-data=%PW_PKG%\driver;playwright/driver" ^
  %EXTRA_DATA% ^
  --hidden-import "uvicorn.logging" ^
  --hidden-import "uvicorn.loops.auto" ^
  --hidden-import "uvicorn.protocols.http.auto" ^
  --hidden-import "uvicorn.protocols.websockets.auto" ^
  --hidden-import "uvicorn.lifespan.on" ^
  --hidden-import "fastapi" ^
  --hidden-import "fastapi.staticfiles" ^
  --hidden-import "starlette.staticfiles" ^
  --hidden-import "aiofiles" ^
  --collect-all "playwright" ^
  --hidden-import "ddddocr" ^
  --hidden-import "tkinter" ^
  --hidden-import "tkinter.ttk" ^
  --hidden-import "tkinter.messagebox" ^
  --exclude-module "matplotlib" ^
  --collect-all "cryptography" ^
  --collect-data "certifi" ^
  --hidden-import "sqlite3" ^
  --hidden-import "_sqlite3" ^
  --hidden-import "api" ^
  --hidden-import "api.routes" ^
  --hidden-import "api.ws" ^
  --hidden-import "core" ^
  --hidden-import "core.db" ^
  --hidden-import "core.license" ^
  --hidden-import "core.process_env" ^
  --hidden-import "core.task_manager" ^
  --hidden-import "core.version" ^
  --hidden-import "services" ^
  --hidden-import "services.auto_bet_svc" ^
  --hidden-import "services.follow_bet_svc" ^
  --hidden-import "services.rush_bet_svc" ^
  --hidden-import "services.rotate_bet_svc" ^
  --hidden-import "services.custom_rotate_bet_svc" ^
  --hidden-import "services.custom_win_bet_svc" ^
  --hidden-import "services.four_code_win_bet_svc" ^
  --hidden-import "services.custom_rotate_analysis_svc" ^
  --hidden-import "services.custom_win_analysis_svc" ^
  --hidden-import "services.four_code_win_analysis_svc" ^
  --hidden-import "services.draw_analysis_svc" ^
  --hidden-import "services.draw_capture_svc" ^
  --hidden-import "services.main_trend_bet_svc" ^
  --hidden-import "services.settlement_guard" ^
  --hidden-import "services.account_whitelist" ^
  --hidden-import "services.updater" ^
  --paths "%~dp0backend\obf_build" ^
  "%MAIN_PY%"
if errorlevel 1 (echo pyinstaller failed & exit /b 1)

echo.
echo ============================================
echo   Build complete
echo   Output: backend\dist\自动下单系统Pro.exe
echo ============================================