@echo off
chcp 65001 > nul
echo ============================================
echo   自动下单系统 Pro — 一键打包
echo ============================================

echo.
echo [1/6] 安装固定版本的构建依赖...
cd /d "%~dp0"
pip install -r requirements-release.txt -q
if errorlevel 1 (echo 安装依赖失败 & exit /b 1)

echo [2/6] 运行自动化测试...
python -m pytest -q
if errorlevel 1 (echo 自动化测试失败 & exit /b 1)

echo [3/6] 安装 Playwright 浏览器...
playwright install chromium
if errorlevel 1 echo 警告: Playwright 浏览器安装失败，可继续打包

echo [4/6] 构建前端...
cd /d "%~dp0frontend"
call npm ci --silent
if errorlevel 1 (echo npm ci 失败 & exit /b 1)
call npm run build
if errorlevel 1 (echo 前端构建失败 & exit /b 1)
echo 前端构建完成 (输出到 backend/static/)

echo [5/6] PyArmor 加密源代码...
cd /d "%~dp0backend"
pyarmor --version > "%TEMP%\jianada-pyarmor-version.txt" 2>&1
if errorlevel 1 (echo PyArmor 不可用 & exit /b 1)
findstr /i /c:"trial" /c:"non-profits" "%TEMP%\jianada-pyarmor-version.txt" >nul
if not errorlevel 1 if not "%ALLOW_TRIAL_PYARMOR%"=="1" (
  type "%TEMP%\jianada-pyarmor-version.txt"
  echo 正式构建禁止使用 PyArmor 试用/非商业许可证
  exit /b 1
)

rd /s /q "%~dp0backend\obf_build" 2>nul
pyarmor gen --output "%~dp0backend\obf_build" -r core services api main.py
if errorlevel 1 (
  if "%ALLOW_UNOBFUSCATED_BUILD%"=="1" (
    echo 警告: 已显式允许未加密调试构建
  ) else (
    echo PyArmor 加密失败，正式构建已中止
    exit /b 1
  )
)

echo PyArmor 加密完成

:PACK
echo [6/6] 打包 EXE...
cd /d "%~dp0backend"

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
  --clean ^
  --noconfirm ^
  --onefile ^
  --noconsole ^
  --name "自动下单系统Pro" ^
  --add-data "static;static" ^
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
  --hidden-import "core.task_manager" ^
  --hidden-import "core.version" ^
  --hidden-import "services" ^
  --hidden-import "services.auto_bet_svc" ^
  --hidden-import "services.follow_bet_svc" ^
  --hidden-import "services.rush_bet_svc" ^
  --hidden-import "services.pick_bet_svc" ^
  --hidden-import "services.rotate_bet_svc" ^
  --hidden-import "services.settlement_guard" ^
  --hidden-import "services.account_whitelist" ^
  --hidden-import "services.updater" ^
  --paths "%~dp0backend\obf_build" ^
  "%MAIN_PY%"
if errorlevel 1 (echo 打包失败 & exit /b 1)
if not exist "%~dp0backend\dist\自动下单系统Pro.exe" (echo 未找到打包输出 & exit /b 1)

echo.
echo ============================================
echo   打包完成！
echo   输出文件: backend\dist\自动下单系统Pro.exe
echo ============================================
