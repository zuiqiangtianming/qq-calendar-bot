# -*- coding: utf-8 -*-
"""
QQ 群「日历管家」机器人
──────────────────────────────────────────────
纯规则实现，不接大模型。群里 @机器人 发 /指令，或用消息底部的按钮。

指令：
    /日历                            出图：所有有安排的月份，各一张手机竖屏图
    /日历 [本周|下周|全部|7|10.14]   出表格（筛选场景表格更好读）
    /加 10.14 17:20 面试 张三         新增一场（最后一段当人名，可省略）
    /批量 10.14 10:00 面试 张三; ...  一次加多场，分号或换行分隔
    /删 10.14 15:20                  按日期 + 时间删除
    /帮助

同一场（同群 + 同时间 + 同事项 + 同参与人）只会存一条，重复添加会被跳过。
@机器人 但没带指令时，自动回一份帮助。

依赖：pip install qq-botpy pillow
配置：同目录 .env 里填 QQ_BOT_APPID / QQ_BOT_SECRET
运行：python bot.py
"""

import asyncio
import base64
import json
import os
import re
import sqlite3
from collections import OrderedDict
from datetime import date, datetime, timedelta

import botpy
from botpy.connection import ConnectionState
from botpy.http import Route
from botpy.message import GroupMessage

import render

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(BASE_DIR, "out")


def load_env():
    """从同目录的 .env 读凭证，省得每次 export。已存在的环境变量优先。"""
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())


load_env()

# ─────────────── 配置 ───────────────
APPID = os.getenv("QQ_BOT_APPID", "")
SECRET = os.getenv("QQ_BOT_SECRET", "")
DB_PATH = os.path.join(BASE_DIR, "schedule.db")

DEFAULT_DAYS = 14        # /日历 默认往后看多少天
MAX_ROWS = 40            # 表格最多渲染多少天，防止消息过长被截断

WEEK_CN = "一二三四五六日"

HELP = """📖 **指令示例**

`/日历`

`/加 10.14 17:20 面试 张三`

`/批量 10.14 10:00 面试 张三; 10.14 15:00 笔试 李四`

`/删 10.14 15:20`"""


# ─────────────── 存储 ───────────────
# 「同一场」的判定：同群 + 同时间 + 同事项 + 同参与人（大小写、首尾空格忽略）。
# 用表达式唯一索引把规则落到库里，比只在代码里判断更保险。
UNIQUE_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_events_unique ON events("
    "group_id, start_at, lower(trim(title)), lower(trim(person)))"
)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS events (
               id         INTEGER PRIMARY KEY AUTOINCREMENT,
               group_id   TEXT NOT NULL,
               start_at   TEXT NOT NULL,          -- YYYY-MM-DD HH:MM
               title      TEXT NOT NULL,
               person     TEXT NOT NULL DEFAULT '',
               creator    TEXT NOT NULL DEFAULT '',
               created_at TEXT NOT NULL
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_group_time ON events(group_id, start_at)")
    try:
        conn.execute(UNIQUE_INDEX)
    except sqlite3.Error:
        # 库里已经有历史重复数据，索引建不起来 —— 先清一遍再去重
        cur = conn.execute(
            "DELETE FROM events WHERE id NOT IN ("
            "  SELECT MIN(id) FROM events"
            "  GROUP BY group_id, start_at, lower(trim(title)), lower(trim(person)))"
        )
        if cur.rowcount:
            print(f"[migrate] 已清掉历史重复日程 {cur.rowcount} 条")
        conn.commit()
        conn.execute(UNIQUE_INDEX)
    conn.commit()
    return conn


# ─────────────── 解析 ───────────────
DATE_RE = re.compile(r"^(\d{1,2})[.\-/月](\d{1,2})日?$")
TIME_RE = re.compile(r"^(\d{1,2})[:：](\d{1,2})$")
MENTION_RE = re.compile(r"<@!?[0-9A-Za-z]+>")     # 群里 @人 会展开成 <@openid>


def parse_date(token, today):
    """'今天' / '明天' / '10.14' → date；看不懂返回 None。"""
    token = (token or "").strip()
    if token in ("今天", "今日", "today"):
        return today.date()
    if token in ("明天", "明日", "tomorrow"):
        return (today + timedelta(days=1)).date()
    if token in ("后天",):
        return (today + timedelta(days=2)).date()
    m = DATE_RE.match(token)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    try:
        d = today.date().replace(month=month, day=day)
    except ValueError:
        return None
    if (d - today.date()).days < -180:      # 填的是明年
        try:
            d = d.replace(year=d.year + 1)
        except ValueError:
            return None
    return d


def mmdd(d):
    return f"{d.month:02d}-{d.day:02d}"


def week_cn(d):
    return f"周{WEEK_CN[d.weekday()]}"


# ─────────────── 查询日历 ───────────────
def build_calendar(group_id, arg, today):
    arg = (arg or "").strip()
    start = today.date()
    end = None

    if arg in ("本周", "这周", "周"):
        end = start + timedelta(days=6 - start.weekday())   # 到今天所在的周日
        label = "本周"
    elif arg in ("下周",):
        start = start + timedelta(days=7 - start.weekday())
        end = start + timedelta(days=6)
        label = "下周"
    elif arg in ("全部", "all", "所有"):
        label = "全部"
    elif arg.isdigit():
        days = max(1, min(int(arg), 180))
        end = start + timedelta(days=days - 1)
        label = f"未来 {days} 天"
    elif arg:
        d = parse_date(arg, today)
        if d is None:
            return "没看懂这个范围～ 试试：`/日历 本周`、`/日历 全部`、`/日历 7`、`/日历 10.14`"
        start, end = d, d + timedelta(days=6)
        label = f"{mmdd(d)} 起一周"
    else:
        end = start + timedelta(days=DEFAULT_DAYS - 1)
        label = "未来两周"

    conn = db()
    if end is None:
        rows = conn.execute(
            "SELECT id, start_at, title, person FROM events "
            "WHERE group_id = ? AND date(start_at) >= ? ORDER BY start_at",
            (group_id, start.isoformat()),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, start_at, title, person FROM events "
            "WHERE group_id = ? AND date(start_at) BETWEEN ? AND ? ORDER BY start_at",
            (group_id, start.isoformat(), end.isoformat()),
        ).fetchall()
    conn.close()

    if not rows:
        return f"🗓 **{label}** 没有安排，挺清净的。"

    by_day = OrderedDict()
    for eid, start_at, title, person in rows:
        by_day.setdefault(start_at[:10], []).append((start_at[11:16], title, person))

    if len(by_day) > MAX_ROWS:
        by_day = OrderedDict(list(by_day.items())[:MAX_ROWS])
        truncated = True
    else:
        truncated = False

    total = sum(len(v) for v in by_day.values())
    first, last = next(iter(by_day)), list(by_day)[-1]
    span = mmdd(date.fromisoformat(first))
    if last != first:
        span += f" ~ {mmdd(date.fromisoformat(last))}"

    lines = [
        f"🗓 **日历** · {label} · {span} · 共 {total} 场",
        "",
        "| 日期 | 星期 | 场次 | 安排 |",
        "|---|---|---|---|",
    ]
    for day, items in by_day.items():
        d = date.fromisoformat(day)
        cells = " ｜ ".join(
            f"{t} {title}" + (f" {person}" if person else "") for t, title, person in items
        )
        lines.append(f"| {mmdd(d)} | {week_cn(d)} | {len(items)} | {cells} |")

    if truncated:
        lines.append("")
        lines.append(f"_内容较多，只显示前 {MAX_ROWS} 天，用 `/日历 本周` 收窄范围_")

    return "\n".join(lines)


# ─────────────── 增 / 删 ───────────────
def parse_add(arg, today):
    """解析一条新增内容，返回 (start_at, title, person, err)；成功时 err 为 None。

    抽出来是为了让 /加 和 /批量 共用同一套规则，避免两边解析不一致。
    """
    # 消息里带的 @ 会展开成 <@openid>，对日历来说是噪声，先摘掉
    tokens = MENTION_RE.sub(" ", arg or "").split()
    if len(tokens) < 3:
        return None, None, None, (
            "格式：`/加 10.14 17:20 面试 张三`\n"
            "日期和时间在最前面，最后一段会当成人名（可以不写）。"
        )

    date_tok, time_tok, rest = tokens[0], tokens[1], tokens[2:]

    d = parse_date(date_tok, today)
    if d is None:
        return None, None, None, f"日期「{date_tok}」没看懂，用 `10.14` 或 `明天` 试试。"
    m = TIME_RE.match(time_tok)
    if not m:
        return None, None, None, f"时间「{time_tok}」没看懂，用 `17:20` 这样写。"
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh < 24 and 0 <= mm < 60):
        return None, None, None, "时间超出范围了。"

    if len(rest) >= 2:
        title, person = " ".join(rest[:-1]), rest[-1]
    else:
        title, person = rest[0], ""

    return f"{d.isoformat()} {hh:02d}:{mm:02d}", title, person, None


def find_dup(conn, group_id, start_at, title, person):
    """找同群、同时间、同事项、同参与人的那条（忽略大小写和首尾空格）。"""
    return conn.execute(
        "SELECT id FROM events WHERE group_id = ? AND start_at = ? "
        "AND lower(trim(title)) = ? AND lower(trim(person)) = ?",
        (group_id, start_at, title.strip().lower(), person.strip().lower()),
    ).fetchone()


def insert_event(group_id, creator, start_at, title, person):
    """入库。

    返回 (新编号, None)；若这场已经存在则返回 (None, 已有编号)，不重复插入。
    """
    title, person = (title or "").strip(), (person or "").strip()
    conn = db()
    row = find_dup(conn, group_id, start_at, title, person)
    if row:
        conn.close()
        return None, row[0]
    try:
        cur = conn.execute(
            "INSERT INTO events(group_id, start_at, title, person, creator, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (group_id, start_at, title, person, creator,
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        eid = cur.lastrowid
    except sqlite3.IntegrityError:
        # 撞在唯一索引上：说明刚刚被别的消息抢先插进去了
        row = find_dup(conn, group_id, start_at, title, person)
        eid, dup = None, (row[0] if row else None)
        conn.close()
        return eid, dup
    conn.close()
    return eid, None


def describe(start_at, title, person):
    """把一条日程写成一行，给回复和批量列表共用。"""
    d = date.fromisoformat(start_at[:10])
    return f"{mmdd(d)} {week_cn(d)} {start_at[11:]} {title}" + (f" · {person}" if person else "")


def do_add(group_id, creator, arg, today):
    start_at, title, person, err = parse_add(arg, today)
    if err:
        return err

    eid, dup = insert_event(group_id, creator, start_at, title, person)
    d = date.fromisoformat(start_at[:10])
    body = f"> **{mmdd(d)} {week_cn(d)} {start_at[11:]}**\n> {title}" + (f" · {person}" if person else "")

    if eid is None:                     # 已在日历里，别再记一遍
        return f"ℹ️ 这场已经在日历里了（编号 `{dup}`），没有重复加。\n\n{body}"
    return f"✅ 已记入日历（编号 `{eid}`）\n\n{body}"


BATCH_MAX = 20        # 一次批量最多几条，太多了消息会很长


def do_batch_add(group_id, creator, arg, today):
    """一次加多场。条目之间用分号或换行分隔。"""
    raw = (arg or "").strip()
    if not raw:
        return (
            "一次加多场，条目之间用 `;` 分隔：\n"
            "`/批量 10.14 10:00 面试 张三; 10.14 15:00 笔试 李四`\n"
            "换行分隔也行。"
        )

    items = [s.strip() for s in re.split(r"[;\n；]", raw) if s.strip()]
    if len(items) > BATCH_MAX:
        return f"一次最多 {BATCH_MAX} 条，你给了 {len(items)} 条，拆开分批发吧。"

    added, failed, dups = [], [], []
    for it in items:
        start_at, title, person, err = parse_add(it, today)
        if err:
            failed.append((it, err.replace("\n", " ")))
            continue
        eid, dup = insert_event(group_id, creator, start_at, title, person)
        if eid is None:                     # 重复的跳过，但要说清楚是哪些
            dups.append((start_at, title, person))
            continue
        added.append((start_at, title, person))

    lines = []
    if added:
        lines.append(f"✅ 批量添加完成：新增 {len(added)} 条")
        lines.append("")
        for start_at, title, person in added:
            lines.append(f"· {describe(start_at, title, person)}")
    if dups:
        if lines:
            lines.append("")
        lines.append(f"ℹ️ 跳过 {len(dups)} 条已存在的（没重复加）：")
        for start_at, title, person in dups:
            lines.append(f"· {describe(start_at, title, person)}")
    if failed:
        if lines:
            lines.append("")
        lines.append(f"⚠️ 有 {len(failed)} 条没加进去：")
        for it, err in failed:
            lines.append(f"· `{it}` → {err}")

    return "\n".join(lines)


def do_remove(group_id, arg):
    """支持 /删 10.14 15:20（推荐），也兼容 /删 <编号>。"""
    arg = (arg or "").strip()
    if not arg:
        return "用法：`/删 10.14 15:20`（日期 + 时间）"

    tokens = arg.split()

    # 形式一：日期 + 时间定位
    if len(tokens) >= 2:
        d = parse_date(tokens[0], datetime.now())
        m = TIME_RE.match(tokens[1])
        if d is None or not m:
            return "没看懂～ 用 `/删 10.14 15:20` 这种写法。"
        hh, mm = int(m.group(1)), int(m.group(2))
        target = f"{d.isoformat()} {hh:02d}:{mm:02d}"
        conn = db()
        rows = conn.execute(
            "SELECT id, title, person FROM events WHERE group_id = ? AND start_at = ?",
            (group_id, target),
        ).fetchall()
        if not rows:
            conn.close()
            return f"{mmdd(d)} {hh:02d}:{mm:02d} 没有找到安排。"
        conn.execute(
            "DELETE FROM events WHERE group_id = ? AND start_at = ?", (group_id, target)
        )
        conn.commit()
        conn.close()
        names = "、".join(t + (f"（{p}）" if p else "") for _, t, p in rows)
        return f"🗑 已删除 {mmdd(d)} {hh:02d}:{mm:02d}：{names}"

    # 形式二：按编号（编号只在 /日历 数据里，一般用日期时间更方便）
    if arg.isdigit():
        eid = int(arg)
        conn = db()
        row = conn.execute(
            "SELECT title FROM events WHERE id = ? AND group_id = ?", (eid, group_id)
        ).fetchone()
        if not row:
            conn.close()
            return f"没找到编号 `{eid}` 的安排。"
        conn.execute("DELETE FROM events WHERE id = ? AND group_id = ?", (eid, group_id))
        conn.commit()
        conn.close()
        return f"🗑 已删除（编号 `{eid}`）：{row[0]}"

    return "用法：`/删 10.14 15:20`（日期 + 时间）"


# ─────────────── 意图分发 ───────────────
def dispatch(group_id, creator, raw):
    today = datetime.now()
    if not raw:
        return HELP
    body = raw[1:] if raw.startswith("/") else raw
    parts = body.split(" ", 1)
    cmd = parts[0].strip()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("日历", "行程", "日程", "calendar"):
        return build_calendar(group_id, arg, today)
    if cmd in ("加", "新增", "安排", "add"):
        return do_add(group_id, creator, arg, today)
    if cmd in ("批量", "批量加", "batch", "multi"):
        return do_batch_add(group_id, creator, arg, today)
    if cmd in ("删", "删除", "del"):
        return do_remove(group_id, arg)
    if cmd in ("帮助", "help", "?", "菜单"):
        return HELP
    return "没听懂～ 发 `/帮助` 看看我会啥。"


# ─────────────── 按钮 ───────────────
# action.type = 2 表示「点击后把 data 自动回显到输入框并 @机器人」，
# 用户再敲一次回车即可发送 —— 复用同一套指令解析，不需要处理互动回调。
def build_keyboard():
    def btn(bid, label, data):
        return {
            "id": bid,
            "render_data": {"label": label, "visited_label": label, "style": 1},
            "action": {"type": 2, "permission": {"type": 2}, "data": data, "enter": True},
        }

    return {
        "content": {
            "rows": [
                {"buttons": [
                    btn("b_all", "日历", "/日历"),
                    btn("b_add", "加一场", "/加"),
                ]},
                {"buttons": [
                    btn("b_batch", "批量增加", "/批量"),
                    btn("b_del", "删一场", "/删"),
                ]},
            ]
        }
    }


# ─────────────── 图片日历 ───────────────
CAL_CMDS = ("日历", "行程", "日程", "calendar")


def load_events(group_id, since=None):
    """取该群未过期的日程，喂给渲染器。

    和表格版保持一致：只画今天及以后的，已经过去的安排不再占版面。
    """
    since = (since or date.today()).isoformat()
    conn = db()
    rows = conn.execute(
        "SELECT start_at, title, person FROM events "
        "WHERE group_id = ? AND date(start_at) >= ? ORDER BY start_at",
        (group_id, since),
    ).fetchall()
    conn.close()
    return [{"start_at": r[0], "title": r[1], "person": r[2]} for r in rows]


IMG_ALIASES = ("图片", "图", "image")   # 历史写法，等同裸 /日历，留着兼容旧消息里的按钮
MAX_IMG_MONTHS = 3        # 一张图一个月，最多画几个月，再多就刷屏了


def is_image_query(raw):
    """判断这条指令要不要出图。

    不带参数的 `/日历` 出图；`/日历 图片` 是同义写法（早期按钮里带的就是它）。
    带范围参数的 /日历（本周/全部/7/10.14）走表格 —— 筛选场景表格更合适。
    """
    if not raw.startswith("/"):
        return False
    parts = raw[1:].split(" ", 1)
    if parts[0].strip() not in CAL_CMDS:
        return False
    return len(parts) == 1 or parts[1].strip() in IMG_ALIASES


async def upload_group_image(api, group_openid, png_path):
    """把本地 PNG 上传到群聊富媒体，返回 file_info。

    官方有两种上传姿势：URL 上传（要求资源已在公网）和二进制直传（5M 内）。
    我们的日历图只有几十 KB，走二进制直传最省事。
    SDK 的 post_group_file 只接受 url 参数，所以这里复用 Route + _http 自己发请求，
    鉴权和 access_token 刷新仍然由 botpy 负责。

    关键：srv_send_msg 必须为 False。设 True 虽然能一步到位，
    但会占用「主动消息」频次（每群每月仅 4 条），而被动回复不受此限制。
    """
    with open(png_path, "rb") as fh:
        data = fh.read()
    if len(data) > 5 * 1024 * 1024:
        raise ValueError("图片超过 5M，二进制直传不支持，需改走分片上传")

    payload = {
        "file_type": 1,                                   # 1 = 图片
        "file_name": os.path.basename(png_path),
        "file_data": base64.b64encode(data).decode("ascii"),
        "srv_send_msg": False,
    }
    route = Route("POST", "/v2/groups/{group_openid}/files", group_openid=group_openid)
    resp = await api._http.request(route, json=payload)

    if not isinstance(resp, dict):
        try:
            resp = json.loads(resp)
        except Exception:
            raise RuntimeError(f"上传返回无法解析：{resp!r}")
    info = resp.get("file_info")
    if not info:
        raise RuntimeError(f"上传未返回 file_info：{resp}")
    return info


# ─────────────── 兼容补丁 ───────────────
# botpy 1.2.1 的事件表里只有 group_at_message_create，
# 而平台实际推的群消息事件名是 group_message_create（日志里的 [ERROR] unknown event），
# 键对不上就直接被丢掉。SDK 内部是靠 inspect.getmembers(self) 扫描 parse_* 方法自动建表的，
# 所以在实例化之前把方法挂到类上，它就会自己注册进去。
def _parse_group_message_create(self, payload):
    _message = GroupMessage(self.api, payload.get("id", None), payload.get("d", {}))
    self._dispatch("group_message_create", _message)


ConnectionState.parse_group_message_create = _parse_group_message_create


# ─────────────── 客户端 ───────────────
class CalendarBot(botpy.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._seen = set()

    async def on_ready(self):
        print(f"[ready] {self.robot.name} 已上线")

    async def on_group_at_message_create(self, message: GroupMessage):
        await self.handle_group_message(message)

    async def on_group_message_create(self, message: GroupMessage):
        await self.handle_group_message(message)

    async def handle_group_message(self, message: GroupMessage):
        if message.id in self._seen:            # 同一条消息可能从两个事件名进来，去重
            return
        self._seen.add(message.id)
        if len(self._seen) > 1000:
            self._seen.clear()

        raw = (message.content or "").strip()
        print(f"[in] group={message.group_openid} content={raw!r}")

        idx = raw.find("/")                     # 砍掉 "@机器人" 之类的前缀
        if idx < 0:
            # 机器人默认只收得到 @它 的消息，所以「收到了但没有指令」就等于
            # 「@了它但没说要干什么」—— 这时候把用法摆出来最合适。
            print("[cmd] 无指令 → 回帮助")
            await self.safe_reply(message, HELP)
            return
        raw = raw[idx:]
        print(f"[cmd] {raw!r}")

        # /日历 优先出图；图出不来（没数据 / 上传被拒）就退回文字表格
        if is_image_query(raw) and await self.send_calendar_image(message):
            return

        creator = getattr(message.author, "member_openid", "") or ""
        text = dispatch(message.group_openid, creator, raw)
        await self.safe_reply(message, text)

    async def send_calendar_image(self, message):
        """把所有有安排的月份各画一张发出去。返回 True 表示已发出，不必再走文字流程。"""
        gid = message.group_openid
        events = load_events(gid)
        if not events:
            return False                        # 一条数据都没有，交给文字流程回「没有安排」

        today = date.today()
        have = sorted({(int(e["start_at"][:4]), int(e["start_at"][5:7]))
                       for e in events if len(e["start_at"]) >= 7})
        months = have[:MAX_IMG_MONTHS]

        sent = 0
        seq = 1                                 # 每次 reply 都要一个新 seq，见 send_one_image
        for year, month in months:
            ok, seq = await self.send_one_image(message, events, year, month, today, seq)
            if not ok:
                break
            sent += 1

        if sent == 0:
            return False
        if len(have) > sent:
            await self.reply_text(
                message, f"还有 {len(have) - sent} 个月没画，用 `/日历 10.14` 看具体月份。", seq=seq)
        return True

    async def send_one_image(self, message, events, year, month, today, seq):
        """渲染 + 上传 + 发送单个月份的日历图。

        返回 (是否成功, 下一个可用 seq)。seq 必须每次都换新的：
        这些回复共用同一个 msg_id，服务端把「相同 msg_id + msg_seq」判为重复发送，
        所以失败重试也不能复用刚才那个号。
        """
        png = os.path.join(IMG_DIR, f"cal_{message.group_openid[-8:]}_{year}{month:02d}.png")
        try:
            render.render_calendar_png(events, year, month, today, png)
        except Exception as exc:
            print(f"[warn] 日历渲染失败：{exc}")
            return False, seq

        try:
            file_info = await upload_group_image(self.api, message.group_openid, png)
        except Exception as exc:
            print(f"[warn] 图片上传失败：{exc}")
            return False, seq

        # 图 + 按钮最理想，但 media 和 keyboard 能否共存看服务端脸色，所以留一级降级
        for extra, tag in (({"keyboard": build_keyboard()}, "图片 + 按钮"), ({}, "图片")):
            try:
                await message.reply(msg_type=7, media={"file_info": file_info},
                                    msg_seq=seq, **extra)
                print(f"[out] {tag} seq={seq}")
                return True, seq + 1
            except Exception as exc:
                print(f"[warn] {tag} 发送失败：{exc}")
                seq += 1
        return False, seq

    async def reply_text(self, message, text, seq=1):
        try:
            await message.reply(msg_type=0, content=text, msg_seq=seq)
        except Exception as exc:
            print(f"[warn] 附加文字发送失败：{exc}")

    async def safe_reply(self, message, text):
        """先按 markdown 富文本发（表格能渲染），不行就逐级降级，保证消息一定发得出去。"""
        kb = build_keyboard()
        seq = 1
        for extra, tag in (
            ({"msg_type": 2, "markdown": {"content": text}, "keyboard": kb}, "markdown + 按钮"),
            ({"msg_type": 0, "content": text, "keyboard": kb}, "纯文本 + 按钮"),
            ({"msg_type": 0, "content": text}, "纯文本"),
        ):
            try:
                await message.reply(msg_seq=seq, **extra)
                print(f"[out] {tag}")
                return
            except Exception as exc:
                print(f"[warn] {tag} 发送失败：{exc}")
                seq += 1                              # 换号重试，同号会被判重复


if __name__ == "__main__":
    if not APPID or not SECRET:
        raise SystemExit("请先在同目录 .env 里配置 QQ_BOT_APPID / QQ_BOT_SECRET")
    print(render.font_report())                            # 服务器上最容易踩的坑，先报到日志里
    asyncio.set_event_loop(asyncio.new_event_loop())    # Python 3.10+ 需要显式建 loop
    intents = botpy.Intents(public_messages=True)       # 群 / 单聊消息
    CalendarBot(intents=intents).run(appid=APPID, secret=SECRET)
