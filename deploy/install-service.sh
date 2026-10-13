#!/usr/bin/env bash
# 装成 systemd 服务：开机自启、进程挂了自动拉起。需要 root。
# 用法：sudo ./deploy/install-service.sh
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
SVC=qq-calendar-bot

if [ "$(id -u)" -ne 0 ]; then
    echo "× 需要 root：sudo $0" >&2
    exit 1
fi
if [ ! -x "$ROOT/.venv/bin/python" ]; then
    echo "× 还没初始化，先跑 ./deploy/setup.sh" >&2
    exit 1
fi
if [ ! -f "$ROOT/.env" ]; then
    echo "× 缺 .env，先填好 AppID / AppSecret" >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    echo "× 这台机器没有 systemd，用 nohup 或 supervisor 代替：见 README" >&2
    exit 1
fi

RUN_USER="${SUDO_USER:-root}"
echo "==> 生成 /etc/systemd/system/$SVC.service（User=$RUN_USER）"
sed -e "s|__ROOT__|$ROOT|g" -e "s|__USER__|$RUN_USER|g" \
    deploy/qq-calendar-bot.service > "/etc/systemd/system/$SVC.service"

systemctl daemon-reload
systemctl enable --now "$SVC"

sleep 3
echo
systemctl --no-pager -l status "$SVC" || true
echo
echo "常用命令："
echo "  journalctl -u $SVC -f        看 systemd 层面的日志"
echo "  tail -f $ROOT/bot.log        看机器人自己的输出"
echo "  systemctl restart $SVC       改完代码后重启"
echo "  systemctl disable --now $SVC 停用并取消开机自启"
