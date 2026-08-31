@echo off
chcp 65001 > nul
setlocal
call "%~dp0build.bat"
if errorlevel 1 exit /b 1
set /p RELEASE_NOTES=请输入本次更新说明: 
python "%~dp0tools\prepare_release.py" --notes "%RELEASE_NOTES%"
if errorlevel 1 exit /b 1
echo 发布候选包已生成到 release_upload
