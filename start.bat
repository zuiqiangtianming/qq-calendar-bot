@echo off
setlocal
cd /d "%~dp0"

if not exist ".env" (
    echo [!] 没找到 .env，请先把 .env.example 复制成 .env，并填入 QQ_BOT_APPID / QQ_BOT_SECRET
    echo.
    pause
    exit /b 1
)

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo 正在启动 QQ 日程管家（按 Ctrl+C 停止，关掉本窗口即退出）...
echo.

"%PY%" bot.py

echo.
echo 机器人已退出。
pause
