#!/usr/bin/env bash
# 一次性初始化：建虚拟环境 → 装依赖 → 检查中文字体 → 生成 .env
# 用法：./deploy/setup.sh
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="${PYTHON:-python3}"

if ! command -v "$PY" >/dev/null 2>&1; then
    echo "× 找不到 $PY，先装 python3（Debian/Ubuntu: apt-get install -y python3 python3-venv）" >&2
    exit 1
fi

echo "==> 用 $("$PY" -V 2>&1) 创建虚拟环境 .venv"
"$PY" -m venv .venv
./.venv/bin/python -m pip install --upgrade pip -q

echo "==> 安装依赖"
./.venv/bin/pip install -q -r requirements.txt

echo "==> 检查中文字体"
./.venv/bin/python -c "import render; print(render.font_report())"

if [ ! -f .env ]; then
    cp .env.example .env
    echo
    echo "==> 已生成 .env，请把 AppID / AppSecret 填进去："
    echo "    vi $ROOT/.env"
fi

echo
echo "初始化完成。"
echo "  前台运行： ./deploy/run.sh"
echo "  常驻运行： sudo ./deploy/install-service.sh   （systemd，开机自启）"
