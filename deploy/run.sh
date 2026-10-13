#!/usr/bin/env bash
# 前台运行（Ctrl+C 停止）。调试时用这个，日志直接打在屏幕上。
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x ./.venv/bin/python ]; then
    echo "× 还没初始化，先跑 ./deploy/setup.sh" >&2
    exit 1
fi

exec ./.venv/bin/python bot.py
