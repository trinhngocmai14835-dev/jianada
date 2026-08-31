@echo off
chcp 65001 > nul
setlocal
if "%R2_BUCKET%"=="" (
  echo 请先设置 R2_BUCKET 环境变量
  exit /b 1
)
set /p CONFIRM=输入 PUBLISH 确认上传正式版本: 
python "%~dp0tools\publish_release.py" --bucket "%R2_BUCKET%" --confirm "%CONFIRM%"
