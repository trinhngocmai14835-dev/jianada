@echo off
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8080 "^') do (
    if not "%%a"=="0" taskkill /F /PID %%a >nul 2>&1
)
cd /d "C:\Users\Administrator\Desktop\jianada\backend"
python -m uvicorn main:app --host 127.0.0.1 --port 8080
pause