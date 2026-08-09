"""Vertical cards + realistic X tweet UI (avatars, premium badges, metrics)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

W, H = 1080, 1920

BG = (0, 0, 0)
FEED_BG = (0, 0, 0)
CARD_BG = (22, 24, 28)
TEXT = (231, 233, 234)
MUTED = (113, 118, 123)
ACCENT = (29, 155, 240)  # blue / premium blue
GOLD = (228, 185, 52)  # business gold
GOV = (130, 154, 171)  # government gray
LIKE_RED = (249, 24, 128)
BORDER = (47, 51, 54)
CAMP1 = (29, 155, 240)
CAMP2 = (249, 24, 128)

_AVATAR_COLORS = [
    (29, 155, 240),
    (0, 186, 124),
    (249, 24, 128),
    (255, 212, 0),
    (120, 86, 255),
    (244, 33, 46),
    (255, 122, 0),
]


def _font(size: int, *, bold: bool = False):
    candidates = []
    if bold:
        candidates += [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/SFNS.ttf",
        ]
    candidates += [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _emoji_font(size: int):
    """macOS color emoji font (fixes □ tofu boxes)."""
    for p in (
        "/System/Library/Fonts/Apple Color Emoji.ttc",
        "/System/Library/Fonts/Apple Color Emoji.ttf",
    ):
        try:
            return ImageFont.truetype(p, size=size)
        except OSError:
            continue
    return None


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """Simple word wrap for non-emoji UI text."""
    words = (text or "").replace("\n", " ").split()
    if not words:
        return [""]
    lines, cur = [], words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        try:
            width = draw.textlength(trial, font=font)
        except Exception:
            width = len(trial) * 12
        if width <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


# Rough emoji / symbol detector (covers most tweet emoji + symbols Arial lacks)
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U0001F300-\U0001F9FF"  # misc pictographs / emoticons / supplemental
    "\U0001FA00-\U0001FAFF"  # extended-A
    "\U00002600-\U000027BF"  # misc symbols
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"  # ZWJ
    "\U0000231A-\U0000231B"
    "\U000023E9-\U000023F3"
    "\U000023F8-\U000023FA"
    "\U000025AA-\U000025FE"
    "\U00002B05-\U00002B07"
    "\U00002B1B-\U00002B1C"
    "\U00002B50"
    "\U00002B55"
    "\U00003030"
    "\U0000303D"
    "\U00003297"
    "\U00003299"
    "\U000000A9"  # ©
    "\U000000AE"  # ®
    "]+",
    flags=re.UNICODE,
)


def _is_emoji_char(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch)
    if ch in ("©", "®", "™"):
        return True
    return bool(_EMOJI_RE.fullmatch(ch)) or (
        0x1F300 <= o <= 0x1FAFF
        or 0x2600 <= o <= 0x27BF
        or 0x1F1E0 <= o <= 0x1F1FF
        or o in (0x200D, 0xFE0F, 0xFE0E)
        or 0x1F000 <= o <= 0x1F02F
    )


def _measure_mixed(text: str, text_font, emoji_font, emoji_size: int) -> float:
    w = 0.0
    for ch in text:
        if _is_emoji_char(ch) and emoji_font is not None:
            # color emoji advance is roughly size
            try:
                w += float(emoji_font.getlength(ch))
            except Exception:
                w += emoji_size
        else:
            try:
                w += float(text_font.getlength(ch))
            except Exception:
                w += text_font.size if hasattr(text_font, "size") else 20
    return w


def _wrap_mixed(text: str, text_font, emoji_font, emoji_size: int, max_w: int) -> list[str]:
    """Wrap by characters so emoji don't break measurement."""
    text = text or ""
    if not text:
        return [""]
    lines: list[str] = []
    cur = ""
    for ch in text:
        trial = cur + ch
        if _measure_mixed(trial, text_font, emoji_font, emoji_size) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = ch if ch != " " else ""
    if cur:
        lines.append(cur)
    return lines or [""]


def _draw_mixed_text(
    img: Image.Image,
    xy: tuple[int, int],
    text: str,
    *,
    text_font,
    fill=TEXT,
    emoji_size: int | None = None,
) -> float:
    """Draw text with Apple Color Emoji for emoji runs (no □ boxes). Returns width."""
    x, y = xy
    ef_size = int(emoji_size or getattr(text_font, "size", 40) or 40)
    emoji_font = _emoji_font(ef_size)
    draw = ImageDraw.Draw(img)
    cx = float(x)
    i = 0
    chars = list(text or "")
    while i < len(chars):
        ch = chars[i]
        if _is_emoji_char(ch) and emoji_font is not None:
            seq = ch
            j = i + 1
            while j < len(chars) and (
                _is_emoji_char(chars[j])
                or ord(chars[j]) in (0x200D, 0xFE0F, 0xFE0E)
                or 0x1F3FB <= ord(chars[j]) <= 0x1F3FF
            ):
                seq += chars[j]
                j += 1
            # draw color emoji on a small transparent tile, then paste
            tile_s = int(ef_size * 1.6)
            tile = Image.new("RGBA", (tile_s, tile_s), (0, 0, 0, 0))
            try:
                ImageDraw.Draw(tile).text(
                    (0, 0), seq, font=emoji_font, embedded_color=True
                )
                try:
                    adv = float(emoji_font.getlength(seq))
                except Exception:
                    adv = float(ef_size)
                img.paste(tile, (int(cx), int(y - ef_size * 0.15)), tile)
            except Exception:
                adv = float(ef_size)
            cx += max(adv, ef_size * 0.85)
            i = j
            continue
        run = ch
        j = i + 1
        while j < len(chars) and not _is_emoji_char(chars[j]):
            run += chars[j]
            j += 1
        draw.text((int(cx), y), run, font=text_font, fill=fill)
        try:
            cx += float(text_font.getlength(run))
        except Exception:
            cx += len(run) * (ef_size * 0.5)
        i = j
    return cx - x


def _draw_x_logo_geom(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int = 36) -> None:
    """Draw a simple X logo (no special unicode)."""
    s = size // 2
    w = max(3, size // 8)
    draw.line((cx - s, cy - s, cx + s, cy + s), fill=TEXT, width=w)
    draw.line((cx + s, cy - s, cx - s, cy + s), fill=TEXT, width=w)


def _icon_reply(draw: ImageDraw.ImageDraw, cx: int, cy: int, color=MUTED, s: int = 18) -> None:
    # speech bubble
    draw.rounded_rectangle((cx - s, cy - s + 2, cx + s, cy + s - 4), radius=6, outline=color, width=3)
    draw.polygon(
        [(cx - s // 2, cy + s - 6), (cx - s + 2, cy + s + 6), (cx - 2, cy + s - 4)],
        fill=color,
    )


def _icon_repost(draw: ImageDraw.ImageDraw, cx: int, cy: int, color=MUTED, s: int = 18) -> None:
    # two bent arrows
    draw.arc((cx - s, cy - s, cx + 4, cy + s // 2), 200, 20, fill=color, width=3)
    draw.polygon([(cx + 2, cy - s), (cx + 12, cy - s + 6), (cx + 2, cy - s + 12)], fill=color)
    draw.arc((cx - 4, cy - s // 2, cx + s, cy + s), 20, 200, fill=color, width=3)
    draw.polygon([(cx - 2, cy + s), (cx - 12, cy + s - 6), (cx - 2, cy + s - 12)], fill=color)


def _icon_like(draw: ImageDraw.ImageDraw, cx: int, cy: int, color=MUTED, s: int = 18) -> None:
    # heart approximation with two circles + triangle
    draw.ellipse((cx - s, cy - s // 2 - 2, cx, cy + s // 3), outline=color, width=3)
    draw.ellipse((cx, cy - s // 2 - 2, cx + s, cy + s // 3), outline=color, width=3)
    draw.polygon(
        [(cx - s + 1, cy), (cx + s - 1, cy), (cx, cy + s)],
        outline=color,
    )
    # fill lightly
    draw.polygon([(cx - s + 3, cy + 2), (cx + s - 3, cy + 2), (cx, cy + s - 2)], fill=color)
    draw.ellipse((cx - s + 2, cy - s // 2, cx - 1, cy + s // 4), fill=color)
    draw.ellipse((cx + 1, cy - s // 2, cx + s - 2, cy + s // 4), fill=color)


def _icon_views(draw: ImageDraw.ImageDraw, cx: int, cy: int, color=MUTED, s: int = 18) -> None:
    # bar chart
    draw.rectangle((cx - s, cy + s // 3, cx - s // 3, cy + s), fill=color)
    draw.rectangle((cx - s // 4, cy - s // 4, cx + s // 4, cy + s), fill=color)
    draw.rectangle((cx + s // 3, cy - s, cx + s, cy + s), fill=color)


def _icon_share(draw: ImageDraw.ImageDraw, cx: int, cy: int, color=MUTED, s: int = 18) -> None:
    draw.line((cx, cy + s, cx, cy - s // 2), fill=color, width=3)
    draw.polygon([(cx, cy - s), (cx - 10, cy - s // 3), (cx + 10, cy - s // 3)], fill=color)
    draw.arc((cx - s, cy, cx + s, cy + s), 10, 170, fill=color, width=3)


def _icon_home(draw: ImageDraw.ImageDraw, cx: int, cy: int, color=MUTED, s: int = 20) -> None:
    draw.polygon(
        [(cx, cy - s), (cx - s, cy), (cx - s + 4, cy), (cx - s + 4, cy + s), (cx + s - 4, cy + s), (cx + s - 4, cy), (cx + s, cy)],
        outline=color,
        width=3,
    )


def _icon_search(draw: ImageDraw.ImageDraw, cx: int, cy: int, color=MUTED, s: int = 18) -> None:
    draw.ellipse((cx - s, cy - s, cx + s // 2, cy + s // 2), outline=color, width=3)
    draw.line((cx + s // 3, cy + s // 3, cx + s, cy + s), fill=color, width=3)


def _icon_bell(draw: ImageDraw.ImageDraw, cx: int, cy: int, color=MUTED, s: int = 18) -> None:
    draw.arc((cx - s, cy - s, cx + s, cy + s // 2), 0, 180, fill=color, width=3)
    draw.line((cx - s, cy, cx + s, cy), fill=color, width=3)
    draw.ellipse((cx - 4, cy + s // 2, cx + 4, cy + s // 2 + 8), fill=color)


def _icon_dm(draw: ImageDraw.ImageDraw, cx: int, cy: int, color=MUTED, s: int = 18) -> None:
    draw.rounded_rectangle((cx - s, cy - s // 2, cx + s, cy + s // 2 + 4), radius=4, outline=color, width=3)
    draw.line((cx - s + 2, cy - s // 2 + 2, cx, cy + 2), fill=color, width=2)
    draw.line((cx + s - 2, cy - s // 2 + 2, cx, cy + 2), fill=color, width=2)


def parse_tweet_line(raw: str) -> tuple[str, str, str]:
    text = (raw or "").strip()
    m = re.match(r"^@([A-Za-z0-9_]{1,30})\s*[:：\-—]\s*(.*)$", text, re.DOTALL)
    if m:
        handle = m.group(1)
        return handle, handle, (m.group(2) or "").strip()
    m2 = re.match(r"^@([A-Za-z0-9_]{1,30})\s+(.*)$", text, re.DOTALL)
    if m2:
        handle = m2.group(1)
        return handle, handle, (m2.group(2) or "").strip()
    return "User", "user", text


def _avatar_color(handle: str) -> tuple[int, int, int]:
    h = int(hashlib.md5(handle.encode()).hexdigest(), 16)
    return _AVATAR_COLORS[h % len(_AVATAR_COLORS)]


def _circle_crop(im: Image.Image, size: int) -> Image.Image:
    im = ImageOps.fit(im.convert("RGBA"), (size, size), method=Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def _paste_avatar(
    canvas: Image.Image,
    *,
    left: int,
    top: int,
    size: int,
    handle: str,
    avatar_path: str | Path | None,
) -> None:
    if avatar_path and Path(avatar_path).is_file():
        try:
            raw = Image.open(avatar_path)
            circ = _circle_crop(raw, size)
            canvas.paste(circ, (left, top), circ)
            return
        except Exception:
            pass
    # fallback monogram
    draw = ImageDraw.Draw(canvas)
    color = _avatar_color(handle)
    draw.ellipse((left, top, left + size, top + size), fill=color)
    letter = (handle[:1] or "?").upper()
    draw.text(
        (left + size // 2, top + size // 2),
        letter,
        font=_font(int(size * 0.45), bold=True),
        fill=(255, 255, 255),
        anchor="mm",
    )


def _badge_color(verified_type: str | None) -> tuple[int, int, int] | None:
    if not verified_type:
        return None
    t = verified_type.lower()
    if t in ("blue", "true", "1"):
        return ACCENT
    if t == "business":
        return GOLD
    if t == "government":
        return GOV
    return ACCENT


def _draw_verified_badge(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    *,
    verified_type: str | None,
    size: int = 28,
) -> int:
    """Draw geometric check badge (no emoji font). Returns width consumed."""
    color = _badge_color(verified_type)
    if color is None:
        return 0
    r = size // 2
    cx, cy = x + r, y + r
    draw.ellipse((x, y, x + size, y + size), fill=color)
    # white checkmark path
    draw.line(
        [(cx - r * 0.45, cy + r * 0.05), (cx - r * 0.1, cy + r * 0.4), (cx + r * 0.5, cy - r * 0.35)],
        fill=(255, 255, 255),
        width=max(2, size // 6),
    )
    return size + 8


def _fmt_metric(n: int | None) -> str:
    if n is None:
        return "0"
    n = int(n)
    if n >= 1_000_000:
        s = f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{s}M"
    if n >= 10_000:
        s = f"{n / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{s}K"
    if n >= 1_000:
        s = f"{n / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{s}K"
    return f"{n:,}"


def _relative_time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        from datetime import datetime, timezone

        text = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        sec = max(0, int((now - dt).total_seconds()))
        if sec < 3600:
            return f"{max(1, sec // 60)}m"
        if sec < 86400:
            return f"{sec // 3600}h"
        if sec < 86400 * 7:
            return f"{sec // 86400}d"
        return dt.strftime("%b %-d" if False else dt.strftime("%b %d"))
    except Exception:
        return ""


def render_tweet_card(
    raw_tweet: str = "",
    *,
    out_path: Path,
    handle: str | None = None,
    name: str | None = None,
    body: str | None = None,
    time_label: str | None = None,
    created_at: str | None = None,
    metrics: dict | None = None,
    verified: bool = False,
    verified_type: str | None = None,
    avatar_path: str | Path | None = None,
) -> Path:
    """Phone-frame X post: real avatar, premium badge type, real metrics, real text."""
    if body is None or handle is None:
        pn, ph, pb = parse_tweet_line(raw_tweet)
        display = name or pn
        handle = (handle or ph or "user").lstrip("@")
        body = body if body is not None else pb
    else:
        display = name or handle or "User"
        handle = (handle or "user").lstrip("@")
        body = body or ""

    # Resolve badge: prefer verified_type; fall back to verified bool → blue
    vtype = verified_type
    if not vtype and verified:
        vtype = "blue"

    at = f"@{handle}"
    tlabel = time_label if time_label is not None else _relative_time(created_at)
    m = metrics or {}
    replies = _fmt_metric(m.get("reply_count"))
    rts = _fmt_metric(m.get("retweet_count"))
    likes = _fmt_metric(m.get("like_count"))
    views = _fmt_metric(m.get("impression_count"))

    img = Image.new("RGB", (W, H), FEED_BG)
    draw = ImageDraw.Draw(img)

    # —— App chrome (status + top bar) ——
    pad = 48
    top_bar_h = 132
    draw.rectangle((0, 0, W, top_bar_h), fill=BG)
    # back chevron (geometry)
    draw.line([(pad + 28, top_bar_h // 2), (pad + 8, top_bar_h // 2 - 16)], fill=TEXT, width=4)
    draw.line([(pad + 28, top_bar_h // 2), (pad + 8, top_bar_h // 2 + 16)], fill=TEXT, width=4)
    _draw_x_logo_geom(draw, W // 2, top_bar_h // 2, 34)
    draw.line((0, top_bar_h, W, top_bar_h), fill=BORDER, width=2)

    # —— Tweet content block (full-width feed style, larger) ——
    content_left = pad
    content_right = W - pad
    y = top_bar_h + 40

    av = 96
    _paste_avatar(img, left=content_left, top=y, size=av, handle=handle, avatar_path=avatar_path)
    draw = ImageDraw.Draw(img)

    name_f = _font(38, bold=True)
    meta_f = _font(32)
    body_f = _font(40)
    action_f = _font(28)
    emoji_font = _emoji_font(42)

    hx = content_left + av + 28
    hy = y + 4
    draw.text((hx, hy), display, font=name_f, fill=TEXT)
    nx = hx + int(draw.textlength(display, font=name_f)) + 10
    nx += _draw_verified_badge(draw, nx, hy + 6, verified_type=vtype, size=30)
    meta = at + (f" · {tlabel}" if tlabel else "")
    draw.text((hx, hy + 44), meta, font=meta_f, fill=MUTED)

    # three-dot menu (geometry)
    for dx in (-14, 0, 14):
        draw.ellipse(
            (content_right - 20 + dx, hy + 14, content_right - 12 + dx, hy + 22),
            fill=MUTED,
        )

    # Body with real emoji (Apple Color Emoji) — no tofu boxes
    y_body = y + av + 28
    max_w = content_right - content_left
    clean_body = (body or "").replace("\r", "").strip()
    paragraphs = [p.strip() for p in clean_body.split("\n") if p.strip()] or [""]
    for pi, para in enumerate(paragraphs):
        for line in _wrap_mixed(para, body_f, emoji_font, 42, max_w)[:14]:
            _draw_mixed_text(
                img, (content_left, y_body), line, text_font=body_f, fill=TEXT, emoji_size=42
            )
            y_body += 54
        if pi < len(paragraphs) - 1:
            y_body += 14
    draw = ImageDraw.Draw(img)

    # Timestamp detail line
    y_body += 28
    if created_at:
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            detailed = dt.strftime("%I:%M %p · %b %d, %Y").lstrip("0")
        except Exception:
            detailed = tlabel or ""
    else:
        detailed = tlabel or ""
    if detailed:
        draw.text((content_left, y_body), detailed, font=meta_f, fill=MUTED)
        y_body += 48

    if m.get("impression_count"):
        draw.text((content_left, y_body), f"{views}  ", font=_font(32, bold=True), fill=TEXT)
        vw = int(draw.textlength(f"{views}  ", font=_font(32, bold=True)))
        draw.text((content_left + vw, y_body), "Views", font=_font(32), fill=MUTED)
        y_body += 52

    draw.line((content_left, y_body, content_right, y_body), fill=BORDER, width=1)
    y_body += 28

    parts = []
    if m.get("retweet_count") is not None:
        parts.append((rts, "Reposts"))
    if m.get("quote_count") is not None:
        parts.append((_fmt_metric(m.get("quote_count")), "Quotes"))
    if m.get("like_count") is not None:
        parts.append((likes, "Likes"))
    if m.get("bookmark_count"):
        parts.append((_fmt_metric(m.get("bookmark_count")), "Bookmarks"))
    x = content_left
    for num, label in parts[:4]:
        draw.text((x, y_body), str(num), font=_font(32, bold=True), fill=TEXT)
        nw = int(draw.textlength(str(num) + " ", font=_font(32, bold=True)))
        draw.text((x + nw, y_body), label, font=_font(32), fill=MUTED)
        x += nw + int(draw.textlength(label, font=_font(32))) + 36
    y_body += 56
    draw.line((content_left, y_body, content_right, y_body), fill=BORDER, width=1)
    y_body += 44

    # Action row — drawn icons (never emoji / never □)
    slot = (content_right - content_left) // 5
    icon_fns = (_icon_reply, _icon_repost, _icon_like, _icon_views, _icon_share)
    nums = (replies, rts, likes, views, "")
    colors = (MUTED, MUTED, LIKE_RED, MUTED, MUTED)
    for i, (fn, num, col) in enumerate(zip(icon_fns, nums, colors)):
        cx = content_left + i * slot + slot // 2
        fn(draw, cx - 18, y_body, color=col, s=16)
        if num:
            draw.text((cx + 14, y_body), str(num), font=action_f, fill=MUTED, anchor="lm")

    # Bottom tab bar — geometric icons only
    tab_y = H - 140
    draw.line((0, tab_y, W, tab_y), fill=BORDER, width=2)
    draw.rectangle((0, tab_y, W, H), fill=BG)
    tab_icons = (_icon_home, _icon_search, _draw_x_logo_geom, _icon_bell, _icon_dm)
    for i, fn in enumerate(tab_icons):
        cx = int((i + 0.5) * W / 5)
        cy = tab_y + 52
        col = ACCENT if i == 0 else MUTED
        if fn is _draw_x_logo_geom:
            fn(draw, cx, cy, 22)
        else:
            fn(draw, cx, cy, color=col, s=18)
    draw.rounded_rectangle((W // 2 - 70, H - 28, W // 2 + 70, H - 16), radius=6, fill=MUTED)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=95)
    return out_path


def render_camp_divider(camp_key: str, *, out_path: Path, subtitle: str = "") -> Path:
    label = "Camp 1" if camp_key == "camp1" else "Camp 2" if camp_key == "camp2" else camp_key
    color = CAMP1 if camp_key == "camp1" else CAMP2 if camp_key == "camp2" else ACCENT
    base = tuple(max(0, min(255, c // 8)) for c in color)
    img = Image.new("RGB", (W, H), base)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, W, 140), fill=(0, 0, 0))
    _draw_x_logo_geom(draw, W // 2 - 70, 70, 28)
    draw.text((W // 2 + 20, 70), "next side", font=_font(32), fill=MUTED, anchor="lm")
    draw.rounded_rectangle((72, 640, W - 72, 1280), radius=40, fill=CARD_BG, outline=color, width=6)
    draw.ellipse((W // 2 - 48, 720, W // 2 + 48, 816), fill=color)
    draw.text(
        (W // 2, 768),
        "1" if camp_key == "camp1" else "2",
        font=_font(48, bold=True),
        fill=(0, 0, 0),
        anchor="mm",
    )
    draw.text((W // 2, 920), label, font=_font(64, bold=True), fill=color, anchor="mm")
    sub = subtitle or ""
    for i, line in enumerate(_wrap(draw, sub, _font(32), W - 220)[:4]):
        draw.text((W // 2, 1020 + i * 42), line, font=_font(32), fill=TEXT, anchor="mm")
    draw.text((W // 2, 1220), "Posts coming up →", font=_font(30), fill=MUTED, anchor="mm")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=92)
    return out_path


def _draw_vertical_fade(img: Image.Image, top_rgb, bot_rgb) -> None:
    """Simple top→bottom gradient background."""
    px = img.load()
    for y in range(H):
        t = y / max(1, H - 1)
        r = int(top_rgb[0] * (1 - t) + bot_rgb[0] * t)
        g = int(top_rgb[1] * (1 - t) + bot_rgb[1] * t)
        b = int(top_rgb[2] * (1 - t) + bot_rgb[2] * t)
        for x in range(W):
            px[x, y] = (r, g, b)


def render_title_card(topic: str, *, out_path: Path, n_tweets: int = 0) -> Path:
    """Opening cover — question-led, X brief branding, stats chips."""
    img = Image.new("RGB", (W, H), BG)
    _draw_vertical_fade(img, (8, 12, 22), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # top brand bar
    draw.rectangle((0, 0, W, 120), fill=(0, 0, 0))
    _draw_x_logo_geom(draw, 72, 60, 28)
    draw.text((110, 60), "Brief", font=_font(34, bold=True), fill=TEXT, anchor="lm")
    draw.text((W - 56, 60), "LIVE DISCOURSE", font=_font(22, bold=True), fill=ACCENT, anchor="rm")
    draw.line((0, 120, W, 120), fill=BORDER, width=2)

    # accent orb
    draw.ellipse((W - 420, 180, W + 80, 680), outline=(20, 40, 70), width=3)
    draw.ellipse((-80, 1400, 420, 1900), outline=(40, 20, 40), width=3)

    # kicker
    kicker = "TODAY ON X"
    draw.text((W // 2, 320), kicker, font=_font(28, bold=True), fill=ACCENT, anchor="mm")
    # underline accent
    kw = int(draw.textlength(kicker, font=_font(28, bold=True)))
    draw.rounded_rectangle(
        (W // 2 - kw // 2 - 8, 348, W // 2 + kw // 2 + 8, 354),
        radius=3,
        fill=ACCENT,
    )

    # main question / topic
    title_f = _font(58, bold=True)
    y = 420
    for line in _wrap(draw, topic or "Topic", title_f, W - 140)[:6]:
        draw.text((W // 2, y), line, font=title_f, fill=TEXT, anchor="mm")
        y += 72

    # subtitle
    y += 24
    sub = "Two camps. Real posts. What X is saying."
    draw.text((W // 2, y), sub, font=_font(30), fill=MUTED, anchor="mm")

    # stats row
    y = 980
    chips = []
    if n_tweets:
        chips.append((f"{int(n_tweets):,} posts", ACCENT, (0, 0, 0)))
    chips.append(("2 camps", (40, 40, 44), TEXT))
    chips.append(("Hydrated from X", (40, 40, 44), TEXT))
    total_w = 0
    measures = []
    for text, fill, fg in chips:
        font = _font(26, bold=True)
        tw = int(draw.textlength(text, font=font)) + 44
        measures.append((text, fill, fg, tw, font))
        total_w += tw
    total_w += 16 * (len(measures) - 1)
    x = (W - total_w) // 2
    for text, fill, fg, tw, font in measures:
        h = 52
        draw.rounded_rectangle((x, y, x + tw, y + h), radius=26, fill=fill)
        draw.text((x + tw // 2, y + h // 2), text, font=font, fill=fg, anchor="mm")
        x += tw + 16

    # bottom CTA strip
    draw.rounded_rectangle((72, 1600, W - 72, 1720), radius=28, fill=CARD_BG, outline=BORDER, width=2)
    draw.text((W // 2, 1640), "Scroll the evidence", font=_font(30, bold=True), fill=TEXT, anchor="mm")
    draw.text((W // 2, 1688), "Summary → Camp posts → Leaning", font=_font(26), fill=MUTED, anchor="mm")

    draw.rounded_rectangle((W // 2 - 70, H - 28, W // 2 + 70, H - 16), radius=6, fill=MUTED)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=95)
    return out_path


def render_summary_card(
    body: str,
    *,
    out_path: Path,
    topic: str = "",
) -> Path:
    """Second page — editorial summary of the discourse."""
    img = Image.new("RGB", (W, H), BG)
    _draw_vertical_fade(img, (10, 14, 24), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # header
    draw.rectangle((0, 0, W, 120), fill=(0, 0, 0))
    _draw_x_logo_geom(draw, 72, 60, 26)
    draw.text((110, 60), "Brief", font=_font(32, bold=True), fill=TEXT, anchor="lm")
    draw.text((W - 56, 60), "01  /  SUMMARY", font=_font(22, bold=True), fill=MUTED, anchor="rm")
    draw.line((0, 120, W, 120), fill=BORDER, width=2)

    # section label
    draw.rounded_rectangle((56, 180, 320, 236), radius=20, fill=(20, 40, 70))
    draw.text((188, 208), "WHAT'S HAPPENING", font=_font(22, bold=True), fill=ACCENT, anchor="mm")

    y = 280
    if topic:
        for line in _wrap(draw, topic, _font(36, bold=True), W - 120)[:3]:
            draw.text((56, y), line, font=_font(36, bold=True), fill=TEXT)
            y += 48
        y += 12

    # main body card
    card_top = y + 8
    card_bot = H - 280
    draw.rounded_rectangle(
        (40, card_top, W - 40, card_bot),
        radius=32,
        fill=(14, 16, 20),
        outline=BORDER,
        width=2,
    )
    # left accent bar
    draw.rounded_rectangle(
        (40, card_top + 28, 52, card_bot - 28),
        radius=6,
        fill=ACCENT,
    )

    body_f = _font(34)
    yb = card_top + 40
    max_w = W - 140
    for line in _wrap(draw, body or "", body_f, max_w)[:22]:
        draw.text((72, yb), line, font=body_f, fill=TEXT)
        yb += 46
        if yb > card_bot - 48:
            break

    # footer hint
    draw.text(
        (W // 2, H - 200),
        "Next: posts from each camp",
        font=_font(28),
        fill=MUTED,
        anchor="mm",
    )
    draw.rounded_rectangle((W // 2 - 70, H - 28, W // 2 + 70, H - 16), radius=6, fill=MUTED)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=95)
    return out_path


def render_leaning_card(
    leaning: str,
    *,
    out_path: Path,
    topic: str = "",
    camp1_name: str = "Camp 1",
    camp2_name: str = "Camp 2",
) -> Path:
    """Closing page — overall X leaning / verdict."""
    img = Image.new("RGB", (W, H), BG)
    _draw_vertical_fade(img, (18, 10, 22), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.rectangle((0, 0, W, 120), fill=(0, 0, 0))
    _draw_x_logo_geom(draw, 72, 60, 26)
    draw.text((110, 60), "Brief", font=_font(32, bold=True), fill=TEXT, anchor="lm")
    draw.text((W - 56, 60), "END  /  LEANING", font=_font(22, bold=True), fill=MUTED, anchor="rm")
    draw.line((0, 120, W, 120), fill=BORDER, width=2)

    draw.rounded_rectangle((56, 180, 300, 236), radius=20, fill=(50, 20, 40))
    draw.text((178, 208), "X LEANING", font=_font(22, bold=True), fill=CAMP2, anchor="mm")

    # verdict chip from leaning text
    lean_l = (leaning or "").strip().lower()
    if lean_l in ("mixed", "neutral", "split"):
        verdict = "MIXED"
        vcolor = GOLD
    elif "camp1" in lean_l or "pro-" in lean_l:
        verdict = "LEAN CAMP 1"
        vcolor = CAMP1
    elif "camp2" in lean_l:
        verdict = "LEAN CAMP 2"
        vcolor = CAMP2
    else:
        # free text leaning — show MIXED-style label from first word
        verdict = (leaning or "UNCLEAR").strip().upper()[:18]
        vcolor = ACCENT

    draw.text((W // 2, 320), "Overall read", font=_font(28), fill=MUTED, anchor="mm")
    draw.text((W // 2, 400), verdict, font=_font(64, bold=True), fill=vcolor, anchor="mm")

    # balance meter
    mx0, mx1, my = 100, W - 100, 500
    draw.rounded_rectangle((mx0, my, mx1, my + 18), radius=9, fill=(40, 40, 44))
    mid = (mx0 + mx1) // 2
    if verdict == "MIXED" or lean_l in ("mixed", "neutral", "split"):
        # center knob
        draw.ellipse((mid - 22, my - 12, mid + 22, my + 30), fill=GOLD)
    elif "CAMP 1" in verdict:
        draw.rounded_rectangle((mx0, my, mid + 40, my + 18), radius=9, fill=CAMP1)
        draw.ellipse((mid + 20, my - 12, mid + 64, my + 30), fill=CAMP1)
    elif "CAMP 2" in verdict:
        draw.rounded_rectangle((mid - 40, my, mx1, my + 18), radius=9, fill=CAMP2)
        draw.ellipse((mid - 64, my - 12, mid - 20, my + 30), fill=CAMP2)
    else:
        draw.ellipse((mid - 22, my - 12, mid + 22, my + 30), fill=vcolor)

    draw.text((mx0, my + 48), "Camp 1", font=_font(24, bold=True), fill=CAMP1)
    draw.text((mx1, my + 48), "Camp 2", font=_font(24, bold=True), fill=CAMP2, anchor="ra")

    # camp name chips
    y = 620
    for label, name, col in (
        ("CAMP 1", camp1_name, CAMP1),
        ("CAMP 2", camp2_name, CAMP2),
    ):
        draw.rounded_rectangle((56, y, W - 56, y + 130), radius=24, fill=CARD_BG, outline=col, width=3)
        draw.rounded_rectangle((76, y + 24, 200, y + 68), radius=14, fill=col)
        draw.text((138, y + 46), label, font=_font(22, bold=True), fill=(0, 0, 0), anchor="mm")
        for i, line in enumerate(_wrap(draw, name or label, _font(28), W - 240)[:2]):
            draw.text((220, y + 36 + i * 36), line, font=_font(28), fill=TEXT)
        y += 150

    # leaning body
    draw.rounded_rectangle((56, y + 20, W - 56, H - 220), radius=28, fill=(14, 16, 20), outline=BORDER, width=2)
    yb = y + 52
    for line in _wrap(draw, leaning or "", _font(32), W - 160)[:12]:
        draw.text((88, yb), line, font=_font(32), fill=TEXT)
        yb += 42

    draw.text((W // 2, H - 160), "End of brief", font=_font(26), fill=MUTED, anchor="mm")
    draw.rounded_rectangle((W // 2 - 70, H - 28, W // 2 + 70, H - 16), radius=6, fill=MUTED)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=95)
    return out_path


