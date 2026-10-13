# -*- coding: utf-8 -*-
"""把日程渲染成一张手机竖屏友好的 PNG。

版式：竖向列表 —— 一天一块，块里是一条条扁平的日程条。
画布按 SCALE 倍像素绘制（2 倍：逻辑宽 560 → 实际 1120）。

为什么是 2 倍：手机聊天窗口会把图片按屏宽等比缩放，主流手机物理宽 1080px，
560 宽的图被放大近 2 倍，字就发虚；1120 宽正好 1:1 落上去，笔画才不糊。
注意 PNG 没有「DPI」这回事 —— DPI 只影响打印尺寸，对屏幕显示毫无作用，
屏幕上的清晰度只由像素尺寸决定，所以这里放大的是画布像素，不是 DPI 元数据。
改倍数用环境变量 QQ_CAL_SCALE（默认 2），1 就是回到老尺寸。

纯 Pillow 实现，不依赖浏览器 / 无头 Chromium，一张几十毫秒。
配色取自辰少给的参考图（见 README「图片日历」一节）。
"""
import os
from datetime import date, datetime

from PIL import Image, ImageDraw, ImageFont

# ─────────────── 字体 ───────────────
# 目标只有一条：找到一套带中文字形的字体。各发行版装的字体不一样，
# 所以按候选表逐个探测（Windows / Debian / RHEL / Alpine / macOS 都覆盖），
# 也可以用环境变量 QQ_CAL_FONT / QQ_CAL_FONT_BOLD 直接指定。
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",                                # Windows 微软雅黑
    r"C:\Windows\Fonts\msyh.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",    # Debian / Ubuntu
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",  # Fedora / RHEL
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",         # Arch / Alpine
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-VF.otf.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",            # 文泉驿微米黑
    "/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/System/Library/Fonts/PingFang.ttc",                        # macOS
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",           # 兜底：无中文，会显示成方框
]

_FONT_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyhbd.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]

FONT_HINT = """没找到可用的中文字体，日历图里的汉字会变成方框。装一套即可：
  Debian / Ubuntu : sudo apt-get install -y fonts-noto-cjk
  CentOS / RHEL   : sudo yum install -y wqy-zenhei-fonts
  Alpine          : sudo apk add font-noto-cjk
也可以手动指定：export QQ_CAL_FONT=/path/to/字体.ttc"""


def _first_existing(candidates):
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


def _sc_index(path):
    """Noto CJK 这类 .ttc 是「字体集合」，JP/KR/SC/TC 都在里面。

    直接开 index=0 常常拿到日文字形（部分汉字写法不同），所以按家族名挑出简体那张。
    非集合字体、或认不出来时退回第 0 张。
    """
    if not path.lower().endswith((".ttc", ".otc")):
        return 0
    for i in range(8):
        try:
            name = " ".join(ImageFont.truetype(path, 16, index=i).getname())
        except Exception:
            break
        if "sc" in name.lower():
            return i
    return 0


FONT_REG = _first_existing([os.getenv("QQ_CAL_FONT", "")] + _FONT_CANDIDATES)
FONT_BOLD = (_first_existing([os.getenv("QQ_CAL_FONT_BOLD", "")] + _FONT_BOLD_CANDIDATES)
             or FONT_REG)
REG_INDEX = _sc_index(FONT_REG)
BOLD_INDEX = REG_INDEX if FONT_BOLD == FONT_REG else _sc_index(FONT_BOLD)


def font_report():
    """启动时打印一行，方便确认服务器上的字体认没认出来。"""
    if not FONT_REG:
        return "[font] ⚠️ " + FONT_HINT.replace("\n", "\n        ")
    bold = "（与正文同一套）" if FONT_BOLD == FONT_REG else f"{FONT_BOLD}"
    return f"[font] 正文 {FONT_REG} [face {REG_INDEX}]\n       粗体 {bold}"

# ─────────────── 配色 ───────────────
# 每人一组：主色（左侧竖条 / 时间字）/ 浅底（条背景）/ 深字（条内正文）
PALETTE = [
    ("#EF9F27", "#FAEEDA", "#854F0B"),   # 橙
    ("#378ADD", "#E6F1FB", "#185FA5"),   # 蓝
    ("#63A93E", "#EAF3DE", "#3B6D11"),   # 绿
    ("#8B5CF6", "#F0EAFB", "#5B3FA8"),   # 紫
    ("#E24B4A", "#FCEBEB", "#A32E2E"),   # 红
    ("#12A5A5", "#E4F5F5", "#0B6E6E"),   # 青
]
NEUTRAL = ("#B0B0B0", "#F1F1F1", "#6E6E6E")   # 没人名时用灰

INK = "#262626"        # 标题 / 日期
SUB = "#6E6E6E"        # 副标题 / 图例
MUTED = "#969696"      # 场次 / 提示
LINE = "#E1E1E1"       # 分隔线
TODAY_LINE = "#378ADD"  # 今天
BG = "#FFFFFF"
DAY_BG = "#F5F6F7"     # 日期小标题条底色
TODAY_BG = "#E6F1FB"   # 今天的日期条底色

# ─────────────── 版式 ───────────────
# 下面这些「逻辑尺寸」都按 560px 宽的版式写，实际画布统一乘 SCALE。
# 好处：改清晰度只动一个数，版式比例永远不变。
SCALE = max(1.0, float(os.getenv("QQ_CAL_SCALE", "2")))


def S(v):
    """逻辑尺寸 → 画布像素。"""
    return int(round(v * SCALE))


W = S(560)       # 实际画布宽（默认 1120，贴 1080p 手机 1:1）
PAD = S(20)
BAR_H = S(34)    # 单条日程的高度（扁平条）
BAR_GAP = S(5)
DAY_H = S(28)    # 日期小标题条高度
DAY_GAP = S(14)  # 两块日期之间的间隔
TIP_H = S(20)
MAX_PER_DAY = 6  # 单天最多画几条，再多折成「还有 N 场」
MAX_BARS = 26    # 一张图最多几条
MAX_H = S(1300)  # 画布高度上限：再长就会被手机缩成一条看不清的窄带

WEEK_CN = "一二三四五六日"

_font_cache = {}


def _font(path, index, size):
    key = (path, index, size)
    if key not in _font_cache:
        if not path:
            raise RuntimeError(FONT_HINT)
        _font_cache[key] = ImageFont.truetype(path, size, index=index)
    return _font_cache[key]


def _reg(size):
    return _font(FONT_REG, REG_INDEX, size)


def _bold(size):
    return _font(FONT_BOLD, BOLD_INDEX, size)


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def color_map(persons):
    """把出现的人名按字典序稳定映射到调色板 —— 人不变，颜色就不变。"""
    uniq = sorted({p for p in persons if p})
    return {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(uniq)}


def _fit(draw, text, font, max_w):
    """文字超宽就截断加省略号。"""
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…" if text else ""


def _vcenter(draw, text, font, box_y, box_h):
    """返回让文字在 box 内垂直居中所需的 y。"""
    bbox = draw.textbbox((0, 0), text or "汉", font=font)
    return box_y + (box_h - (bbox[3] - bbox[1])) / 2 - bbox[1]


def render_calendar_png(events, year, month, today, out_path,
                        max_per_day=MAX_PER_DAY, max_bars=MAX_BARS):
    """events: [{'start_at': 'YYYY-MM-DD HH:MM', 'title': str, 'person': str}, ...]

    只画 year/month 这个月的部分。返回 out_path。
    """
    # 按天归拢本月日程
    by_day = {}
    for e in events:
        try:
            dt = datetime.strptime(e["start_at"], "%Y-%m-%d %H:%M")
        except (ValueError, KeyError):
            continue
        if dt.year == year and dt.month == month:
            by_day.setdefault(dt.day, []).append((dt, e))

    colors = color_map([e.get("person", "") for e in events])
    days = sorted(by_day)

    title_h, sub_h, legend_h = S(36), S(22), S(26)
    head_h = PAD + title_h + sub_h + legend_h + S(20)
    budget = MAX_H - head_h - S(12)     # 正文可用高度

    # ── 先排好每块要画什么、多高，才能定画布总高 ──
    blocks = []          # (day, 当天总场次, 要画的条目, 折叠掉几条, 块高)
    used = 0             # 已画的条数
    body_h = 0
    omitted = 0
    for day in days:
        items = sorted(by_day[day], key=lambda t: t[0])
        if used >= max_bars:
            omitted += len(items)
            continue
        allowed = min(len(items), max_per_day, max_bars - used)
        shown = items[:allowed]
        hidden = len(items) - len(shown)
        h = DAY_H + S(4) + len(shown) * BAR_H + (len(shown) - 1) * BAR_GAP
        if hidden:
            h += S(4) + TIP_H
        need = h + (DAY_GAP if blocks else 0)
        if blocks and body_h + need > budget:      # 第一块无论如何都画，避免出现空图
            omitted += len(items)
            continue
        blocks.append((day, len(items), shown, hidden, h))
        body_h += need
        used += len(shown)

    foot_h = (TIP_H + S(18)) if omitted else S(12)
    H = head_h + (body_h + foot_h if blocks else S(44))

    im = Image.new("RGB", (W, H), _rgb(BG))
    d = ImageDraw.Draw(im)

    f_title = _bold(S(26))
    f_sub = _reg(S(15))
    f_legend = _reg(S(15))
    f_day = _bold(S(17))
    f_cnt = _reg(S(14))
    f_bar = _reg(S(17))
    f_time = _bold(S(17))
    f_tip = _reg(S(14))

    x0, x1 = PAD, W - PAD
    y = PAD

    # ── 标题 + 右上角场次 ──
    d.text((x0, y), f"{month} 月 · 面试日历", font=f_title, fill=_rgb(INK))
    if blocks:
        total = sum(b[1] for b in blocks) + omitted
        cnt_txt = f"共 {total} 场"
        d.text((x1 - d.textlength(cnt_txt, font=f_cnt), y + S(13)), cnt_txt,
               font=f_cnt, fill=_rgb(MUTED))
    y += title_h

    # ── 副标题：日期跨度 ──
    if days:
        span = f"{month:02d}-{days[0]:02d} ~ {month:02d}-{days[-1]:02d}"
    else:
        span = f"{year} 年 {month} 月"
    d.text((x0, y), span, font=f_sub, fill=_rgb(SUB))
    y += sub_h

    # ── 人名图例（颜色 → 人）──
    lx = x0
    for name, (main, _bg, _tx) in colors.items():
        d.ellipse([lx, y + S(5), lx + S(10), y + S(15)], fill=_rgb(main))
        d.text((lx + S(15), y), name, font=f_legend, fill=_rgb(SUB))
        lx += S(15) + d.textlength(name, font=f_legend) + S(18)

    # ── 分隔线 ──
    d.line([x0, head_h - S(14), x1, head_h - S(14)], fill=_rgb(LINE), width=S(1))

    if not blocks:
        d.text((x0, head_h), "本月暂时没有安排。", font=f_tip, fill=_rgb(MUTED))

    # ── 正文：一天一块 ──
    y = head_h
    for bi, (day, n_total, shown, hidden, _h) in enumerate(blocks):
        d_day = date(year, month, day)
        is_today = (d_day == today)

        # 日期小标题条
        d.rounded_rectangle([x0, y, x1, y + DAY_H], radius=S(6),
                            fill=_rgb(TODAY_BG if is_today else DAY_BG))
        dt_text = f"{month:02d}-{day:02d}"
        d.text((x0 + S(10), _vcenter(d, dt_text, f_day, y, DAY_H)), dt_text,
               font=f_day, fill=_rgb(TODAY_LINE if is_today else INK))
        wk = f"周{WEEK_CN[d_day.weekday()]}"
        d.text((x0 + S(10) + d.textlength(dt_text, font=f_day) + S(8),
                _vcenter(d, wk, f_cnt, y, DAY_H)), wk, font=f_cnt, fill=_rgb(MUTED))
        cnt = f"{n_total} 场"
        d.text((x1 - S(10) - d.textlength(cnt, font=f_cnt),
                _vcenter(d, cnt, f_cnt, y, DAY_H)), cnt, font=f_cnt, fill=_rgb(MUTED))
        y += DAY_H + S(4)

        # 扁平日程条
        for i, (dt, e) in enumerate(shown):
            main, bgc, txtc = colors.get(e.get("person", ""), NEUTRAL)
            d.rounded_rectangle([x0, y, x1, y + BAR_H], radius=S(6), fill=_rgb(bgc))
            d.rounded_rectangle([x0, y + S(4), x0 + S(4), y + BAR_H - S(4)],
                                radius=S(2), fill=_rgb(main))

            tx = x0 + S(14)
            hm = dt.strftime("%H:%M")
            d.text((tx, _vcenter(d, hm, f_time, y, BAR_H)), hm,
                   font=f_time, fill=_rgb(main))
            tx += d.textlength(hm, font=f_time) + S(10)

            label = e.get("title", "") + (f" · {e['person']}" if e.get("person") else "")
            label = _fit(d, label, f_bar, x1 - S(12) - tx)
            d.text((tx, _vcenter(d, label, f_bar, y, BAR_H)), label,
                   font=f_bar, fill=_rgb(txtc))
            if i < len(shown) - 1:
                y += BAR_H + BAR_GAP
            else:
                y += BAR_H

        if hidden:
            y += S(4)
            tip = f"还有 {hidden} 场…"
            d.text((x0 + S(14), _vcenter(d, tip, f_tip, y, TIP_H)), tip,
                   font=f_tip, fill=_rgb(MUTED))
            y += TIP_H

        if bi < len(blocks) - 1:
            y += DAY_GAP

    # ── 被砍掉的部分给个交代 ──
    if omitted:
        d.text((x0, y + S(8)), f"还有 {omitted} 场没画下，用 `/日历 全部` 看表格",
               font=f_tip, fill=_rgb(MUTED))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    im.save(out_path, "PNG", optimize=True)
    return out_path
