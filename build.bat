@echo off
chcp 65001 > nul
echo ============================================
echo   自动下单系统 Pro — 一键打包
echo ============================================

echo.
echo [1/5] 安装 Python 依赖...
cd /d "%~dp0backend"
pip install -r requirements.txt -q
if errorlevel 1 (echo 安装依赖失败 & exit /b 1)

echo [2/5] 安装 Playwright 浏览器...
playwright install chromium
if errorlevel 1 echo 警告: Playwright 浏览器安装失败，可继续打包

echo [3/5] 构建前端...
cd /d "%~dp0frontend"
call npm install --silent
if errorlevel 1 (echo npm install 失败 & exit /b 1)
call npm run build
if errorlevel 1 (echo 前端构建失败 & exit /b 1)
echo 前端构建完成 (输出到 backend/static/)

echo [4/5] PyArmor 加密源代码...
cd /d "%~dp0backend"
pip install "pyarmor" -q
if errorlevel 1 (echo ⚠️  PyArmor 安装失败，跳过加密步骤 & goto PACK)

rd /s /q "%~dp0backend\obf_build" 2>nul
pyarmor gen --output "%~dp0backend\obf_build" -r core services api main.py
if errorlevel 1 (echo ⚠️  PyArmor 加密失败，跳过加密步骤 & goto PACK)

echo PyArmor 加密完成

:PACK
echo [5/5] 打包 EXE...
cd /d "%~dp0"
pip install pyinstaller -q

for /f "delims=" %%i in ('python -c "import ddddocr,os;print(os.path.dirname(ddddocr.__file__))"') do set DDDDOCR_DIR=%%i
for /f "delims=" %%i in ('python -c "import playwright,os;print(os.path.dirname(playwright.__file__))"') do set PW_PKG=%%i

set PYARMOR_RUNTIME=
for /f "delims=" %%i in ('python -c "import glob,os; r=glob.glob(os.path.join(os.path.dirname(os.path.abspath(\".\")),'backend','obf_build','pyarmor_runtime_*')); print(r[0] if r else '')"') do set PYARMOR_RUNTIME=%%i

set EXTRA_DATA=
if not "%PYARMOR_RUNTIME%"=="" (
  for %%d in ("%PYARMOR_RUNTIME%") do set EXTRA_DATA=--add-data "%%~d;%%~nd"
  echo 检测到 PyArmor runtime: %PYARMOR_RUNTIME%
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
  --hidden-import "services.pick_bet_svc" ^
  --hidden-import "services.rotate_bet_svc" ^
  --hidden-import "services.custom_rotate_bet_svc" ^
  --hidden-import "services.custom_win_bet_svc" ^
  --hidden-import "services.draw_analysis_svc" ^
  --hidden-import "services.main_trend_bet_svc" ^
  --hidden-import "services.settlement_guard" ^
  --hidden-import "services.account_whitelist" ^
  --hidden-import "services.updater" ^
  --paths "%~dp0backend\obf_build" ^
  "%MAIN_PY%"
if errorlevel 1 (echo 打包失败 & exit /b 1)

echo.
echo ============================================
echo   打包完成！
echo   输出文件: backend\dist\自动下单系统Pro.exe
echo ============================================
