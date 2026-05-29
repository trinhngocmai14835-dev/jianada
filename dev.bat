@echo off
chcp 65001 > nul
echo 启动开发模式 (后端:8080, 前端:3000)...

start "后端" cmd /k "cd /d %~dp0backend && uvicorn main:app --reload --port 8080"
timeout /t 2 > nul
start "前端" cmd /k "cd /d %~dp0frontend && npm run dev"
echo 已启动，打开 http://localhost:3000
