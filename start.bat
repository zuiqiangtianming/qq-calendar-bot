@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".env" (
    echo [!] 没找到 .env，请先复制 .env.example 为 .env 并填入 QQ_BOT_APPID / QQ_BOT_SECRET
    pause
    exit /b 1
)

set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe

echo 正在启动 QQ 日程管家（Ctrl+C 停止）...
"%PY%" bot.py

echo.
echo 机器人已退出。
pause
