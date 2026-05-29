@echo off
cd /d %~dp0
echo 安装依赖...
pip install -r requirements.txt -q
echo.
echo 启动 Telegram 机器人...
python bot.py
pause
