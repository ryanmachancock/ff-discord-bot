"""Renders the roster-first team card as a PNG for /team.

Matches the locked-in HTML/CSS mockup (turf header, dashed sideline roster
list, position badge + real-position tag, injury status tags, optional
live-game score bug with possession/red-zone highlighting, starters total,
bench section). Discord embeds can't do this layout, so it's drawn directly
with Pillow instead.

Data contract for render_team_card(data):
{
  'team_name': str, 'owner_name': str, 'record': str ("6-8"),
  'rank': int, 'total_teams': int, 'current_week': int,
  'proj_record': str or None ("8-6"), 'proj_record_note': str or None ("unlucky"),
  'live_games_count': int,
  'starters': [ROW, ...], 'bench': [ROW, ...],
  'starters_total_actual': float, 'starters_total_proj': float,
}

ROW = {
  'slot': str ("QB"/"RB"/"WR"/"TE"/"FLEX"/"D/ST"/"K"/"BE" -- the roster slot),
  'position': str (the player's real position, e.g. "RB" for a FLEX slot),
  'name': str, 'opp_abbr': str ("SEA"),
  'headshot_path': str or None (local cached file; None = fallback initial),
  'is_logo': bool (True for D/ST rows, so the image is contain-fit not cover-fit),
  'status': 'Q' or 'O' or None,
  'actual': float, 'proj': float,
  'live': None or {
      'team_abbr': str, 'opp_abbr': str, 'team_score': int, 'opp_score': int,
      'clock': str ("Q3 8:42"), 'possession': bool, 'redzone': bool,
      'quiet': bool (game live, this player has no possession/redzone signal),
  },
}

Live data is optional and only ever populated once the real ESPN live-
scoreboard field names are verified in-season (see project memory) --
until then callers just pass 'live': None and the score-bug simply doesn't
render for that row, exactly like a bye-week/non-live player today.
"""

import io
import math
import itertools
from PIL import Image, ImageDraw, ImageFont

SCALE = 2  # render at 2x the CSS mockup's logical pixels for a crisp PNG
CARD_W = 640 * SCALE
# Discord fits inline attachment previews inside a ~400x300 box. Our cards are
# tall (many stacked rows), so the ~300px HEIGHT cap is what actually governs
# the shrink for nearly all of them -- not width. Shrinking CARD_W (tried and
# reverted) only raises the font/canvas ratio on the width axis, which has no
# effect when height is the binding constraint: it just increases name
# truncation for zero visible size gain. Making cards legible without a click
# requires reducing their total pixel HEIGHT (shorter rows/header), not width.


def px(v):
    return round(v * SCALE)


# ---------------------------------------------------------------- colors --
TURF_C1 = (43, 110, 63)
TURF_C2 = (36, 96, 54)
WHITE = (255, 255, 255)
ROSTER_BG = (255, 255, 255)
ROSTER_ZEBRA = (245, 248, 244)
DASH_SIDELINE = (205, 214, 200)
SECTION_HDR_GREEN = (47, 107, 58)
SECTION_HDR_BORDER = (211, 216, 206)
POS_BADGE_BG = (238, 236, 231)
POS_BADGE_FG = (74, 70, 61)
STATUS_Q_BG = (247, 231, 195)
STATUS_Q_FG = (107, 74, 0)
STATUS_OUT_BG = (242, 215, 213)
STATUS_OUT_FG = (140, 30, 20)
PLAYER_SUB_GRAY = (110, 122, 108)
PTS_DARK = (28, 43, 30)
PTS_MUTED = (133, 124, 109)
LIVE_POSS_BG = (255, 247, 226)
LIVE_POSS_ACCENT = (201, 133, 0)
LIVE_REDZONE_BG = (250, 229, 220)
LIVE_REDZONE_ACCENT = (236, 131, 90)
RZ_TAG_BG = (247, 213, 201)
RZ_TAG_FG = (184, 72, 31)
LIVE_BADGE_BG = (22, 19, 10)
LIVE_BADGE_FG = (255, 210, 63)
STREAK_W_BG = (222, 238, 227)
STREAK_W_FG = (30, 107, 48)
STREAK_L_BG = (246, 227, 225)
STREAK_L_FG = (161, 47, 34)
LIVE_BADGE_DOT = (255, 77, 77)
LIVE_DOT_BLUE = (110, 143, 201)
TOTAL_ROW_BG = (244, 241, 231)
TEAM_META = (217, 242, 224)
HEADER_SUB = (205, 238, 214)
HEADSHOT_BG = (228, 224, 213)
HEADSHOT_BORDER = (216, 211, 196)
FALLBACK_BG = (22, 19, 10)

BENCH_FADE = 0.18  # approximates CSS opacity:0.82 by blending toward white


def _fade(color, amt=BENCH_FADE, toward=WHITE):
    return tuple(round(c + (t - c) * amt) for c, t in zip(color, toward))


_FONT_CANDIDATES = {
    'regular': ["C:/Windows/Fonts/segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"],
    'bold': ["C:/Windows/Fonts/segoeuib.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
    'impact': ["C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/bahnschrift.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}
_font_cache = {}


def _font(weight, size):
    key = (weight, size)
    if key in _font_cache:
        return _font_cache[key]
    for path in _FONT_CANDIDATES[weight]:
        try:
            font = ImageFont.truetype(path, size)
            _font_cache[key] = font
            return font
        except OSError:
            continue
    # ponytail: no TTF found (e.g. a bare Linux box without fonts-dejavu-core)
    # -- fall back to Pillow's bitmap font so the card still renders.
    font = ImageFont.load_default(size=size) if hasattr(ImageFont, "load_default") else ImageFont.load_default()
    _font_cache[key] = font
    return font


def _tw(draw, text, font):
    return draw.textlength(text, font=font)


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}" + {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')


def _ellipsize(draw, text, font, max_w):
    if _tw(draw, text, font) <= max_w:
        return text
    while text and _tw(draw, text + "…", font) > max_w:
        text = text[:-1]
    return text + "…" if text else "…"


def _dashed_hline(draw, x0, x1, y, color, width=1, dash=6, gap=4):
    x = x0
    while x < x1:
        draw.line((x, y, min(x + dash, x1), y), fill=color, width=width)
        x += dash + gap


def _dashed_vline(draw, x, y0, y1, color, width=1, dash=6, gap=4):
    y = y0
    while y < y1:
        draw.line((x, y, x, min(y + dash, y1)), fill=color, width=width)
        y += dash + gap


def _finalize(img):
    """Every card ends by calling this instead of building its own BytesIO --
    Discord doesn't put any border around a posted image attachment, so
    without one, content that runs flush to the edge (the dashed sideline at
    x=0, the turf header's top bleed, the /bench footer hint) can read as an
    unintentional crop rather than a deliberate card boundary. What the
    border needs contrast against is Discord's OWN chrome, not the card's
    internal colors -- both the chat background and the click-to-expand
    lightbox backdrop are near-black on Discord's (default, and far more
    common) dark theme, so white is what actually reads there (a near-black
    border, tried first, was invisible against that same near-black chrome
    even though it looked fine against the card's own white/green content
    in isolation). The frame is added as genuinely new canvas -- expand the
    image and paste the card inside it -- rather than an outline drawn on
    top of the existing edge pixels, which clipped into content sitting
    close to the border (the bottom footer hint text)."""
    bw = px(8)
    framed = Image.new("RGB", (img.width + bw * 2, img.height + bw * 2), WHITE)
    framed.paste(img, (bw, bw))
    buf = io.BytesIO()
    framed.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _turf_stripes(img, box, c1, c2, stripe_w):
    """Approximates the CSS `repeating-linear-gradient(115deg, ...)` turf
    texture with sheared parallelogram bands -- close enough for a
    decorative background; the exact gradient angle isn't load-bearing."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    shear = h * 0.35
    draw = ImageDraw.Draw(img)
    x = x0 - shear - stripe_w
    i = 0
    while x < x1 + stripe_w:
        color = c1 if i % 2 == 0 else c2
        poly = [(x, y0), (x + stripe_w, y0), (x + stripe_w + shear, y1), (x + shear, y1)]
        draw.polygon(poly, fill=color)
        x += stripe_w
        i += 1


def _circle_image(canvas, path, cx, cy, r, border_color=None, border_w=0,
                   fallback_text=None, fallback_bg=FALLBACK_BG, contain=False, bg=HEADSHOT_BG):
    """Pastes a circular-cropped image (cover or contain fit) centered at
    (cx, cy) with radius r, or draws a fallback initial circle if path is
    missing/unreadable -- same visual language for "no photo" everywhere."""
    cx, cy, r = int(cx), int(cy), int(r)
    d = r * 2
    if path:
        try:
            src = Image.open(path).convert("RGBA")
            if contain:
                base = Image.new("RGBA", (d, d), bg + (255,))
                src.thumbnail((d - px(3), d - px(3)))
                base.paste(src, ((d - src.width) // 2, (d - src.height) // 2), src)
                circle = base
            else:
                w, h = src.size
                side = min(w, h)
                src = src.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2)).resize((d, d))
                circle = src
            mask = Image.new("L", (d, d), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, d, d), fill=255)
            canvas.paste(circle, (cx - r, cy - r), mask)
        except Exception:
            path = None
    if not path:
        circle = Image.new("RGBA", (d, d), fallback_bg + (255,))
        cdraw = ImageDraw.Draw(circle)
        letter = (fallback_text or "?")[0].upper()
        f = _font('impact', int(d * 0.5))
        tw = cdraw.textlength(letter, font=f)
        cdraw.text(((d - tw) / 2, d * 0.12), letter, font=f, fill=WHITE)
        mask = Image.new("L", (d, d), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, d, d), fill=255)
        canvas.paste(circle, (cx - r, cy - r), mask)
    if border_color and border_w:
        draw = ImageDraw.Draw(canvas)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=border_color, width=border_w)


def _chip(draw, x, y, text, font, fg, bg, pad_x=None, pad_y=None, radius=None):
    """Draws a small rounded chip (position badge / status tag) with the
    text centered inside; returns the x-coordinate just past its right edge
    so callers can lay out a run of chips left-to-right."""
    pad_x = px(5) if pad_x is None else pad_x
    pad_y = px(2) if pad_y is None else pad_y
    radius = px(3) if radius is None else radius
    tw = draw.textlength(text, font=font)
    asc, desc = font.getmetrics()
    th = asc + desc
    w = tw + pad_x * 2
    h = th * 0.72 + pad_y * 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=bg)
    draw.text((x + pad_x, y + pad_y - desc * 0.15), text, font=font, fill=fg)
    return x + w


def _football_icon(draw, x, y, w, h):
    draw.ellipse((x, y, x + w, y + h), fill=(139, 90, 43), outline=(92, 58, 26), width=max(1, px(0.4)))
    draw.line((x + w * 0.28, y + h / 2, x + w * 0.72, y + h / 2), fill=WHITE, width=max(1, px(0.6)))


def render_team_card(data: dict) -> io.BytesIO:
    """Starters only -- bench players live in their own /bench card now (see
    render_bench_card) so this card can afford full two-line rows without
    tripping Discord's ~300px inline-preview height cap on a 12+ row list."""
    starters = data['starters']
    bench_count = data.get('bench_count', 0)

    f_team_name = _font('impact', px(26))
    f_team_meta = _font('bold', px(11))
    f_header_record = _font('impact', px(22))
    f_header_sub = _font('bold', px(11))
    f_live_badge = _font('bold', px(10))
    f_section_hdr = _font('bold', px(10))
    f_player_name = _font('bold', px(13))
    f_tag = _font('bold', px(9.5))
    f_player_sub = _font('regular', px(11.5))
    f_pts_actual = _font('impact', px(17))
    f_pts_proj = _font('regular', px(10.5))
    f_total_label = _font('bold', px(11))
    f_total_val = _font('impact', px(20))
    f_total_val_sm = _font('regular', px(12))
    f_footer = _font('bold', px(10))

    # ---- pass 1: measure heights ----
    HEADER_H = px(96) if data.get('live_games_count', 0) > 0 else px(78)
    SECTION_HDR_H = px(30)
    ROW_H_BASE = px(46)
    ROW_H_LIVE = px(60)
    TOTAL_ROW_H = px(38)
    FOOTER_H = px(26) if bench_count else 0

    def row_h(row):
        return ROW_H_LIVE if row.get('live') else ROW_H_BASE

    starters_h = sum(row_h(r) for r in starters)

    total_h = HEADER_H + SECTION_HDR_H + starters_h + TOTAL_ROW_H + FOOTER_H

    img = Image.new("RGB", (CARD_W, total_h), ROSTER_BG)
    draw = ImageDraw.Draw(img)

    # ---- header ----
    _turf_stripes(img, (0, 0, CARD_W, HEADER_H), TURF_C1, TURF_C2, px(42))
    draw = ImageDraw.Draw(img)

    pad_l, pad_r = px(22), px(22)
    logo_r = px(23)
    logo_cx, logo_cy = pad_l + logo_r, HEADER_H // 2
    _circle_image(img, None, logo_cx, logo_cy, logo_r, border_color=(255, 255, 255, 150), border_w=px(2),
                  fallback_text=data['team_name'], fallback_bg=FALLBACK_BG)
    draw = ImageDraw.Draw(img)

    text_x = logo_cx + logo_r + px(12)
    name_y = HEADER_H // 2 - px(20)
    draw.text((text_x, name_y), data['team_name'].upper(), font=f_team_name, fill=WHITE)
    meta = f"{data['owner_name'].upper()}  ·  RANK {data['rank']} OF {data['total_teams']}  ·  WEEK {data['current_week']}"
    draw.text((text_x, name_y + px(30)), meta, font=f_team_meta, fill=TEAM_META)

    record_text = data['record']
    rw = _tw(draw, record_text, f_header_record)
    right_y = HEADER_H // 2 - px(24)
    draw.text((CARD_W - pad_r - rw, right_y), record_text, font=f_header_record, fill=WHITE)
    if data.get('proj_record'):
        sub = f"Proj. {data['proj_record']}"
        if data.get('proj_record_note'):
            sub += f"  ·  {data['proj_record_note']}"
        sw = _tw(draw, sub, f_header_sub)
        draw.text((CARD_W - pad_r - sw, right_y + px(24)), sub, font=f_header_sub, fill=HEADER_SUB)
    if data.get('live_games_count', 0) > 0:
        label = f"{data['live_games_count']} Games Live"
        lw = _tw(draw, label, f_live_badge) + px(5 + 6) + px(6 * 2)
        bx = CARD_W - pad_r - lw
        by = right_y + px(24 + 20)
        draw.rounded_rectangle((bx, by, bx + lw, by + px(18)), radius=px(3), fill=LIVE_BADGE_BG)
        dot_r = px(3)
        draw.ellipse((bx + px(9) - dot_r, by + px(9) - dot_r, bx + px(9) + dot_r, by + px(9) + dot_r), fill=LIVE_BADGE_DOT)
        draw.text((bx + px(9) + dot_r + px(5), by + px(4)), label, font=f_live_badge, fill=LIVE_BADGE_FG)

    # ---- roster helpers ----
    def section_header(y, label):
        draw.text((px(8), y + px(9)), label.upper(), font=f_section_hdr, fill=SECTION_HDR_GREEN)
        proj_w = _tw(draw, "PROJ", f_section_hdr)
        draw.text((CARD_W - px(22) - proj_w, y + px(9)), "PROJ", font=f_section_hdr, fill=SECTION_HDR_GREEN)
        _dashed_hline(draw, 0, CARD_W, y + SECTION_HDR_H, SECTION_HDR_BORDER, width=max(1, px(0.5)))

    def draw_row(y, h, row, faded=False):
        F = (lambda c: _fade(c)) if faded else (lambda c: c)
        live = row.get('live')
        bg = ROSTER_BG
        accent = None
        if live and live.get('redzone'):
            bg, accent = LIVE_REDZONE_BG, LIVE_REDZONE_ACCENT
        elif live and live.get('possession'):
            bg, accent = LIVE_POSS_BG, LIVE_POSS_ACCENT
        draw.rectangle((0, y, CARD_W, y + h), fill=F(bg))
        if accent:
            draw.rectangle((0, y, px(3), y + h), fill=F(accent))
        _dashed_vline(draw, 0, y, y + h, F(DASH_SIDELINE), width=max(1, px(1.5)))

        cx = px(8 + 17)
        cy = y + h // 2 - (px(7) if live else 0)
        _circle_image(img, row.get('headshot_path'), cx, cy, px(17),
                      border_color=F(HEADSHOT_BORDER), border_w=max(1, px(0.5)),
                      fallback_text=row['name'], contain=row.get('is_logo', False), bg=F(HEADSHOT_BG))

        badge_x = px(8 + 34 + 8)
        badge_w = px(40)
        badge_h = px(19)
        badge_y = y + h // 2 - badge_h // 2 - (px(7) if live else 0)
        draw.rounded_rectangle((badge_x, badge_y, badge_x + badge_w, badge_y + badge_h), radius=px(3), fill=F(POS_BADGE_BG))
        slot = row['slot']
        sw = _tw(draw, slot, f_tag)
        draw.text((badge_x + (badge_w - sw) / 2, badge_y + px(3)), slot, font=f_tag, fill=F(POS_BADGE_FG))

        info_x = badge_x + badge_w + px(8)
        info_right = CARD_W - px(22) - px(56)
        text_top = y + px(6) - (px(7) if live else 0)

        nx = info_x
        name_txt = _ellipsize(draw, row['name'], f_player_name, (info_right - info_x) * 0.62)
        draw.text((nx, text_top), name_txt, font=f_player_name, fill=F(PTS_DARK))
        nx += _tw(draw, name_txt, f_player_name) + px(6)
        nx = _chip(draw, nx, text_top + px(2), row['position'], f_tag, F(POS_BADGE_FG), F(POS_BADGE_BG))
        if row.get('status') == 'Q':
            _chip(draw, nx + px(4), text_top + px(2), "Q", f_tag, F(STATUS_Q_FG), F(STATUS_Q_BG))
        elif row.get('status') == 'O':
            _chip(draw, nx + px(4), text_top + px(2), "O", f_tag, F(STATUS_OUT_FG), F(STATUS_OUT_BG))

        sub = f"{row.get('team_abbr', '')} vs {row.get('opp_abbr', '')}".strip()
        sub_y = text_top + px(19)
        sub_x = info_x
        if live and live.get('quiet'):
            dot_r = px(3)
            draw.ellipse((sub_x, sub_y + px(6) - dot_r, sub_x + dot_r * 2, sub_y + px(6) + dot_r), fill=F(LIVE_DOT_BLUE))
            sub_x += dot_r * 2 + px(4)
        draw.text((sub_x, sub_y), sub, font=f_player_sub, fill=F(PLAYER_SUB_GRAY))

        if live:
            sb_y = sub_y + px(18)
            sb_x = info_x
            if live.get('possession'):
                _football_icon(draw, sb_x, sb_y + px(3), px(10), px(7))
                sb_x += px(14)
            f_score_bug = _font('bold', px(11))
            line1 = f"{live['team_abbr']} "
            draw.text((sb_x, sb_y), line1, font=f_score_bug, fill=F(PTS_MUTED))
            sb_x += _tw(draw, line1, f_score_bug)
            score = f"{live['team_score']}\u2013{live['opp_score']}"
            draw.text((sb_x, sb_y), score, font=f_score_bug, fill=F(PTS_DARK))
            sb_x += _tw(draw, score, f_score_bug)
            line2 = f" {live['opp_abbr']}"
            draw.text((sb_x, sb_y), line2, font=f_score_bug, fill=F(PTS_MUTED))
            sb_x += _tw(draw, line2, f_score_bug)
            draw.line((sb_x + px(3), sb_y + px(1), sb_x + px(3), sb_y + px(12)), fill=F(DASH_SIDELINE), width=max(1, px(1)))
            sb_x += px(9)
            draw.text((sb_x, sb_y), live['clock'], font=f_score_bug, fill=F(PTS_MUTED))
            sb_x += _tw(draw, live['clock'], f_score_bug) + px(5)
            if live.get('redzone'):
                _chip(draw, sb_x, sb_y - px(1), "RZ", f_tag, F(RZ_TAG_FG), F(RZ_TAG_BG))

        pts_actual = f"{row['actual']:.1f}"
        pts_proj = f"({row['proj']:.1f})"
        pw = _tw(draw, pts_actual, f_pts_actual)
        pts_y = y + h // 2 - px(15) - (px(7) if live else 0)
        draw.text((CARD_W - px(22) - pw, pts_y), pts_actual, font=f_pts_actual, fill=F(PTS_DARK))
        pjw = _tw(draw, pts_proj, f_pts_proj)
        draw.text((CARD_W - px(22) - pjw, pts_y + px(19)), pts_proj, font=f_pts_proj, fill=F(PTS_MUTED))

    # ---- starting lineup ----
    y = HEADER_H
    section_header(y, "Starting Lineup")
    y += SECTION_HDR_H
    for i, row in enumerate(starters):
        h = row_h(row)
        if i % 2 == 1:
            draw.rectangle((0, y, CARD_W, y + h), fill=ROSTER_ZEBRA)
        draw_row(y, h, row)
        y += h

    # ---- starters total ----
    draw.rectangle((0, y, CARD_W, y + TOTAL_ROW_H), fill=TOTAL_ROW_BG)
    draw.rectangle((0, y, CARD_W, y + max(1, px(1))), fill=SECTION_HDR_GREEN)
    _dashed_hline(draw, 0, CARD_W, y + TOTAL_ROW_H, SECTION_HDR_BORDER, width=max(1, px(0.5)))
    draw.text((px(22), y + px(9)), "STARTERS TOTAL", font=f_total_label, fill=SECTION_HDR_GREEN)
    total_actual = f"{data['starters_total_actual']:.1f}"
    total_proj = f" / proj {data['starters_total_proj']:.1f}"
    taw = _tw(draw, total_actual, f_total_val)
    tpw = _tw(draw, total_proj, f_total_val_sm)
    tx = CARD_W - px(22) - taw - tpw
    draw.text((tx, y + px(8)), total_actual, font=f_total_val, fill=PTS_DARK)
    draw.text((tx + taw, y + px(12)), total_proj, font=f_total_val_sm, fill=(159, 202, 168))
    y += TOTAL_ROW_H

    # ---- footer: bench players moved to their own /bench card ----
    if bench_count:
        draw.rectangle((0, y, CARD_W, y + FOOTER_H), fill=ROSTER_BG)
        hint = f"{bench_count} Bench Player{'s' if bench_count != 1 else ''}  ·  /bench {data['team_name']}"
        hw = _tw(draw, hint, f_footer)
        draw.text(((CARD_W - hw) / 2, y + FOOTER_H // 2 - px(6)), hint, font=f_footer, fill=PLAYER_SUB_GRAY)

    return _finalize(img)


def render_standings_card(data: dict) -> io.BytesIO:
    """Data contract:
    {
      'league_name': str, 'team_count': int, 'scoring_label': str ("Standard Scoring"),
      'header_right_label': str ("FINAL" or "WK 14"), 'header_right_sub': str or None,
      'playoff_team_count': int, 'playoff_gb': float or None (games back of the
          first team outside the playoff line, relative to the last team in),
      'teams': [{'rank': int, 'name': str, 'owner': str, 'record': str,
                 'pf': float, 'pa': float, 'streak': str ("W2"/"L4"),
                 'logo_path': str or None}, ...],  # already sorted by rank
    }
    """
    teams = data['teams']

    f_league_name = _font('impact', px(26))
    f_league_meta = _font('bold', px(11))
    f_header_right = _font('impact', px(22))
    f_header_sub = _font('bold', px(11))
    f_hdr_label = _font('bold', px(10))
    f_rank = _font('impact', px(15))
    f_team_name = _font('bold', px(13))
    f_team_owner = _font('regular', px(11))
    f_record = _font('bold', px(13))
    f_pf_pa = _font('regular', px(12.5))
    f_streak = _font('bold', px(10))
    f_playoff_label = _font('bold', px(10))
    f_playoff_sub = _font('bold', px(11))

    HEADER_H = px(78)
    HDR_ROW_H = px(28)
    ROW_H = px(44)
    PLAYOFF_H = px(34)

    n_playoff = data.get('playoff_team_count') or 0
    has_playoff_line = 0 < n_playoff < len(teams)

    total_h = HEADER_H + HDR_ROW_H + ROW_H * len(teams) + (PLAYOFF_H if has_playoff_line else 0)
    img = Image.new("RGB", (CARD_W, total_h), ROSTER_BG)
    _turf_stripes(img, (0, 0, CARD_W, HEADER_H), TURF_C1, TURF_C2, px(42))
    draw = ImageDraw.Draw(img)

    pad_l, pad_r = px(22), px(22)
    logo_r = px(23)
    logo_cx, logo_cy = pad_l + logo_r, HEADER_H // 2
    _circle_image(img, None, logo_cx, logo_cy, logo_r, border_color=(255, 255, 255, 150), border_w=px(2),
                  fallback_text=data['league_name'])

    text_x = logo_cx + logo_r + px(12)
    name_y = HEADER_H // 2 - px(20)
    draw.text((text_x, name_y), data['league_name'].upper(), font=f_league_name, fill=WHITE)
    meta = f"{data['team_count']} TEAMS  ·  {data['scoring_label'].upper()}"
    draw.text((text_x, name_y + px(30)), meta, font=f_league_meta, fill=TEAM_META)

    right_label = data['header_right_label']
    rw = _tw(draw, right_label, f_header_right)
    right_y = HEADER_H // 2 - px(24)
    draw.text((CARD_W - pad_r - rw, right_y), right_label, font=f_header_right, fill=WHITE)
    if data.get('header_right_sub'):
        sw = _tw(draw, data['header_right_sub'], f_header_sub)
        draw.text((CARD_W - pad_r - sw, right_y + px(24)), data['header_right_sub'], font=f_header_sub, fill=HEADER_SUB)

    # Column geometry, right-to-left, mirroring the CSS grid template.
    col_rank_w, col_avatar_w = px(22), px(30)
    col_record_w, col_pf_w, col_pa_w, col_streak_w = px(58), px(58), px(58), px(44)
    gap = px(8)
    x_rank = pad_l
    x_avatar = x_rank + col_rank_w + gap
    x_name = x_avatar + col_avatar_w + gap
    streak_r = CARD_W - pad_r
    pa_r = streak_r - col_streak_w - gap
    pf_r = pa_r - col_pa_w - gap
    record_r = pf_r - col_pf_w - gap
    name_right = record_r - col_record_w - gap

    y = HEADER_H
    draw.text((x_name, y + px(9)), "TEAM", font=f_hdr_label, fill=SECTION_HDR_GREEN)
    for label, right in (("RECORD", record_r), ("PF", pf_r), ("PA", pa_r), ("STRK", streak_r)):
        lw = _tw(draw, label, f_hdr_label)
        draw.text((right - lw, y + px(9)), label, font=f_hdr_label, fill=SECTION_HDR_GREEN)
    _dashed_hline(draw, 0, CARD_W, y + HDR_ROW_H, SECTION_HDR_BORDER, width=max(1, px(0.5)))
    y += HDR_ROW_H

    for i, t in enumerate(teams):
        if i % 2 == 1:
            draw.rectangle((0, y, CARD_W, y + ROW_H), fill=ROSTER_ZEBRA)
        _dashed_vline(draw, 0, y, y + ROW_H, DASH_SIDELINE, width=max(1, px(1.5)))

        rank_str = str(t['rank'])
        rsw = _tw(draw, rank_str, f_rank)
        draw.text((x_rank + (col_rank_w - rsw) / 2, y + ROW_H // 2 - px(9)), rank_str, font=f_rank, fill=PTS_MUTED)

        cx, cy = x_avatar + col_avatar_w // 2, y + ROW_H // 2
        _circle_image(img, t.get('logo_path'), cx, cy, col_avatar_w // 2,
                      border_color=HEADSHOT_BORDER, border_w=max(1, px(0.5)), fallback_text=t['name'])

        name_txt = _ellipsize(draw, t['name'], f_team_name, name_right - x_name)
        draw.text((x_name, y + px(6)), name_txt, font=f_team_name, fill=PTS_DARK)
        draw.text((x_name, y + px(23)), t['owner'], font=f_team_owner, fill=PTS_MUTED)

        recw = _tw(draw, t['record'], f_record)
        draw.text((record_r - recw, y + ROW_H // 2 - px(9)), t['record'], font=f_record, fill=PTS_DARK)

        pf_str = f"{t['pf']:.1f}"
        pfw = _tw(draw, pf_str, f_pf_pa)
        draw.text((pf_r - pfw, y + ROW_H // 2 - px(8)), pf_str, font=f_pf_pa, fill=POS_BADGE_FG)

        pa_str = f"{t['pa']:.1f}"
        paw = _tw(draw, pa_str, f_pf_pa)
        draw.text((pa_r - paw, y + ROW_H // 2 - px(8)), pa_str, font=f_pf_pa, fill=PTS_MUTED)

        streak = t['streak']
        is_win = streak.upper().startswith('W')
        s_bg, s_fg = (STREAK_W_BG, STREAK_W_FG) if is_win else (STREAK_L_BG, STREAK_L_FG)
        stw = _tw(draw, streak, f_streak)
        s_w, s_h = stw + px(12), px(16)
        s_x, s_y = streak_r - s_w, y + ROW_H // 2 - s_h // 2
        draw.rounded_rectangle((s_x, s_y, s_x + s_w, s_y + s_h), radius=px(3), fill=s_bg)
        draw.text((s_x + (s_w - stw) / 2, s_y + px(2)), streak, font=f_streak, fill=s_fg)

        y += ROW_H

        if has_playoff_line and (i + 1) == n_playoff:
            draw.rectangle((0, y, CARD_W, y + PLAYOFF_H), fill=TOTAL_ROW_BG)
            draw.rectangle((0, y, CARD_W, y + max(1, px(1))), fill=SECTION_HDR_GREEN)
            _dashed_hline(draw, 0, CARD_W, y + PLAYOFF_H, SECTION_HDR_BORDER, width=max(1, px(0.5)))
            draw.text((px(30), y + px(10)), "PLAYOFF LINE", font=f_playoff_label, fill=SECTION_HDR_GREEN)
            if data.get('playoff_gb') is not None:
                gb_text = f"{_ordinal(n_playoff + 1)} is {data['playoff_gb']:.1f} GB"
                gbw = _tw(draw, gb_text, f_playoff_sub)
                draw.text((CARD_W - pad_r - gbw, y + px(9)), gb_text, font=f_playoff_sub, fill=PTS_MUTED)
            y += PLAYOFF_H

    return _finalize(img)


MU_MUTED = (163, 156, 143)
MU_SCORE_LOST = (196, 214, 201)


def _centered_section_header(draw, y, h, label, font):
    lw = draw.textlength(label, font=font)
    cx = CARD_W // 2
    pad = px(10)
    rule_y = y + h // 2
    _dashed_hline(draw, px(22), cx - lw / 2 - pad, rule_y, SECTION_HDR_BORDER, width=max(1, px(0.5)))
    _dashed_hline(draw, cx + lw / 2 + pad, CARD_W - px(22), rule_y, SECTION_HDR_BORDER, width=max(1, px(0.5)))
    draw.text((cx - lw / 2, y + px(5)), label, font=font, fill=SECTION_HDR_GREEN)


def render_matchup_card(data: dict) -> io.BytesIO:
    """Starters only -- bench players live in their own /bench card now (see
    render_bench_card).
    Data contract:
    {
      'team1': {'name','owner','record','logo_path','score'}, 'team2': {same},
      'header_sub': str ("Final · Week 17"),
      'starters': [ROW, ...], 'bench_count1': int, 'bench_count2': int,
      'totals': {'left': float, 'right': float},
    }
    ROW = {
      'slot': str, 'left': SIDE, 'right': SIDE,
    }
    SIDE = {'name': str, 'position': str, 'headshot_path': str or None,
            'is_logo': bool, 'pts': float, 'proj': float, 'win': True/False/None}
    """
    starters = data['starters']
    bench_count1 = data.get('bench_count1', 0)
    bench_count2 = data.get('bench_count2', 0)

    f_mu_name = _font('bold', px(14))
    f_mu_record = _font('bold', px(11))
    f_mu_score = _font('impact', px(30))
    f_mu_dash = _font('impact', px(20))
    f_mu_sub = _font('bold', px(10.5))
    f_badge = _font('bold', px(10))
    f_pname = _font('regular', px(12))
    f_pos_tag = _font('bold', px(9))
    f_pts = _font('bold', px(14))
    f_proj = _font('regular', px(9))
    f_total_label = _font('bold', px(11))
    f_total_val = _font('impact', px(18))
    f_footer = _font('bold', px(10))

    HEADER_H = px(92)
    ROW_H = px(40)
    TOTAL_ROW_H = px(38)
    FOOTER_H = px(26) if (bench_count1 or bench_count2) else 0

    total_h = HEADER_H + ROW_H * len(starters) + TOTAL_ROW_H + FOOTER_H
    img = Image.new("RGB", (CARD_W, total_h), ROSTER_BG)
    _turf_stripes(img, (0, 0, CARD_W, HEADER_H), TURF_C1, TURF_C2, px(42))
    draw = ImageDraw.Draw(img)

    # ---- header: two mirrored team blocks around a center score ----
    pad_l, pad_r = px(22), px(22)
    logo_r = px(21)
    team_y = px(38)

    t1, t2 = data['team1'], data['team2']
    win1 = t1['score'] > t2['score']
    score1_str, score2_str = f"{t1['score']:.1f}", f"{t2['score']:.1f}"
    dash = "–"
    gap = px(8)
    score_w = _tw(draw, score1_str, f_mu_score) + gap + _tw(draw, dash, f_mu_dash) + gap + _tw(draw, score2_str, f_mu_score)
    score_x = (CARD_W - score_w) / 2

    logo1_cx = pad_l + logo_r
    _circle_image(img, t1.get('logo_path'), logo1_cx, team_y, logo_r, border_color=(255, 255, 255, 150), border_w=px(2), fallback_text=t1['name'])
    text1_x = logo1_cx + logo_r + px(10)
    text1_right = score_x - px(12)
    name1 = _ellipsize(draw, t1['name'], f_mu_name, text1_right - text1_x)
    draw.text((text1_x, team_y - px(16)), name1, font=f_mu_name, fill=WHITE)
    draw.text((text1_x, team_y + px(4)), f"{t1['owner']} · {t1['record']}", font=f_mu_record, fill=TEAM_META)

    logo2_cx = CARD_W - pad_r - logo_r
    _circle_image(img, t2.get('logo_path'), logo2_cx, team_y, logo_r, border_color=(255, 255, 255, 150), border_w=px(2), fallback_text=t2['name'])
    text2_right = logo2_cx - logo_r - px(10)
    text2_x = score_x + score_w + px(12)
    name2 = _ellipsize(draw, t2['name'], f_mu_name, text2_right - text2_x)
    n2w = _tw(draw, name2, f_mu_name)
    draw.text((text2_right - n2w, team_y - px(16)), name2, font=f_mu_name, fill=WHITE)
    rec2 = f"{t2['owner']} · {t2['record']}"
    r2w = _tw(draw, rec2, f_mu_record)
    draw.text((text2_right - r2w, team_y + px(4)), rec2, font=f_mu_record, fill=TEAM_META)

    sx = score_x
    draw.text((sx, team_y - px(19)), score1_str, font=f_mu_score, fill=WHITE if win1 else MU_SCORE_LOST)
    sx += _tw(draw, score1_str, f_mu_score) + gap
    draw.text((sx, team_y - px(11)), dash, font=f_mu_dash, fill=(255, 255, 255, 100))
    sx += _tw(draw, dash, f_mu_dash) + gap
    draw.text((sx, team_y - px(19)), score2_str, font=f_mu_score, fill=MU_SCORE_LOST if win1 else WHITE)

    sub_w = _tw(draw, data['header_sub'], f_mu_sub)
    draw.text(((CARD_W - sub_w) / 2, HEADER_H - px(22)), data['header_sub'], font=f_mu_sub, fill=TEAM_META)

    # ---- column geometry, mirroring the CSS grid ----
    col_badge_w, col_avatar_w, col_pts_w, col_div_w = px(30), px(24), px(44), px(12)
    row_pad_l, row_pad_r = px(8), px(22)
    gap6 = px(6)
    fixed_w = col_badge_w + col_avatar_w + col_pts_w + col_div_w + col_pts_w + col_avatar_w
    inner_w = CARD_W - row_pad_l - row_pad_r
    name_col_w = (inner_w - fixed_w - gap6 * 7) / 2

    x_badge = row_pad_l
    x_avatar1 = x_badge + col_badge_w + gap6
    x_name1 = x_avatar1 + col_avatar_w + gap6
    x_pts1 = x_name1 + name_col_w + gap6
    x_div = x_pts1 + col_pts_w + gap6
    x_pts2 = x_div + col_div_w + gap6
    x_name2 = x_pts2 + col_pts_w + gap6
    x_avatar2 = x_name2 + name_col_w + gap6

    def draw_side(x_name, x_avatar, side, align_right, y, h):
        cx = x_avatar + col_avatar_w // 2
        _circle_image(img, side.get('headshot_path'), cx, y + h // 2, col_avatar_w // 2,
                      border_color=HEADSHOT_BORDER, border_w=max(1, px(0.5)),
                      fallback_text=side['name'], contain=side.get('is_logo', False))
        tag_w = _tw(draw, side['position'], f_pos_tag) + px(10)
        name_avail = name_col_w - tag_w - px(4)
        name_txt = _ellipsize(draw, side['name'], f_pname, name_avail)
        name_y = y + h // 2 - px(7)
        if not align_right:
            draw.text((x_name, name_y), name_txt, font=f_pname, fill=PTS_DARK)
            tag_x = x_name + _tw(draw, name_txt, f_pname) + px(4)
            _chip(draw, tag_x, name_y - px(1), side['position'], f_pos_tag, POS_BADGE_FG, POS_BADGE_BG)
        else:
            nw = _tw(draw, name_txt, f_pname)
            tag_x = x_name + name_col_w - tag_w
            name_x = tag_x - px(4) - nw
            draw.text((name_x, name_y), name_txt, font=f_pname, fill=PTS_DARK)
            _chip(draw, tag_x, name_y - px(1), side['position'], f_pos_tag, POS_BADGE_FG, POS_BADGE_BG)

    def draw_pts(x, side, y, h):
        color = PTS_DARK if side.get('win') else (MU_MUTED if side.get('win') is False else PTS_DARK)
        pts_str = f"{side['pts']:.1f}"
        pw = _tw(draw, pts_str, f_pts)
        draw.text((x + (col_pts_w - pw) / 2, y + h // 2 - px(13)), pts_str, font=f_pts, fill=color)
        proj_str = f"({side['proj']:.1f})"
        prw = _tw(draw, proj_str, f_proj)
        draw.text((x + (col_pts_w - prw) / 2, y + h // 2 + px(3)), proj_str, font=f_proj, fill=MU_MUTED)

    y = HEADER_H
    for i, row in enumerate(starters):
        if i % 2 == 1:
            draw.rectangle((0, y, CARD_W, y + ROW_H), fill=ROSTER_ZEBRA)
        bw = _tw(draw, row['slot'], f_badge)
        draw.rounded_rectangle((x_badge, y + ROW_H // 2 - px(10), x_badge + col_badge_w, y + ROW_H // 2 + px(10)), radius=px(3), fill=POS_BADGE_BG)
        draw.text((x_badge + (col_badge_w - bw) / 2, y + ROW_H // 2 - px(7)), row['slot'], font=f_badge, fill=POS_BADGE_FG)
        draw_side(x_name1, x_avatar1, row['left'], False, y, ROW_H)
        draw_pts(x_pts1, row['left'], y, ROW_H)
        draw.line((x_div + col_div_w / 2, y + px(6), x_div + col_div_w / 2, y + ROW_H - px(6)), fill=HEADSHOT_BORDER, width=max(1, px(0.5)))
        draw_pts(x_pts2, row['right'], y, ROW_H)
        draw_side(x_name2, x_avatar2, row['right'], True, y, ROW_H)
        y += ROW_H

    draw.rectangle((0, y, CARD_W, y + TOTAL_ROW_H), fill=TOTAL_ROW_BG)
    draw.rectangle((0, y, CARD_W, y + max(1, px(1))), fill=SECTION_HDR_GREEN)
    _dashed_hline(draw, 0, CARD_W, y + TOTAL_ROW_H, SECTION_HDR_BORDER, width=max(1, px(0.5)))
    draw.text((row_pad_l, y + TOTAL_ROW_H // 2 - px(6)), "STARTERS TOTAL", font=f_total_label, fill=SECTION_HDR_GREEN)
    win_total = data['totals']['left'] > data['totals']['right']
    t1_str = f"{data['totals']['left']:.1f}"
    t1w = _tw(draw, t1_str, f_total_val)
    draw.text((x_pts1 + (col_pts_w - t1w) / 2, y + TOTAL_ROW_H // 2 - px(9)), t1_str, font=f_total_val, fill=PTS_DARK if win_total else PTS_MUTED)
    t2_str = f"{data['totals']['right']:.1f}"
    t2w = _tw(draw, t2_str, f_total_val)
    draw.text((x_pts2 + (col_pts_w - t2w) / 2, y + TOTAL_ROW_H // 2 - px(9)), t2_str, font=f_total_val, fill=PTS_MUTED if win_total else PTS_DARK)
    y += TOTAL_ROW_H

    # ---- footer: bench players moved to their own /bench card ----
    if bench_count1 or bench_count2:
        draw.rectangle((0, y, CARD_W, y + FOOTER_H), fill=ROSTER_BG)
        hint = f"{bench_count1} vs {bench_count2} Bench Players  ·  /bench {t1['name']} {t2['name']}"
        hw = _tw(draw, hint, f_footer)
        draw.text(((CARD_W - hw) / 2, y + FOOTER_H // 2 - px(6)), hint, font=f_footer, fill=PLAYER_SUB_GRAY)

    return _finalize(img)


def render_bench_card(data: dict) -> io.BytesIO:
    """Bench-only view, split out of /team and /matchup so those cards can
    keep full two-line starter rows without tripping Discord's inline-preview
    height cap. Two modes, chosen by whether 'team2' is present:

    Single-team mode:
    { 'team1': {'name','owner','record','logo_path'}, 'bench1': [ROW, ...] }
    ROW = {'slot','position','name','team_abbr','opp_abbr','headshot_path',
           'is_logo','status','actual','proj'}  (same shape as /team's rows)

    Two-team mode adds:
    { 'team2': {same as team1}, 'bench2': [ROW, ...] }
    """
    dual = 'team2' in data
    t1 = data['team1']
    bench1 = data['bench1']

    f_team_name = _font('impact', px(22))
    f_team_meta = _font('bold', px(11))
    f_bench_label = _font('impact', px(16))
    f_section_hdr = _font('bold', px(10))
    f_player_name = _font('bold', px(13))
    f_tag = _font('bold', px(9.5))
    f_player_sub = _font('regular', px(11.5))
    f_pts_actual = _font('impact', px(17))
    f_pts_proj = _font('regular', px(10.5))
    f_total_label = _font('bold', px(11))
    f_total_val = _font('impact', px(20))

    HEADER_H = px(72)
    SECTION_HDR_H = px(30)
    ROW_H = px(46)
    TOTAL_ROW_H = px(38)

    if not dual:
        total_h = HEADER_H + SECTION_HDR_H + ROW_H * len(bench1) + TOTAL_ROW_H
        img = Image.new("RGB", (CARD_W, total_h), ROSTER_BG)
        _turf_stripes(img, (0, 0, CARD_W, HEADER_H), TURF_C1, TURF_C2, px(42))
        draw = ImageDraw.Draw(img)

        pad_l, pad_r = px(22), px(22)
        logo_r = px(20)
        logo_cx, logo_cy = pad_l + logo_r, HEADER_H // 2
        _circle_image(img, t1.get('logo_path'), logo_cx, logo_cy, logo_r, border_color=(255, 255, 255, 150),
                      border_w=px(2), fallback_text=t1['name'], fallback_bg=FALLBACK_BG)
        text_x = logo_cx + logo_r + px(12)
        draw.text((text_x, HEADER_H // 2 - px(18)), t1['name'].upper(), font=f_team_name, fill=WHITE)
        draw.text((text_x, HEADER_H // 2 + px(4)), f"{t1['owner']} · {t1['record']}", font=f_team_meta, fill=TEAM_META)
        label_w = _tw(draw, "BENCH", f_bench_label)
        draw.text((CARD_W - pad_r - label_w, HEADER_H // 2 - px(11)), "BENCH", font=f_bench_label, fill=WHITE)

        def section_header(y, label):
            draw.text((px(8), y + px(9)), label.upper(), font=f_section_hdr, fill=SECTION_HDR_GREEN)
            proj_w = _tw(draw, "PROJ", f_section_hdr)
            draw.text((CARD_W - px(22) - proj_w, y + px(9)), "PROJ", font=f_section_hdr, fill=SECTION_HDR_GREEN)
            _dashed_hline(draw, 0, CARD_W, y + SECTION_HDR_H, SECTION_HDR_BORDER, width=max(1, px(0.5)))

        def draw_row(y, h, row):
            draw.rectangle((0, y, CARD_W, y + h), fill=ROSTER_BG)
            _dashed_vline(draw, 0, y, y + h, DASH_SIDELINE, width=max(1, px(1.5)))

            cx = px(8 + 17)
            cy = y + h // 2
            is_dst = row.get('is_logo', False)
            _circle_image(img, row.get('headshot_path'), cx, cy, px(17),
                          border_color=HEADSHOT_BORDER, border_w=max(1, px(0.5)),
                          fallback_text=row['name'], contain=is_dst, bg=HEADSHOT_BG)

            badge_x = px(8 + 34 + 8)
            badge_w, badge_h = px(40), px(19)
            badge_y = y + h // 2 - badge_h // 2
            draw.rounded_rectangle((badge_x, badge_y, badge_x + badge_w, badge_y + badge_h), radius=px(3), fill=POS_BADGE_BG)
            slot = row['slot']
            sw = _tw(draw, slot, f_tag)
            draw.text((badge_x + (badge_w - sw) / 2, badge_y + px(3)), slot, font=f_tag, fill=POS_BADGE_FG)

            info_x = badge_x + badge_w + px(8)
            info_right = CARD_W - px(22) - px(56)
            text_top = y + px(6)

            nx = info_x
            name_txt = _ellipsize(draw, row['name'], f_player_name, (info_right - info_x) * 0.62)
            draw.text((nx, text_top), name_txt, font=f_player_name, fill=PTS_DARK)
            nx += _tw(draw, name_txt, f_player_name) + px(6)
            nx = _chip(draw, nx, text_top + px(2), row['position'], f_tag, POS_BADGE_FG, POS_BADGE_BG)
            if row.get('status') == 'Q':
                _chip(draw, nx + px(4), text_top + px(2), "Q", f_tag, STATUS_Q_FG, STATUS_Q_BG)
            elif row.get('status') == 'O':
                _chip(draw, nx + px(4), text_top + px(2), "O", f_tag, STATUS_OUT_FG, STATUS_OUT_BG)

            sub = f"{row.get('team_abbr', '')} vs {row.get('opp_abbr', '')}".strip()
            draw.text((info_x, text_top + px(19)), sub, font=f_player_sub, fill=PLAYER_SUB_GRAY)

            pts_actual = f"{row['actual']:.1f}"
            pts_proj = f"({row['proj']:.1f})"
            pw = _tw(draw, pts_actual, f_pts_actual)
            pts_y = y + h // 2 - px(15)
            draw.text((CARD_W - px(22) - pw, pts_y), pts_actual, font=f_pts_actual, fill=PTS_DARK)
            pjw = _tw(draw, pts_proj, f_pts_proj)
            draw.text((CARD_W - px(22) - pjw, pts_y + px(19)), pts_proj, font=f_pts_proj, fill=PTS_MUTED)

        y = HEADER_H
        section_header(y, "Bench")
        y += SECTION_HDR_H
        for i, row in enumerate(bench1):
            if i % 2 == 1:
                draw.rectangle((0, y, CARD_W, y + ROW_H), fill=ROSTER_ZEBRA)
            draw_row(y, ROW_H, row)
            y += ROW_H

        draw.rectangle((0, y, CARD_W, y + TOTAL_ROW_H), fill=TOTAL_ROW_BG)
        draw.rectangle((0, y, CARD_W, y + max(1, px(1))), fill=SECTION_HDR_GREEN)
        draw.text((px(22), y + px(9)), "BENCH TOTAL", font=f_total_label, fill=SECTION_HDR_GREEN)
        total_actual = sum(r['actual'] for r in bench1)
        total_str = f"{total_actual:.1f}"
        taw = _tw(draw, total_str, f_total_val)
        draw.text((CARD_W - px(22) - taw, y + px(8)), total_str, font=f_total_val, fill=PTS_DARK)

        return _finalize(img)

    # ---- two-team mirrored mode ----
    t2 = data['team2']
    bench2 = data['bench2']
    rows = list(itertools.zip_longest(bench1, bench2))

    f_mu_name = _font('bold', px(14))
    f_mu_record = _font('bold', px(11))
    f_pname = _font('regular', px(12))
    f_pos_tag = _font('bold', px(9))
    f_pts = _font('bold', px(14))
    f_proj = _font('regular', px(9))

    ROW_H2 = px(40)
    total_h = HEADER_H + ROW_H2 * len(rows)
    img = Image.new("RGB", (CARD_W, total_h), ROSTER_BG)
    _turf_stripes(img, (0, 0, CARD_W, HEADER_H), TURF_C1, TURF_C2, px(42))
    draw = ImageDraw.Draw(img)

    pad_l, pad_r = px(22), px(22)
    logo_r = px(20)
    label_w = _tw(draw, "BENCH", f_bench_label)
    label_x = (CARD_W - label_w) / 2
    team_y = HEADER_H // 2

    logo1_cx = pad_l + logo_r
    _circle_image(img, t1.get('logo_path'), logo1_cx, team_y, logo_r, border_color=(255, 255, 255, 150), border_w=px(2), fallback_text=t1['name'])
    text1_x = logo1_cx + logo_r + px(10)
    name1 = _ellipsize(draw, t1['name'], f_mu_name, label_x - px(12) - text1_x)
    draw.text((text1_x, team_y - px(16)), name1, font=f_mu_name, fill=WHITE)
    draw.text((text1_x, team_y + px(4)), f"{t1['owner']} · {t1['record']}", font=f_mu_record, fill=TEAM_META)

    logo2_cx = CARD_W - pad_r - logo_r
    _circle_image(img, t2.get('logo_path'), logo2_cx, team_y, logo_r, border_color=(255, 255, 255, 150), border_w=px(2), fallback_text=t2['name'])
    text2_right = logo2_cx - logo_r - px(10)
    name2 = _ellipsize(draw, t2['name'], f_mu_name, text2_right - (label_x + label_w + px(12)))
    n2w = _tw(draw, name2, f_mu_name)
    draw.text((text2_right - n2w, team_y - px(16)), name2, font=f_mu_name, fill=WHITE)
    rec2 = f"{t2['owner']} · {t2['record']}"
    r2w = _tw(draw, rec2, f_mu_record)
    draw.text((text2_right - r2w, team_y + px(4)), rec2, font=f_mu_record, fill=TEAM_META)

    draw.text((label_x, team_y - px(8)), "BENCH", font=f_bench_label, fill=WHITE)

    col_badge_w, col_avatar_w, col_pts_w, col_div_w = px(30), px(24), px(44), px(12)
    row_pad_l, row_pad_r = px(8), px(22)
    gap6 = px(6)
    fixed_w = col_badge_w + col_avatar_w + col_pts_w + col_div_w + col_pts_w + col_avatar_w
    inner_w = CARD_W - row_pad_l - row_pad_r
    name_col_w = (inner_w - fixed_w - gap6 * 7) / 2

    x_badge = row_pad_l
    x_avatar1 = x_badge + col_badge_w + gap6
    x_name1 = x_avatar1 + col_avatar_w + gap6
    x_pts1 = x_name1 + name_col_w + gap6
    x_div = x_pts1 + col_pts_w + gap6
    x_pts2 = x_div + col_div_w + gap6
    x_name2 = x_pts2 + col_pts_w + gap6
    x_avatar2 = x_name2 + name_col_w + gap6

    EMPTY_SIDE = {'name': '', 'position': '', 'headshot_path': None, 'is_logo': False, 'actual': 0.0, 'proj': 0.0}

    def draw_side(x_name, x_avatar, row, align_right, y, h):
        is_dst = row.get('is_logo', False)
        cx = x_avatar + col_avatar_w // 2
        _circle_image(img, row.get('headshot_path'), cx, y + h // 2, col_avatar_w // 2,
                      border_color=HEADSHOT_BORDER, border_w=max(1, px(0.5)),
                      fallback_text=row['name'] or '?', contain=is_dst)
        if not row['name']:
            return
        status = row.get('status')
        tag_w = _tw(draw, row['position'], f_pos_tag) + px(10)
        status_w = (_tw(draw, status, f_pos_tag) + px(10) + px(4)) if status else 0
        name_avail = name_col_w - tag_w - status_w - px(4)
        name_txt = _ellipsize(draw, row['name'], f_pname, name_avail)
        name_y = y + h // 2 - px(7)
        status_fg, status_bg = (STATUS_Q_FG, STATUS_Q_BG) if status == 'Q' else (STATUS_OUT_FG, STATUS_OUT_BG)
        if not align_right:
            draw.text((x_name, name_y), name_txt, font=f_pname, fill=PTS_DARK)
            tag_x = x_name + _tw(draw, name_txt, f_pname) + px(4)
            tag_x = _chip(draw, tag_x, name_y - px(1), row['position'], f_pos_tag, POS_BADGE_FG, POS_BADGE_BG)
            if status:
                _chip(draw, tag_x + px(4), name_y - px(1), status, f_pos_tag, status_fg, status_bg)
        else:
            nw = _tw(draw, name_txt, f_pname)
            tag_x = x_name + name_col_w - tag_w - status_w
            name_x = tag_x - px(4) - nw
            draw.text((name_x, name_y), name_txt, font=f_pname, fill=PTS_DARK)
            tag_x = _chip(draw, tag_x, name_y - px(1), row['position'], f_pos_tag, POS_BADGE_FG, POS_BADGE_BG)
            if status:
                _chip(draw, tag_x + px(4), name_y - px(1), status, f_pos_tag, status_fg, status_bg)

    def draw_pts(x, row, y, h):
        if not row['name']:
            return
        pts_str = f"{row['actual']:.1f}"
        pw = _tw(draw, pts_str, f_pts)
        draw.text((x + (col_pts_w - pw) / 2, y + h // 2 - px(13)), pts_str, font=f_pts, fill=PTS_DARK)
        proj_str = f"({row['proj']:.1f})"
        prw = _tw(draw, proj_str, f_proj)
        draw.text((x + (col_pts_w - prw) / 2, y + h // 2 + px(3)), proj_str, font=f_proj, fill=MU_MUTED)

    y = HEADER_H
    for i, (l, r) in enumerate(rows):
        l, r = l or EMPTY_SIDE, r or EMPTY_SIDE
        if i % 2 == 1:
            draw.rectangle((0, y, CARD_W, y + ROW_H2), fill=ROSTER_ZEBRA)
        slot = l['slot'] if l['name'] else (r['slot'] if r['name'] else 'BE')
        bw = _tw(draw, slot, f_pos_tag)
        draw.rounded_rectangle((x_badge, y + ROW_H2 // 2 - px(10), x_badge + col_badge_w, y + ROW_H2 // 2 + px(10)), radius=px(3), fill=POS_BADGE_BG)
        draw.text((x_badge + (col_badge_w - bw) / 2, y + ROW_H2 // 2 - px(7)), slot, font=f_pos_tag, fill=POS_BADGE_FG)
        draw_side(x_name1, x_avatar1, l, False, y, ROW_H2)
        draw_pts(x_pts1, l, y, ROW_H2)
        draw.line((x_div + col_div_w / 2, y + px(6), x_div + col_div_w / 2, y + ROW_H2 - px(6)), fill=HEADSHOT_BORDER, width=max(1, px(0.5)))
        draw_pts(x_pts2, r, y, ROW_H2)
        draw_side(x_name2, x_avatar2, r, True, y, ROW_H2)
        y += ROW_H2

    return _finalize(img)


STATUS_LIVE_FG = (71, 103, 163)
STATUS_MUTED_FG = (163, 156, 143)
SCORE_PENDING = (194, 188, 174)


def render_scoreboard_card(data: dict) -> io.BytesIO:
    """Data contract:
    {
      'league_name': str, 'matchup_count': int, 'scoring_label': str,
      'live_games_count': int, 'week_label': str ("Week 17"),
      'matchups': [MATCHUP, ...],
    }
    MATCHUP = {
      'left': {'name','owner','record','logo_path','score': float or None, 'win': bool or None},
      'right': {same},
      'status': 'live' or 'final' or 'tbd', 'clock': str or None,
    }
    """
    matchups = data['matchups']

    f_league_name = _font('impact', px(26))
    f_league_meta = _font('bold', px(11))
    f_week_label = _font('impact', px(22))
    f_header_sub = _font('bold', px(11))
    f_live_badge = _font('bold', px(10))
    f_sb_name = _font('bold', px(12.5))
    f_sb_owner = _font('regular', px(10.5))
    f_sb_score = _font('impact', px(19))
    f_status_label = _font('bold', px(9))
    f_status_clock = _font('regular', px(9))

    HEADER_H = px(78)
    ROW_H = px(58)
    total_h = HEADER_H + ROW_H * len(matchups)
    img = Image.new("RGB", (CARD_W, total_h), ROSTER_BG)
    _turf_stripes(img, (0, 0, CARD_W, HEADER_H), TURF_C1, TURF_C2, px(42))
    draw = ImageDraw.Draw(img)

    pad_l, pad_r = px(22), px(22)
    logo_r = px(23)
    logo_cx, logo_cy = pad_l + logo_r, HEADER_H // 2
    _circle_image(img, None, logo_cx, logo_cy, logo_r, border_color=(255, 255, 255, 150), border_w=px(2),
                  fallback_text=data['league_name'])

    text_x = logo_cx + logo_r + px(12)
    name_y = HEADER_H // 2 - px(20)
    draw.text((text_x, name_y), data['league_name'].upper(), font=f_league_name, fill=WHITE)
    meta = f"{data['matchup_count']} MATCHUPS  ·  {data['scoring_label'].upper()}"
    draw.text((text_x, name_y + px(30)), meta, font=f_league_meta, fill=TEAM_META)

    live_count = data.get('live_games_count', 0)
    if live_count > 0:
        label = f"{live_count} Games Live"
        lw = _tw(draw, label, f_live_badge)
        dot_r = px(3)
        badge_w = dot_r * 2 + px(5) + lw + px(12)
        bx = CARD_W - pad_r - badge_w
        by = HEADER_H // 2 - px(20)
        draw.rounded_rectangle((bx, by, bx + badge_w, by + px(18)), radius=px(3), fill=LIVE_BADGE_BG)
        draw.ellipse((bx + px(9) - dot_r, by + px(9) - dot_r, bx + px(9) + dot_r, by + px(9) + dot_r), fill=LIVE_BADGE_DOT)
        draw.text((bx + px(9) + dot_r + px(5), by + px(4)), label, font=f_live_badge, fill=LIVE_BADGE_FG)
        sub_w = _tw(draw, data['week_label'], f_header_sub)
        draw.text((CARD_W - pad_r - sub_w, by + px(24)), data['week_label'], font=f_header_sub, fill=HEADER_SUB)
    else:
        ww = _tw(draw, data['week_label'], f_week_label)
        draw.text((CARD_W - pad_r - ww, HEADER_H // 2 - px(16)), data['week_label'], font=f_week_label, fill=WHITE)

    # ---- column geometry, mirrored ----
    col_logo_w, col_score_w, col_status_w = px(26), px(44), px(40)
    row_pad_l, row_pad_r = px(8), px(22)
    gap = px(8)
    fixed_w = col_logo_w + col_score_w + col_status_w + col_score_w + col_logo_w
    inner_w = CARD_W - row_pad_l - row_pad_r
    name_col_w = (inner_w - fixed_w - gap * 6) / 2

    x_logo1 = row_pad_l
    x_name1 = x_logo1 + col_logo_w + gap
    x_score1 = x_name1 + name_col_w + gap
    x_status = x_score1 + col_score_w + gap
    x_score2 = x_status + col_status_w + gap
    x_name2 = x_score2 + col_score_w + gap
    x_logo2 = x_name2 + name_col_w + gap

    y = HEADER_H
    for i, m in enumerate(matchups):
        base_bg = ROSTER_ZEBRA if i % 2 == 1 else ROSTER_BG
        if m['status'] == 'live':
            # Faint blue tint over whatever zebra shade this row would have had.
            row_bg = Image.blend(Image.new("RGB", (CARD_W, ROW_H), base_bg), Image.new("RGB", (CARD_W, ROW_H), LIVE_DOT_BLUE), 0.07)
            img.paste(row_bg, (0, y))
        else:
            draw.rectangle((0, y, CARD_W, y + ROW_H), fill=base_bg)
        _dashed_vline(draw, 0, y, y + ROW_H, DASH_SIDELINE, width=max(1, px(1.5)))

        left, right = m['left'], m['right']
        cy = y + ROW_H // 2

        _circle_image(img, left.get('logo_path'), x_logo1 + col_logo_w // 2, cy, col_logo_w // 2,
                      border_color=HEADSHOT_BORDER, border_w=max(1, px(0.5)), fallback_text=left['name'])
        _circle_image(img, right.get('logo_path'), x_logo2 + col_logo_w // 2, cy, col_logo_w // 2,
                      border_color=HEADSHOT_BORDER, border_w=max(1, px(0.5)), fallback_text=right['name'])

        name1 = _ellipsize(draw, left['name'], f_sb_name, name_col_w)
        draw.text((x_name1, y + px(12)), name1, font=f_sb_name, fill=PTS_DARK)
        draw.text((x_name1, y + px(31)), f"{left['owner']} · {left['record']}", font=f_sb_owner, fill=PTS_MUTED)

        name2 = _ellipsize(draw, right['name'], f_sb_name, name_col_w)
        n2w = _tw(draw, name2, f_sb_name)
        draw.text((x_name2 + name_col_w - n2w, y + px(12)), name2, font=f_sb_name, fill=PTS_DARK)
        rec2 = f"{right['owner']} · {right['record']}"
        r2w = _tw(draw, rec2, f_sb_owner)
        draw.text((x_name2 + name_col_w - r2w, y + px(31)), rec2, font=f_sb_owner, fill=PTS_MUTED)

        def score_color(side):
            if side.get('score') is None:
                return SCORE_PENDING
            return PTS_DARK if side.get('win') else (PTS_MUTED if side.get('win') is False else PTS_DARK)

        s1_str = f"{left['score']:.1f}" if left.get('score') is not None else "–"
        s1w = _tw(draw, s1_str, f_sb_score)
        draw.text((x_score1 + (col_score_w - s1w) / 2, cy - px(11)), s1_str, font=f_sb_score, fill=score_color(left))

        s2_str = f"{right['score']:.1f}" if right.get('score') is not None else "–"
        s2w = _tw(draw, s2_str, f_sb_score)
        draw.text((x_score2 + (col_score_w - s2w) / 2, cy - px(11)), s2_str, font=f_sb_score, fill=score_color(right))

        status = m['status']
        if status == 'live':
            label = "LIVE"
            lw = _tw(draw, label, f_status_label)
            dot_r = px(3)
            total_w = dot_r * 2 + px(4) + lw
            lx = x_status + (col_status_w - total_w) / 2
            ly = cy - px(9)
            draw.ellipse((lx, ly + px(4) - dot_r, lx + dot_r * 2, ly + px(4) + dot_r), fill=LIVE_DOT_BLUE)
            draw.text((lx + dot_r * 2 + px(4), ly), label, font=f_status_label, fill=STATUS_LIVE_FG)
            clock = m.get('clock') or ''
            cw = _tw(draw, clock, f_status_clock)
            draw.text((x_status + (col_status_w - cw) / 2, cy + px(2)), clock, font=f_status_clock, fill=PTS_MUTED)
        else:
            label = "END" if status == 'final' else "TBD"
            lw = _tw(draw, label, f_status_label)
            draw.text((x_status + (col_status_w - lw) / 2, cy - px(4)), label, font=f_status_label, fill=STATUS_MUTED_FG)

        y += ROW_H

    return _finalize(img)


STATUS_ACTIVE_BG = (0, 0, 0, 71)
STATUS_ACTIVE_FG = (184, 240, 196)


def render_player_card(data: dict) -> io.BytesIO:
    """Data contract:
    {
      'name': str, 'position': str, 'pro_team': str, 'status': str ("Active"/"Questionable"/"Out"),
      'headshot_path': str or None, 'team_logo_path': str or None,
      'ppg': float, 'total_points': float, 'games_played': int,
      'highlight_label': str or None ("Week 17"), 'highlight_text': str or None
          ("42.9 pts (proj 23.7) · benched by Tyler's Mediocre team") -- omit both to skip the strip,
      'stats': [{'value': str, 'label': str}, ...] up to 6, position-specific
          fields chosen by the caller (this renderer doesn't know about positions),
      'fantasy_team': str or None (None = "Free Agent"), 'roster_slot': str or None,
    }
    """
    f_name = _font('impact', px(28))
    f_sub_text = _font('bold', px(12))
    f_status = _font('bold', px(9))
    f_ppg = _font('impact', px(32))
    f_ppg_label = _font('bold', px(10))
    f_ppg_sub = _font('bold', px(11))
    f_hl_label = _font('bold', px(11))
    f_hl_text = _font('regular', px(12.5))
    f_section_hdr = _font('bold', px(10))
    f_stat_val = _font('impact', px(20))
    f_stat_label = _font('bold', px(9.5))
    f_footer = _font('regular', px(11))
    f_footer_b = _font('bold', px(11))
    f_badge = _font('bold', px(10))

    HEADER_H = px(100)
    HIGHLIGHT_H = px(38) if data.get('highlight_text') else 0
    SECTION_HDR_H = px(26)
    stats = data.get('stats', [])
    n_rows = (len(stats) + 2) // 3
    TILE_ROW_H = px(48)
    FOOTER_H = px(38)

    total_h = HEADER_H + HIGHLIGHT_H + SECTION_HDR_H + TILE_ROW_H * n_rows + px(14) + FOOTER_H
    img = Image.new("RGB", (CARD_W, total_h), ROSTER_BG)
    _turf_stripes(img, (0, 0, CARD_W, HEADER_H), TURF_C1, TURF_C2, px(42))
    draw = ImageDraw.Draw(img)

    pad_l, pad_r = px(22), px(22)
    headshot_r = px(32)
    hs_cx, hs_cy = pad_l + headshot_r, HEADER_H // 2
    _circle_image(img, data.get('headshot_path'), hs_cx, hs_cy, headshot_r,
                  border_color=(255, 255, 255, 150), border_w=px(2), fallback_text=data['name'])

    text_x = hs_cx + headshot_r + px(14)
    draw.text((text_x, HEADER_H // 2 - px(26)), data['name'].upper(), font=f_name, fill=WHITE)
    sub_y = HEADER_H // 2 + px(8)
    logo_size = px(18)
    if data.get('team_logo_path'):
        _circle_image(img, data['team_logo_path'], text_x + logo_size // 2, sub_y + logo_size // 2, logo_size // 2,
                      contain=True, bg=WHITE)
        sub_text_x = text_x + logo_size + px(8)
    else:
        sub_text_x = text_x
    draw.text((sub_text_x, sub_y), f"{data['position']} · {data['pro_team']}", font=f_sub_text, fill=TEAM_META)
    status_x = sub_text_x + _tw(draw, f"{data['position']} · {data['pro_team']}", f_sub_text) + px(10)
    status_text = data.get('status', 'Active')
    sw = _tw(draw, status_text, f_status)
    sbg_w = sw + px(12)
    draw.rounded_rectangle((status_x, sub_y - px(1), status_x + sbg_w, sub_y + px(14)), radius=px(3), fill=(16, 15, 12))
    draw.text((status_x + px(6), sub_y + px(1)), status_text, font=f_status, fill=STATUS_ACTIVE_FG)

    ppg_str = f"{data['ppg']:.1f}"
    pw = _tw(draw, ppg_str, f_ppg)
    draw.text((CARD_W - pad_r - pw, HEADER_H // 2 - px(28)), ppg_str, font=f_ppg, fill=WHITE)
    lw = _tw(draw, "PPG", f_ppg_label)
    draw.text((CARD_W - pad_r - lw, HEADER_H // 2 + px(4)), "PPG", font=f_ppg_label, fill=TEAM_META)
    sub2 = f"{data['total_points']:.1f} total · {data['games_played']} GP"
    s2w = _tw(draw, sub2, f_ppg_sub)
    draw.text((CARD_W - pad_r - s2w, HEADER_H // 2 + px(17)), sub2, font=f_ppg_sub, fill=TEAM_META)

    y = HEADER_H
    if HIGHLIGHT_H:
        draw.rectangle((0, y, CARD_W, y + HIGHLIGHT_H), fill=TOTAL_ROW_BG)
        draw.rectangle((0, y, CARD_W, y + max(1, px(1))), fill=SECTION_HDR_GREEN)
        _dashed_hline(draw, 0, CARD_W, y + HIGHLIGHT_H, SECTION_HDR_BORDER, width=max(1, px(0.5)))
        draw.text((pad_l, y + HIGHLIGHT_H // 2 - px(6)), data['highlight_label'].upper(), font=f_hl_label, fill=SECTION_HDR_GREEN)
        htw = _tw(draw, data['highlight_text'], f_hl_text)
        draw.text((CARD_W - pad_r - htw, y + HIGHLIGHT_H // 2 - px(7)), data['highlight_text'], font=f_hl_text, fill=(74, 70, 61))
        y += HIGHLIGHT_H

    draw.text((px(8), y + px(8)), "SEASON STATS", font=f_section_hdr, fill=SECTION_HDR_GREEN)
    _dashed_hline(draw, 0, CARD_W, y + SECTION_HDR_H, SECTION_HDR_BORDER, width=max(1, px(0.5)))
    y += SECTION_HDR_H

    tile_w = (CARD_W - pad_l - pad_r) // 3
    for i, stat in enumerate(stats):
        col, row = i % 3, i // 3
        tx = pad_l + col * tile_w
        ty = y + row * TILE_ROW_H
        draw.rectangle((tx, ty, tx + tile_w, ty + TILE_ROW_H), outline=HEADSHOT_BORDER, width=max(1, px(0.5)))
        draw.text((tx + px(12), ty + px(8)), stat['value'], font=f_stat_val, fill=PTS_DARK)
        draw.text((tx + px(12), ty + px(30)), stat['label'].upper(), font=f_stat_label, fill=PTS_MUTED)
    y += TILE_ROW_H * n_rows + px(14)

    draw.rectangle((0, y, CARD_W, y + FOOTER_H), fill=TOTAL_ROW_BG)
    _dashed_hline(draw, 0, CARD_W, y, SECTION_HDR_BORDER, width=max(1, px(0.5)))
    team_label = data.get('fantasy_team') or "Free Agent"
    draw.text((pad_l, y + FOOTER_H // 2 - px(7)), "Rostered by ", font=f_footer, fill=PLAYER_SUB_GRAY)
    rw = _tw(draw, "Rostered by ", f_footer)
    draw.text((pad_l + rw, y + FOOTER_H // 2 - px(7)), team_label, font=f_footer_b, fill=PTS_DARK)
    if data.get('roster_slot'):
        bw = _tw(draw, data['roster_slot'], f_badge) + px(12)
        bx = CARD_W - pad_r - bw
        draw.rounded_rectangle((bx, y + FOOTER_H // 2 - px(9), bx + bw, y + FOOTER_H // 2 + px(9)), radius=px(3), fill=POS_BADGE_BG)
        draw.text((bx + px(6), y + FOOTER_H // 2 - px(6)), data['roster_slot'], font=f_badge, fill=POS_BADGE_FG)

    return _finalize(img)


TAG_GEM_BG = (222, 238, 227)
TAG_GEM_FG = (30, 107, 48)
TAG_POPULAR_BG = (222, 229, 242)
TAG_POPULAR_FG = (71, 103, 163)


def render_player_list_card(data: dict) -> io.BytesIO:
    """Generic ranked player list -- shared by /sleeper and /waiver, which
    differ only in framing (a second metric column vs. a single metric plus
    an acquisition tag), not in structure.

    Data contract:
    {
      'title': str ("Sleeper Picks"), 'league_name': str, 'subtitle': str ("All Positions"),
      'header_right_val': str or None, 'header_right_sub': str or None,
      'players': [PLAYER, ...],
      'footer_label': str or None, 'footer_value': str or None,
    }
    PLAYER = {
      'rank': int, 'name': str, 'position': str, 'sub': str ("ATL · 5.4% owned"),
      'headshot_path': str or None,
      'metric1_val': str, 'metric1_label': str,
      'metric2_val': str or None, 'metric2_label': str or None,
      'tag': str or None, 'tag_type': 'gem' or 'popular' or None,
    }
    """
    players = data['players']

    f_title = _font('impact', px(26))
    f_meta = _font('bold', px(11))
    f_header_val = _font('impact', px(20))
    f_header_sub = _font('bold', px(11))
    f_section_hdr = _font('bold', px(10))
    f_rank = _font('impact', px(14))
    f_name = _font('bold', px(13))
    f_pos_tag = _font('bold', px(9.5))
    f_sub = _font('regular', px(10.5))
    f_metric_val = _font('impact', px(15))
    f_metric_label = _font('regular', px(9))
    f_tag = _font('bold', px(8.5))
    f_footer_label = _font('bold', px(11))
    f_footer_val = _font('regular', px(13))

    HEADER_H = px(78)
    SECTION_HDR_H = px(26)
    ROW_H = px(44)
    FOOTER_H = px(38) if data.get('footer_label') else 0

    total_h = HEADER_H + SECTION_HDR_H + ROW_H * len(players) + FOOTER_H
    img = Image.new("RGB", (CARD_W, total_h), ROSTER_BG)
    _turf_stripes(img, (0, 0, CARD_W, HEADER_H), TURF_C1, TURF_C2, px(42))
    draw = ImageDraw.Draw(img)

    pad_l, pad_r = px(22), px(22)
    logo_r = px(23)
    logo_cx, logo_cy = pad_l + logo_r, HEADER_H // 2
    _circle_image(img, None, logo_cx, logo_cy, logo_r, border_color=(255, 255, 255, 150), border_w=px(2), fallback_text=data['title'])
    text_x = logo_cx + logo_r + px(12)
    draw.text((text_x, HEADER_H // 2 - px(20)), data['title'].upper(), font=f_title, fill=WHITE)
    meta = f"{data['league_name'].upper()}  ·  {data['subtitle'].upper()}"
    draw.text((text_x, HEADER_H // 2 + px(10)), meta, font=f_meta, fill=TEAM_META)

    if data.get('header_right_val'):
        rw = _tw(draw, data['header_right_val'], f_header_val)
        draw.text((CARD_W - pad_r - rw, HEADER_H // 2 - px(18)), data['header_right_val'], font=f_header_val, fill=WHITE)
        sw = _tw(draw, data['header_right_sub'], f_header_sub)
        draw.text((CARD_W - pad_r - sw, HEADER_H // 2 + px(6)), data['header_right_sub'], font=f_header_sub, fill=HEADER_SUB)

    y = HEADER_H
    draw.text((px(8), y + px(8)), "PLAYER", font=f_section_hdr, fill=SECTION_HDR_GREEN)
    _dashed_hline(draw, 0, CARD_W, y + SECTION_HDR_H, SECTION_HDR_BORDER, width=max(1, px(0.5)))
    y += SECTION_HDR_H

    row_pad_l = px(8)
    rank_w, avatar_r = px(20), px(16)
    metric_w = px(56)
    x_rank = row_pad_l
    x_avatar = x_rank + rank_w + px(8)
    x_name = x_avatar + avatar_r * 2 + px(8)

    for i, p in enumerate(players):
        if i % 2 == 1:
            draw.rectangle((0, y, CARD_W, y + ROW_H), fill=ROSTER_ZEBRA)
        rs = str(p['rank'])
        rsw = _tw(draw, rs, f_rank)
        draw.text((x_rank + (rank_w - rsw) / 2, y + ROW_H // 2 - px(9)), rs, font=f_rank, fill=PTS_MUTED)

        _circle_image(img, p.get('headshot_path'), x_avatar + avatar_r, y + ROW_H // 2, avatar_r,
                      border_color=HEADSHOT_BORDER, border_w=max(1, px(0.5)), fallback_text=p['name'])

        # Reserve space on the right for tag + up to two metric columns before
        # letting the name run, so long names truncate instead of colliding.
        reserved = metric_w + px(10)
        if p.get('metric2_val') is not None:
            reserved += metric_w + px(10)
        if p.get('tag'):
            reserved += px(70)
        name_max_w = CARD_W - pad_r - reserved - x_name
        name_txt = _ellipsize(draw, p['name'], f_name, name_max_w)
        draw.text((x_name, y + px(9)), name_txt, font=f_name, fill=PTS_DARK)
        nx = x_name + _tw(draw, name_txt, f_name) + px(6)
        _chip(draw, nx, y + px(10), p['position'], f_pos_tag, POS_BADGE_FG, POS_BADGE_BG)
        draw.text((x_name, y + px(25)), p['sub'], font=f_sub, fill=PTS_MUTED)

        mx = CARD_W - pad_r
        if p.get('tag'):
            tag_bg, tag_fg = (TAG_GEM_BG, TAG_GEM_FG) if p['tag_type'] == 'gem' else (TAG_POPULAR_BG, TAG_POPULAR_FG)
            tw_ = _tw(draw, p['tag'].upper(), f_tag) + px(12)
            th_ = px(15)
            tag_x = mx - tw_
            tag_y = y + ROW_H // 2 - th_ // 2
            draw.rounded_rectangle((tag_x, tag_y, tag_x + tw_, tag_y + th_), radius=px(3), fill=tag_bg)
            draw.text((tag_x + px(6), tag_y + px(2)), p['tag'].upper(), font=f_tag, fill=tag_fg)
            mx -= tw_ + px(10)

        if p.get('metric2_val') is not None:
            m2x = mx - metric_w
            v2w = _tw(draw, p['metric2_val'], f_metric_val)
            draw.text((m2x + (metric_w - v2w) / 2, y + ROW_H // 2 - px(15)), p['metric2_val'], font=f_metric_val, fill=PTS_MUTED)
            l2w = _tw(draw, p['metric2_label'].upper(), f_metric_label)
            draw.text((m2x + (metric_w - l2w) / 2, y + ROW_H // 2 + px(3)), p['metric2_label'].upper(), font=f_metric_label, fill=MU_MUTED)
            mx = m2x - px(10)

        m1x = mx - metric_w
        v1w = _tw(draw, p['metric1_val'], f_metric_val)
        draw.text((m1x + (metric_w - v1w) / 2, y + ROW_H // 2 - px(15)), p['metric1_val'], font=f_metric_val, fill=PTS_DARK)
        l1w = _tw(draw, p['metric1_label'].upper(), f_metric_label)
        draw.text((m1x + (metric_w - l1w) / 2, y + ROW_H // 2 + px(3)), p['metric1_label'].upper(), font=f_metric_label, fill=MU_MUTED)

        y += ROW_H

    if FOOTER_H:
        draw.rectangle((0, y, CARD_W, y + FOOTER_H), fill=TOTAL_ROW_BG)
        draw.rectangle((0, y, CARD_W, y + max(1, px(1))), fill=SECTION_HDR_GREEN)
        draw.text((pad_l, y + FOOTER_H // 2 - px(6)), data['footer_label'].upper(), font=f_footer_label, fill=SECTION_HDR_GREEN)
        fvw = _tw(draw, data['footer_value'], f_footer_val)
        draw.text((CARD_W - pad_r - fvw, y + FOOTER_H // 2 - px(7)), data['footer_value'], font=f_footer_val, fill=PTS_DARK)

    return _finalize(img)


def render_compare_card(data: dict) -> io.BytesIO:
    """Season-long two-team comparison -- shared by /compare and
    /compare_cross_league (the latter just sets each team's 'league' field
    and a series_note flagging the cross-league caveat).

    Data contract:
    {
      'team1': {'name','owner','record','logo_path','league': str or None},
      'team2': {same},
      'series_note': str or None ("CeeDeez Nutz leads season series 2-0"),
      'series_note_warn': bool (True renders the note in the warning color),
      'rows': [{'label': str, 'left_val': str, 'right_val': str,
                'left_win': bool or None, 'right_win': bool or None}, ...],
    }
    """
    f_vs_name = _font('bold', px(14))
    f_vs_record = _font('bold', px(11))
    f_vs_mid = _font('impact', px(16))
    f_series = _font('bold', px(10.5))
    f_cmp_val = _font('impact', px(16))
    f_cmp_label = _font('bold', px(9.5))

    HEADER_H = px(92) if data.get('series_note') else px(76)
    ROW_H = px(38)
    rows = data['rows']
    total_h = HEADER_H + ROW_H * len(rows)
    img = Image.new("RGB", (CARD_W, total_h), ROSTER_BG)
    _turf_stripes(img, (0, 0, CARD_W, HEADER_H), TURF_C1, TURF_C2, px(42))
    draw = ImageDraw.Draw(img)

    pad_l, pad_r = px(22), px(22)
    logo_r = px(21)
    team_y = px(38)
    t1, t2 = data['team1'], data['team2']

    logo1_cx = pad_l + logo_r
    _circle_image(img, t1.get('logo_path'), logo1_cx, team_y, logo_r, border_color=(255, 255, 255, 150), border_w=px(2), fallback_text=t1['name'])
    text1_x = logo1_cx + logo_r + px(10)

    logo2_cx = CARD_W - pad_r - logo_r
    _circle_image(img, t2.get('logo_path'), logo2_cx, team_y, logo_r, border_color=(255, 255, 255, 150), border_w=px(2), fallback_text=t2['name'])
    text2_right = logo2_cx - logo_r - px(10)

    mid_w = _tw(draw, "VS", f_vs_mid)
    mid_x = (CARD_W - mid_w) / 2
    draw.text((mid_x, team_y - px(9)), "VS", font=f_vs_mid, fill=(255, 255, 255, 110))

    name1 = _ellipsize(draw, t1['name'], f_vs_name, mid_x - px(12) - text1_x)
    draw.text((text1_x, team_y - px(16)), name1, font=f_vs_name, fill=WHITE)
    rec1 = f"{t1['owner']} · {t1['record']}" + (f" · {t1['league']}" if t1.get('league') else '')
    draw.text((text1_x, team_y + px(4)), rec1, font=f_vs_record, fill=TEAM_META)

    name2 = _ellipsize(draw, t2['name'], f_vs_name, text2_right - (mid_x + mid_w + px(12)))
    n2w = _tw(draw, name2, f_vs_name)
    draw.text((text2_right - n2w, team_y - px(16)), name2, font=f_vs_name, fill=WHITE)
    rec2 = f"{t2['owner']} · {t2['record']}" + (f" · {t2['league']}" if t2.get('league') else '')
    r2w = _tw(draw, rec2, f_vs_record)
    draw.text((text2_right - r2w, team_y + px(4)), rec2, font=f_vs_record, fill=TEAM_META)

    if data.get('series_note'):
        sw = _tw(draw, data['series_note'], f_series)
        color = LIVE_BADGE_FG if data.get('series_note_warn') else TEAM_META
        draw.text(((CARD_W - sw) / 2, HEADER_H - px(24)), data['series_note'], font=f_series, fill=color)

    y = HEADER_H
    col_w = px(90)
    for i, row in enumerate(rows):
        if i % 2 == 1:
            draw.rectangle((0, y, CARD_W, y + ROW_H), fill=ROSTER_ZEBRA)
        l_color = PTS_DARK if row.get('left_win') else (MU_MUTED if row.get('left_win') is False else PTS_DARK)
        r_color = PTS_DARK if row.get('right_win') else (MU_MUTED if row.get('right_win') is False else PTS_DARK)
        draw.text((pad_l, y + ROW_H // 2 - px(9)), row['left_val'], font=f_cmp_val, fill=l_color)
        rw_ = _tw(draw, row['right_val'], f_cmp_val)
        draw.text((CARD_W - pad_r - rw_, y + ROW_H // 2 - px(9)), row['right_val'], font=f_cmp_val, fill=r_color)
        lw_ = _tw(draw, row['label'].upper(), f_cmp_label)
        draw.text(((CARD_W - lw_) / 2, y + ROW_H // 2 - px(5)), row['label'].upper(), font=f_cmp_label, fill=PTS_MUTED)
        y += ROW_H

    return _finalize(img)


FAIRNESS_COLOR = (184, 100, 31)
INJURY_BG = (251, 235, 233)
INJURY_FG = (140, 30, 20)


def render_trade_card(data: dict) -> io.BytesIO:
    """Data contract:
    {
      'team1_name': str, 'team2_name': str, 'team1_logo': str or None,
      'send': [{'name','position','avg': float}, ...], 'receive': [same],
      'send_total': float, 'receive_total': float,
      'fairness_label': str, 'trade_type': str,
      'injury_notes': [str, ...],
    }
    """
    send, receive = data['send'], data['receive']

    f_header_name = _font('bold', px(15))
    f_header_sub = _font('bold', px(10.5))
    f_side_hdr = _font('bold', px(10))
    f_player = _font('regular', px(12.5))
    f_player_pos = _font('regular', px(9.5))
    f_val = _font('regular', px(12))
    f_total_label = _font('regular', px(11))
    f_total_val = _font('impact', px(14))
    f_injury = _font('bold', px(10.5))
    f_fairness = _font('bold', px(11))

    HEADER_H = px(70)
    SIDE_HDR_H = px(30)
    ROW_H = px(26)
    n_rows = max(len(send), len(receive))
    TOTAL_H = px(34)
    INJURY_H = px(28) * len(data.get('injury_notes', []))
    FAIRNESS_H = px(34)

    total_h = HEADER_H + SIDE_HDR_H + ROW_H * n_rows + TOTAL_H + INJURY_H + FAIRNESS_H
    img = Image.new("RGB", (CARD_W, total_h), ROSTER_BG)
    _turf_stripes(img, (0, 0, CARD_W, HEADER_H), TURF_C1, TURF_C2, px(42))
    draw = ImageDraw.Draw(img)

    pad_l, pad_r = px(22), px(22)
    logo_r = px(21)
    logo_cx, logo_cy = pad_l + logo_r, HEADER_H // 2
    _circle_image(img, data.get('team1_logo'), logo_cx, logo_cy, logo_r, border_color=(255, 255, 255, 150), border_w=px(2), fallback_text=data['team1_name'])
    text_x = logo_cx + logo_r + px(12)
    draw.text((text_x, HEADER_H // 2 - px(16)), "TRADE ANALYSIS", font=f_header_name, fill=WHITE)
    sub = f"{data['team1_name']} ↔ {data['team2_name']}"
    draw.text((text_x, HEADER_H // 2 + px(4)), sub.upper(), font=f_header_sub, fill=TEAM_META)

    y = HEADER_H
    mid_x = CARD_W // 2
    draw.line((mid_x, y, mid_x, y + SIDE_HDR_H + ROW_H * n_rows), fill=SECTION_HDR_BORDER, width=max(1, px(0.5)))
    draw.text((pad_l, y + px(8)), f"{data['team1_name']} SENDS".upper(), font=f_side_hdr, fill=SECTION_HDR_GREEN)
    draw.text((mid_x + px(18), y + px(8)), f"{data['team1_name']} RECEIVES".upper(), font=f_side_hdr, fill=SECTION_HDR_GREEN)
    y += SIDE_HDR_H

    for i in range(n_rows):
        if i < len(send):
            p = send[i]
            draw.text((pad_l, y + px(4)), p['position'], font=f_player_pos, fill=PTS_MUTED)
            posw = _tw(draw, p['position'], f_player_pos)
            draw.text((pad_l + posw + px(6), y + px(3)), p['name'], font=f_player, fill=PTS_DARK)
            avgtxt = f"{p['avg']:.1f} avg"
            avgw = _tw(draw, avgtxt, f_val)
            draw.text((mid_x - px(18) - avgw, y + px(4)), avgtxt, font=f_val, fill=PTS_MUTED)
        if i < len(receive):
            p = receive[i]
            x0 = mid_x + px(18)
            draw.text((x0, y + px(4)), p['position'], font=f_player_pos, fill=PTS_MUTED)
            posw = _tw(draw, p['position'], f_player_pos)
            draw.text((x0 + posw + px(6), y + px(3)), p['name'], font=f_player, fill=PTS_DARK)
            avgtxt = f"{p['avg']:.1f} avg"
            avgw = _tw(draw, avgtxt, f_val)
            draw.text((CARD_W - pad_r - avgw, y + px(4)), avgtxt, font=f_val, fill=PTS_MUTED)
        y += ROW_H

    _dashed_hline(draw, 0, CARD_W, y, SECTION_HDR_BORDER, width=max(1, px(0.5)))
    draw.text((pad_l, y + TOTAL_H // 2 - px(6)), "Combined:", font=f_total_label, fill=PTS_MUTED)
    t1 = f"{data['send_total']:.1f} avg/wk"
    draw.text((pad_l + _tw(draw, 'Combined: ', f_total_label), y + TOTAL_H // 2 - px(8)), t1, font=f_total_val, fill=PTS_DARK)
    draw.text((mid_x + px(18), y + TOTAL_H // 2 - px(6)), "Combined:", font=f_total_label, fill=PTS_MUTED)
    t2 = f"{data['receive_total']:.1f} avg/wk"
    draw.text((mid_x + px(18) + _tw(draw, 'Combined: ', f_total_label), y + TOTAL_H // 2 - px(8)), t2, font=f_total_val, fill=PTS_DARK)
    y += TOTAL_H

    for note in data.get('injury_notes', []):
        draw.rectangle((0, y, CARD_W, y + px(28)), fill=INJURY_BG)
        draw.rectangle((0, y, CARD_W, y + max(1, px(0.5))), fill=SECTION_HDR_BORDER)
        nw = _tw(draw, note, f_injury)
        draw.text(((CARD_W - nw) / 2, y + px(6)), note, font=f_injury, fill=INJURY_FG)
        y += px(28)

    draw.rectangle((0, y, CARD_W, y + FAIRNESS_H), fill=TOTAL_ROW_BG)
    draw.rectangle((0, y, CARD_W, y + max(1, px(2))), fill=SECTION_HDR_GREEN)
    label = f"{data['fairness_label']} · {data['trade_type']}"
    lw = _tw(draw, label.upper(), f_fairness)
    draw.text(((CARD_W - lw) / 2, y + FAIRNESS_H // 2 - px(6)), label.upper(), font=f_fairness, fill=FAIRNESS_COLOR)

    return _finalize(img)


def render_stat_tiles_card(data: dict) -> io.BytesIO:
    """League-superlatives grid for /stats.

    Data contract:
    {
      'title': str ("League Stats"), 'league_name': str, 'subtitle': str ("Full Season"),
      'tiles': [{'label': str, 'team': str, 'value': str, 'sub': str or None}, ...],
    }
    """
    tiles = data['tiles']

    f_title = _font('impact', px(26))
    f_meta = _font('bold', px(11))
    f_tile_label = _font('bold', px(9.5))
    f_tile_team = _font('bold', px(13))
    f_tile_val = _font('impact', px(17))
    f_tile_sub = _font('regular', px(10))

    HEADER_H = px(78)
    n_cols = 2
    n_rows = (len(tiles) + n_cols - 1) // n_cols
    TILE_H = px(68)

    total_h = HEADER_H + TILE_H * n_rows
    img = Image.new("RGB", (CARD_W, total_h), ROSTER_BG)
    _turf_stripes(img, (0, 0, CARD_W, HEADER_H), TURF_C1, TURF_C2, px(42))
    draw = ImageDraw.Draw(img)

    pad_l, pad_r = px(22), px(22)
    logo_r = px(23)
    logo_cx, logo_cy = pad_l + logo_r, HEADER_H // 2
    _circle_image(img, None, logo_cx, logo_cy, logo_r, border_color=(255, 255, 255, 150), border_w=px(2), fallback_text=data['league_name'])
    text_x = logo_cx + logo_r + px(12)
    draw.text((text_x, HEADER_H // 2 - px(20)), data['title'].upper(), font=f_title, fill=WHITE)
    meta = f"{data['league_name'].upper()}  ·  {data['subtitle'].upper()}"
    draw.text((text_x, HEADER_H // 2 + px(10)), meta, font=f_meta, fill=TEAM_META)

    tile_w = (CARD_W - pad_l - pad_r) // n_cols
    for i, t in enumerate(tiles):
        col, row = i % n_cols, i // n_cols
        tx = pad_l + col * tile_w
        ty = HEADER_H + row * TILE_H
        draw.rectangle((tx, ty, tx + tile_w, ty + TILE_H), outline=HEADSHOT_BORDER, width=max(1, px(0.5)))
        draw.text((tx + px(14), ty + px(8)), t['label'].upper(), font=f_tile_label, fill=SECTION_HDR_GREEN)
        draw.text((tx + px(14), ty + px(22)), t['team'], font=f_tile_team, fill=PTS_DARK)
        vx = tx + px(14)
        draw.text((vx, ty + px(42)), t['value'], font=f_tile_val, fill=PTS_DARK)
        if t.get('sub'):
            vw = _tw(draw, t['value'], f_tile_val)
            draw.text((vx + vw + px(6), ty + px(48)), t['sub'], font=f_tile_sub, fill=PTS_MUTED)

    return _finalize(img)


def render_power_rankings_card(data: dict) -> io.BytesIO:
    """Data contract:
    {
      'league_name': str, 'total_points': float, 'avg_ppg': float,
      'rankings': [{'rank': int, 'name': str, 'record': str, 'ppg': float, 'power': float}, ...],
      'footer_label': str or None, 'footer_value': str or None,
    }
    Bar width is power / rankings[0].power -- an honest relative scale, not
    a fixed 0-1 range that would make every top-5 board look maxed out.
    """
    rankings = data['rankings']
    max_power = max(r['power'] for r in rankings) if rankings else 1

    f_title = _font('impact', px(26))
    f_meta = _font('bold', px(11))
    f_section_hdr = _font('bold', px(10))
    f_rank = _font('impact', px(15))
    f_name = _font('bold', px(13))
    f_record = _font('regular', px(10.5))
    f_power_val = _font('regular', px(11))
    f_footer_label = _font('bold', px(11))
    f_footer_val = _font('regular', px(13))

    HEADER_H = px(78)
    SECTION_HDR_H = px(26)
    ROW_H = px(46)
    FOOTER_H = px(38) if data.get('footer_label') else 0

    total_h = HEADER_H + SECTION_HDR_H + ROW_H * len(rankings) + FOOTER_H
    img = Image.new("RGB", (CARD_W, total_h), ROSTER_BG)
    _turf_stripes(img, (0, 0, CARD_W, HEADER_H), TURF_C1, TURF_C2, px(42))
    draw = ImageDraw.Draw(img)

    pad_l, pad_r = px(22), px(22)
    logo_r = px(23)
    logo_cx, logo_cy = pad_l + logo_r, HEADER_H // 2
    _circle_image(img, None, logo_cx, logo_cy, logo_r, border_color=(255, 255, 255, 150), border_w=px(2), fallback_text=data['league_name'])
    text_x = logo_cx + logo_r + px(12)
    draw.text((text_x, HEADER_H // 2 - px(20)), "POWER RANKINGS".upper(), font=f_title, fill=WHITE)
    meta = f"{data['league_name'].upper()}  ·  {data['total_points']:.1f} TOTAL PTS  ·  {data['avg_ppg']:.1f} AVG PPG"
    draw.text((text_x, HEADER_H // 2 + px(10)), meta, font=f_meta, fill=TEAM_META)

    y = HEADER_H
    draw.text((px(8), y + px(8)), "TEAM", font=f_section_hdr, fill=SECTION_HDR_GREEN)
    pw = _tw(draw, "POWER", f_section_hdr)
    draw.text((CARD_W - px(22) - pw, y + px(8)), "POWER", font=f_section_hdr, fill=SECTION_HDR_GREEN)
    _dashed_hline(draw, 0, CARD_W, y + SECTION_HDR_H, SECTION_HDR_BORDER, width=max(1, px(0.5)))
    y += SECTION_HDR_H

    rank_w = px(22)
    x_rank = px(8)
    x_name = x_rank + rank_w + px(8)
    bar_w = px(90)
    power_val_w = px(44)
    bar_x = CARD_W - pad_r - bar_w
    val_x = bar_x - px(10) - power_val_w

    for i, r in enumerate(rankings):
        if i % 2 == 1:
            draw.rectangle((0, y, CARD_W, y + ROW_H), fill=ROSTER_ZEBRA)
        rs = str(r['rank'])
        rsw = _tw(draw, rs, f_rank)
        draw.text((x_rank + (rank_w - rsw) / 2, y + ROW_H // 2 - px(9)), rs, font=f_rank, fill=PTS_MUTED)
        draw.text((x_name, y + px(8)), r['name'], font=f_name, fill=PTS_DARK)
        draw.text((x_name, y + px(25)), f"{r['record']} · {r['ppg']:.1f} ppg", font=f_record, fill=PTS_MUTED)

        val_str = f"{r['power']:.3f}"
        vw = _tw(draw, val_str, f_power_val)
        draw.text((val_x + (power_val_w - vw) / 2, y + ROW_H // 2 - px(7)), val_str, font=f_power_val, fill=POS_BADGE_FG)

        bar_h = px(14)
        bar_y = y + ROW_H // 2 - bar_h // 2
        draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=px(3), fill=POS_BADGE_BG)
        fill_w = int(bar_w * min(1.0, r['power'] / max_power))
        if fill_w > 0:
            draw.rounded_rectangle((bar_x, bar_y, bar_x + fill_w, bar_y + bar_h), radius=px(3), fill=SECTION_HDR_GREEN)
        y += ROW_H

    if FOOTER_H:
        draw.rectangle((0, y, CARD_W, y + FOOTER_H), fill=TOTAL_ROW_BG)
        draw.rectangle((0, y, CARD_W, y + max(1, px(1))), fill=SECTION_HDR_GREEN)
        draw.text((pad_l, y + FOOTER_H // 2 - px(6)), data['footer_label'].upper(), font=f_footer_label, fill=SECTION_HDR_GREEN)
        fvw = _tw(draw, data['footer_value'], f_footer_val)
        draw.text((CARD_W - pad_r - fvw, y + FOOTER_H // 2 - px(7)), data['footer_value'], font=f_footer_val, fill=PTS_DARK)

    return _finalize(img)


PULSE_HOT_FG = (30, 107, 48)
PULSE_COLD_FG = (161, 47, 34)


def render_league_pulse_card(data: dict) -> io.BytesIO:
    """Data contract:
    {
      'league_name': str, 'week_label': str, 'league_avg_ppg': float,
      'hot': [{'name': str, 'ppg': float, 'diff': float}, ...],
      'cold': [{'name': str, 'ppg': float, 'diff': float}, ...],
      'footer_label': str or None, 'footer_value': str or None,
    }
    'diff' is PPG minus the league average -- shown with a sign, not just a
    bare number, since the sign is the whole point of a "hot/cold" framing.
    """
    hot, cold = data['hot'], data['cold']

    f_title = _font('impact', px(26))
    f_meta = _font('bold', px(11))
    f_header_val = _font('impact', px(20))
    f_header_sub = _font('bold', px(11))
    f_section_hdr = _font('bold', px(10))
    f_name = _font('bold', px(13))
    f_sub = _font('regular', px(10.5))
    f_diff = _font('impact', px(14))
    f_footer_label = _font('bold', px(11))
    f_footer_val = _font('regular', px(13))

    HEADER_H = px(78)
    SECTION_HDR_H = px(26)
    ROW_H = px(38)
    FOOTER_H = px(38) if data.get('footer_label') else 0

    total_h = HEADER_H + (SECTION_HDR_H + ROW_H * len(hot) if hot else 0) + (SECTION_HDR_H + ROW_H * len(cold) if cold else 0) + FOOTER_H
    img = Image.new("RGB", (CARD_W, total_h), ROSTER_BG)
    _turf_stripes(img, (0, 0, CARD_W, HEADER_H), TURF_C1, TURF_C2, px(42))
    draw = ImageDraw.Draw(img)

    pad_l, pad_r = px(22), px(22)
    logo_r = px(23)
    logo_cx, logo_cy = pad_l + logo_r, HEADER_H // 2
    _circle_image(img, None, logo_cx, logo_cy, logo_r, border_color=(255, 255, 255, 150), border_w=px(2), fallback_text=data['league_name'])
    text_x = logo_cx + logo_r + px(12)
    draw.text((text_x, HEADER_H // 2 - px(20)), "LEAGUE PULSE".upper(), font=f_title, fill=WHITE)
    meta = f"{data['league_name'].upper()}  ·  {data['week_label'].upper()}"
    draw.text((text_x, HEADER_H // 2 + px(10)), meta, font=f_meta, fill=TEAM_META)

    rw = _tw(draw, f"{data['league_avg_ppg']:.1f}", f_header_val)
    draw.text((CARD_W - pad_r - rw, HEADER_H // 2 - px(18)), f"{data['league_avg_ppg']:.1f}", font=f_header_val, fill=WHITE)
    sw = _tw(draw, "league avg", f_header_sub)
    draw.text((CARD_W - pad_r - sw, HEADER_H // 2 + px(6)), "league avg", font=f_header_sub, fill=HEADER_SUB)

    def section(y, label, rows, fg):
        draw.text((px(8), y + px(8)), label.upper(), font=f_section_hdr, fill=SECTION_HDR_GREEN)
        _dashed_hline(draw, 0, CARD_W, y + SECTION_HDR_H, SECTION_HDR_BORDER, width=max(1, px(0.5)))
        y += SECTION_HDR_H
        for i, r in enumerate(rows):
            if i % 2 == 1:
                draw.rectangle((0, y, CARD_W, y + ROW_H), fill=ROSTER_ZEBRA)
            draw.text((px(22), y + px(7)), r['name'], font=f_name, fill=PTS_DARK)
            draw.text((px(22), y + px(23)), f"{r['ppg']:.1f} ppg", font=f_sub, fill=PTS_MUTED)
            diff_str = f"{'+' if r['diff'] >= 0 else ''}{r['diff']:.1f}"
            dw = _tw(draw, diff_str, f_diff)
            draw.text((CARD_W - px(22) - dw, y + ROW_H // 2 - px(9)), diff_str, font=f_diff, fill=fg)
            y += ROW_H
        return y

    y = HEADER_H
    if hot:
        y = section(y, "Hot", hot, PULSE_HOT_FG)
    if cold:
        y = section(y, "Cold", cold, PULSE_COLD_FG)

    if FOOTER_H:
        draw.rectangle((0, y, CARD_W, y + FOOTER_H), fill=TOTAL_ROW_BG)
        draw.rectangle((0, y, CARD_W, y + max(1, px(1))), fill=SECTION_HDR_GREEN)
        draw.text((pad_l, y + FOOTER_H // 2 - px(6)), data['footer_label'].upper(), font=f_footer_label, fill=SECTION_HDR_GREEN)
        fvw = _tw(draw, data['footer_value'], f_footer_val)
        draw.text((CARD_W - pad_r - fvw, y + FOOTER_H // 2 - px(7)), data['footer_value'], font=f_footer_val, fill=PTS_DARK)

    return _finalize(img)


def render_league_info_card(data: dict) -> io.BytesIO:
    """Data contract:
    {
      'league_name': str, 'team_count': int, 'season': int, 'week_label': str,
      'info_rows': [{'label': str, 'value': str}, ...],
      'roster_composition': [str, ...] (chip labels, e.g. "1 QB", "2 RB"),
    }
    """
    info_rows = data['info_rows']
    chips = data.get('roster_composition', [])

    f_title = _font('impact', px(26))
    f_meta = _font('bold', px(11))
    f_row_label = _font('regular', px(12.5))
    f_row_val = _font('bold', px(12.5))
    f_section_hdr = _font('bold', px(10))
    f_chip = _font('bold', px(10.5))

    HEADER_H = px(78)
    ROW_H = px(30)
    SECTION_HDR_H = px(26) if chips else 0
    CHIP_ROW_H = px(64) if chips else 0  # room for up to 2 wrapped lines of chips

    total_h = HEADER_H + ROW_H * len(info_rows) + SECTION_HDR_H + CHIP_ROW_H
    img = Image.new("RGB", (CARD_W, total_h), ROSTER_BG)
    _turf_stripes(img, (0, 0, CARD_W, HEADER_H), TURF_C1, TURF_C2, px(42))
    draw = ImageDraw.Draw(img)

    pad_l, pad_r = px(22), px(22)
    logo_r = px(23)
    logo_cx, logo_cy = pad_l + logo_r, HEADER_H // 2
    _circle_image(img, None, logo_cx, logo_cy, logo_r, border_color=(255, 255, 255, 150), border_w=px(2), fallback_text=data['league_name'])
    text_x = logo_cx + logo_r + px(12)
    draw.text((text_x, HEADER_H // 2 - px(20)), data['league_name'].upper(), font=f_title, fill=WHITE)
    meta = f"{data['team_count']} TEAMS  ·  SEASON {data['season']}  ·  {data['week_label'].upper()}"
    draw.text((text_x, HEADER_H // 2 + px(10)), meta, font=f_meta, fill=TEAM_META)

    y = HEADER_H
    for i, row in enumerate(info_rows):
        if i % 2 == 1:
            draw.rectangle((0, y, CARD_W, y + ROW_H), fill=ROSTER_ZEBRA)
        draw.text((pad_l, y + ROW_H // 2 - px(8)), row['label'], font=f_row_label, fill=PLAYER_SUB_GRAY)
        vw = _tw(draw, row['value'], f_row_val)
        draw.text((CARD_W - pad_r - vw, y + ROW_H // 2 - px(8)), row['value'], font=f_row_val, fill=PTS_DARK)
        y += ROW_H

    if chips:
        draw.text((px(8), y + px(9)), "ROSTER COMPOSITION", font=f_section_hdr, fill=SECTION_HDR_GREEN)
        _dashed_hline(draw, 0, CARD_W, y + SECTION_HDR_H, SECTION_HDR_BORDER, width=max(1, px(0.5)))
        y += SECTION_HDR_H

        cx = pad_l
        cy = y + px(10)
        for chip in chips:
            cw = _tw(draw, chip, f_chip) + px(16)
            if cx + cw > CARD_W - pad_r:
                cx = pad_l
                cy += px(24)
            draw.rounded_rectangle((cx, cy, cx + cw, cy + px(20)), radius=px(3), fill=POS_BADGE_BG)
            draw.text((cx + px(8), cy + px(3)), chip, font=f_chip, fill=POS_BADGE_FG)
            cx += cw + px(8)

    return _finalize(img)


if __name__ == "__main__":
    import asyncio
    from image_cache import get_images

    async def demo():
        player_ids = [2577417, 4427366, 4259545, 4241389, 4432773, 15847,
                      4608686, 2971573, 4595342, 4688813, 4432577]
        images = await get_images(player_ids=player_ids, team_abbrs=["sea"])
        p = images['players']
        t = images['teams']

        starters = [
            {'slot': 'QB', 'position': 'QB', 'name': 'Dak Prescott', 'team_abbr': 'DAL', 'opp_abbr': 'WAS',
             'headshot_path': p[2577417], 'actual': 26.7, 'proj': 24.7,
             'live': {'team_abbr': 'DAL', 'opp_abbr': 'WAS', 'team_score': 17, 'opp_score': 10,
                      'clock': 'Q3 8:42', 'possession': True, 'redzone': False}},
            {'slot': 'RB', 'position': 'RB', 'name': 'Breece Hall', 'team_abbr': 'NYJ', 'opp_abbr': 'MIA',
             'headshot_path': p[4427366], 'status': 'Q', 'actual': 20.9, 'proj': 13.2,
             'live': {'team_abbr': 'NYJ', 'opp_abbr': 'MIA', 'team_score': 3, 'opp_score': 0,
                      'clock': 'Q1 9:10', 'possession': False, 'redzone': False, 'quiet': True}},
            {'slot': 'RB', 'position': 'RB', 'name': "D'Andre Swift", 'team_abbr': 'CHI', 'opp_abbr': 'GB',
             'headshot_path': p[4259545], 'actual': 21.9, 'proj': 13.9},
            {'slot': 'WR', 'position': 'WR', 'name': 'CeeDee Lamb', 'team_abbr': 'DAL', 'opp_abbr': 'WAS',
             'headshot_path': p[4241389], 'actual': 9.6, 'proj': 17.6},
            {'slot': 'WR', 'position': 'WR', 'name': 'Brian Thomas Jr.', 'team_abbr': 'JAX', 'opp_abbr': 'TEN',
             'headshot_path': p[4432773], 'actual': 7.9, 'proj': 11.4},
            {'slot': 'TE', 'position': 'TE', 'name': 'Travis Kelce', 'team_abbr': 'KC', 'opp_abbr': 'DEN',
             'headshot_path': p[15847], 'actual': 8.6, 'proj': 10.2},
            {'slot': 'FLEX', 'position': 'RB', 'name': 'Kyle Monangai', 'team_abbr': 'CHI', 'opp_abbr': 'GB',
             'headshot_path': p[4608686], 'actual': 21.9, 'proj': 10.3,
             'live': {'team_abbr': 'CHI', 'opp_abbr': 'GB', 'team_score': 14, 'opp_score': 10,
                      'clock': 'Q2 5:15', 'possession': True, 'redzone': True}},
            {'slot': 'D/ST', 'position': 'D/ST', 'name': 'Seahawks D/ST', 'team_abbr': 'SEA', 'opp_abbr': 'SF',
             'headshot_path': t['sea'], 'is_logo': True, 'actual': 12.0, 'proj': 6.7},
            {'slot': 'K', 'position': 'K', 'name': "Ka'imi Fairbairn", 'team_abbr': 'HOU', 'opp_abbr': 'JAX',
             'headshot_path': p[2971573], 'actual': 10.0, 'proj': 7.7},
        ]
        data = {
            'team_name': 'CeeDeez Nutz', 'owner_name': 'Ryan', 'record': '6-8',
            'rank': 9, 'total_teams': 12, 'current_week': 17,
            'proj_record': '7-7', 'proj_record_note': 'unlucky',
            'live_games_count': 2,
            'starters': starters, 'bench_count': 3,
            'starters_total_actual': sum(r['actual'] for r in starters),
            'starters_total_proj': sum(r['proj'] for r in starters),
        }

        buf = render_team_card(data)
        assert buf.getbuffer().nbytes > 0, "render produced an empty buffer"
        with open("team_card_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote team_card_demo.png")

    async def demo_standings():
        from image_cache import get_logos_by_url
        rows = [
            ("GOAT", "Tyler", "10-4", 1806.2, 1671.6, "L2", "https://media.4-paws.org/3/4/6/9/3469d55bcda3d9fd8f5b60b1045bd01cb36e4300/VIER%20PFOTEN_2019-10-08_065-1930x1335.jpg"),
            ("RINO", "TJ", "9-5", 1740.7, 1597.5, "L1", "https://g.espncdn.com/lm-static/ffl/images/default_logos/1.svg"),
            ("Team Josh Allen", "Luke", "8-6", 1805.6, 1645.3, "W1", "https://ih1.redbubble.net/image.1133972217.8204/flat,128x128,075,t.jpg"),
            ("Swift Nation (Travis version)", "Tanner", "7-7", 1511.1, 1602.7, "W3", "https://media-cldnry.s-nbcnews.com/image/upload/rockcms/2024-05/240516-travis-kelce-taylor-swift-ac-1038p-62f872.jpg"),
            ("Poot Emporium Sevens", "Jordan", "6-8", 1778.3, 1731.0, "L1", "https://cdn.drawception.com/images/panels/2012/7-7/8wjyxN9WpX-10.png"),
            ("CeeDeez Nutz", "Ryan", "6-8", 1600.5, 1595.1, "W2", "https://g.espncdn.com/lm-static/logo-packs/ffl/8bitHeros-JoeyEllis/8bit_football-08.svg"),
            ("Hawg Ball", "Eric", "5-9", 1494.5, 1780.4, "W1", "https://content.sportslogos.net/logos/30/606/full/7306.png"),
        ]
        logos = await get_logos_by_url([r[6] for r in rows])
        teams = [
            {'rank': i + 1, 'name': n, 'owner': o, 'record': rec, 'pf': pf, 'pa': pa, 'streak': s, 'logo_path': logos.get(u)}
            for i, (n, o, rec, pf, pa, s, u) in enumerate(rows)
        ]
        data = {
            'league_name': 'Nutt Sacks', 'team_count': 12, 'scoring_label': 'Standard Scoring',
            'header_right_label': 'FINAL', 'header_right_sub': '14 Games Played',
            'playoff_team_count': 6, 'playoff_gb': 1.0,
            'teams': teams,
        }
        buf = render_standings_card(data)
        with open("standings_card_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote standings_card_demo.png")

    async def demo_matchup():
        from image_cache import get_logos_by_url
        # Real Week 17 result: CeeDeez Nutz beat Tyler's Mediocre team 125.3-50.6.
        starters_raw = [
            ('QB', 2577417, 'Dak Prescott', 'QB', 26.7, 24.7, 4426348, 'Jayden Daniels', 'QB', 0.0, 0.0),
            ('RB', 4427366, 'Breece Hall', 'RB', 20.9, 13.2, 4242335, 'Jonathan Taylor', 'RB', 17.4, 19.5),
            ('RB', 4259545, "D'Andre Swift", 'RB', 21.9, 13.9, 4035538, 'David Montgomery', 'RB', 6.0, 8.3),
            ('WR', 4241389, 'CeeDee Lamb', 'WR', 9.6, 17.6, 3126486, 'Deebo Samuel', 'WR', 11.3, 12.0),
            ('WR', 4432773, 'Brian Thomas Jr.', 'WR', 7.9, 11.4, 4683062, 'Xavier Worthy', 'WR', 0.1, 9.6),
            ('TE', 15847, 'Travis Kelce', 'TE', 8.6, 10.2, 4036133, 'T.J. Hockenson', 'TE', 0.0, 0.0),
            ('FLEX', 4608686, 'Kyle Monangai', 'RB', 7.7, 10.3, 4032473, 'Rashid Shaheed', 'WR', 1.8, 9.4),
        ]
        bench_raw = [
            (4595342, 'Oronde Gadsden', 'TE', 12.2, 6.6, 4374302, 'Amon-Ra St. Brown', 'WR', 14.8, 19.4),
            (4688813, 'Josh Downs', 'WR', 5.4, 9.5, 4569618, 'Garrett Wilson', 'WR', 0.0, 0.0),
            (3916945, 'Darius Slayton', 'WR', 5.6, 8.5, 3128720, 'Nick Chubb', 'RB', 0.1, 2.9),
            (4432620, 'Parker Washington', 'WR', 19.0, 9.7, 4361741, 'Brock Purdy', 'QB', 42.9, 23.7),
            (16731, 'Brandin Cooks', 'WR', 14.1, 3.4, 4431611, 'Caleb Williams', 'QB', 27.0, 21.9),
            (4432577, 'C.J. Stroud', 'QB', 17.8, 17.2, 4635008, 'Keon Coleman', 'WR', 0.0, 0.0),
        ]
        ids = [r[1] for r in starters_raw] + [r[6] for r in starters_raw] + [r[0] for r in bench_raw] + [r[5] for r in bench_raw]
        images = await get_images(player_ids=ids, team_abbrs=['sea', 'ten'])
        p = images['players']

        def side(pid, name, pos, pts, proj, win=None, logo=False, abbr=None):
            return {'name': name, 'position': pos, 'pts': pts, 'proj': proj, 'win': win,
                    'headshot_path': images['teams'].get(abbr) if logo else p.get(pid), 'is_logo': logo}

        starters = []
        for slot, lid, lname, lpos, lpts, lproj, rid, rname, rpos, rpts, rproj in starters_raw:
            win_l = lpts > rpts
            starters.append({'slot': slot, 'left': side(lid, lname, lpos, lpts, lproj, win_l),
                              'right': side(rid, rname, rpos, rpts, rproj, not win_l)})
        starters.append({'slot': 'D/ST', 'left': side(None, 'Seahawks D/ST', 'D/ST', 12.0, 6.7, True, True, 'sea'),
                          'right': side(None, 'Titans D/ST', 'D/ST', 0.0, 5.2, False, True, 'ten')})
        starters.append({'slot': 'K', 'left': side(2971573, "Ka'imi Fairbairn", 'K', 10.0, 7.7, False),
                          'right': side(4686361, 'Cam Little', 'K', 14.0, 8.6, True)})
        images_k = await get_images(player_ids=[2971573, 4686361], team_abbrs=[])
        starters[-1]['left']['headshot_path'] = images_k['players'][2971573]
        starters[-1]['right']['headshot_path'] = images_k['players'][4686361]

        bench = [{'slot': 'BE', 'left': side(lid, lname, lpos, lpts, lproj), 'right': side(rid, rname, rpos, rpts, rproj)}
                 for lid, lname, lpos, lpts, lproj, rid, rname, rpos, rpts, rproj in bench_raw]

        logo_urls = {
            'ceedeez': 'https://g.espncdn.com/lm-static/logo-packs/ffl/8bitHeros-JoeyEllis/8bit_football-08.svg',
            'tylers': 'https://i.pinimg.com/564x/ce/f2/0c/cef20cffa0a0c5c9d20e9484392a2534.jpg',
        }
        logos = await get_logos_by_url(list(logo_urls.values()))

        data = {
            'team1': {'name': 'CeeDeez Nutz', 'owner': 'Ryan', 'record': '6-8', 'score': 125.3,
                      'logo_path': logos[logo_urls['ceedeez']]},
            'team2': {'name': "Tyler's Mediocre team", 'owner': 'Tyler', 'record': '6-8', 'score': 50.6,
                      'logo_path': logos[logo_urls['tylers']]},
            'header_sub': 'Final · Week 17',
            'starters': starters, 'bench_count1': len(bench), 'bench_count2': len(bench),
            'totals': {'left': sum(r['left']['pts'] for r in starters), 'right': sum(r['right']['pts'] for r in starters)},
        }
        buf = render_matchup_card(data)
        with open("matchup_card_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote matchup_card_demo.png")

    asyncio.run(demo())
    asyncio.run(demo_standings())
    async def demo_scoreboard():
        from image_cache import get_logos_by_url
        # Real Week 17 results, all six games, plus two mocked-live rows to
        # exercise that state (the real season is already over).
        rows = [
            ("GOAT", "Tyler", "10-4", 124.3, "final", None, "Swift Nation (Travis version)", "Tanner", "7-7", 50.0,
             "https://media.4-paws.org/3/4/6/9/3469d55bcda3d9fd8f5b60b1045bd01cb36e4300/VIER%20PFOTEN_2019-10-08_065-1930x1335.jpg",
             "https://media-cldnry.s-nbcnews.com/image/upload/rockcms/2024-05/240516-travis-kelce-taylor-swift-ac-1038p-62f872.jpg"),
            ("RINO", "TJ", "9-5", 64.8, "live", "Q3 8:12", "Danger Rangers", "Grant", "7-7", 159.2,
             "https://g.espncdn.com/lm-static/ffl/images/default_logos/1.svg",
             "https://mystique-api.fantasy.espn.com/apis/v1/domains/lm/images/0dd370c0-7428-11f0-9926-ff4c4a4ff9e8"),
            ("Team Josh Allen", "Luke", "8-6", None, "tbd", None, "LaPorta Potty", "Jacob", "8-6", None,
             "https://ih1.redbubble.net/image.1133972217.8204/flat,128x128,075,t.jpg",
             "https://a.espncdn.com/i/teamlogos/ncaa/500/8.png"),
            ("Poot Emporium Sevens", "Jordan", "6-8", 123.5, "final", None, "Hawg Ball", "Eric", "5-9", 81.9,
             "https://cdn.drawception.com/images/panels/2012/7-7/8wjyxN9WpX-10.png",
             "https://content.sportslogos.net/logos/30/606/full/7306.png"),
            ("Foot Ball", "Tom", "6-8", 79.9, "live", "Q1 4:50", "Team SoloMid", "Jason", "6-8", 96.4,
             "https://g.espncdn.com/lm-static/ffl/images/default_logos/19.svg",
             "https://mystique-api.fantasy.espn.com/apis/v1/domains/lm/images/1184d0a0-7212-11f0-aec8-aff79ae45af9"),
            ("Tyler's Mediocre team", "Tyler", "6-8", 50.6, "final", None, "CeeDeez Nutz", "Ryan", "6-8", 125.3,
             "https://i.pinimg.com/564x/ce/f2/0c/cef20cffa0a0c5c9d20e9484392a2534.jpg",
             "https://g.espncdn.com/lm-static/logo-packs/ffl/8bitHeros-JoeyEllis/8bit_football-08.svg"),
        ]
        all_urls = [u for r in rows for u in (r[10], r[11])]
        logos = await get_logos_by_url(all_urls)

        def team(name, owner, rec, score, logo_url, other_score):
            win = None if score is None or other_score is None else score > other_score
            return {'name': name, 'owner': owner, 'record': rec, 'score': score, 'win': win, 'logo_path': logos.get(logo_url)}

        matchups = []
        for ln, lo, lr, ls, status, clock, rn, ro, rr, rs, l_url, r_url in rows:
            matchups.append({
                'left': team(ln, lo, lr, ls, l_url, rs), 'right': team(rn, ro, rr, rs, r_url, ls),
                'status': status, 'clock': clock,
            })

        data = {
            'league_name': 'Nutt Sacks', 'matchup_count': 6, 'scoring_label': 'Standard Scoring',
            'live_games_count': 2, 'week_label': 'Week 17', 'matchups': matchups,
        }
        buf = render_scoreboard_card(data)
        with open("scoreboard_card_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote scoreboard_card_demo.png")

    asyncio.run(demo_matchup())

    async def demo_bench():
        from image_cache import get_logos_by_url
        bench1_raw = [
            (4595342, 'Oronde Gadsden', 'TE', 'LAC', 'DEN', 12.2, 6.6),
            (4688813, 'Josh Downs', 'WR', 'IND', 'LAR', 5.4, 9.5),
            (4432577, 'C.J. Stroud', 'QB', 'HOU', 'JAX', 17.8, 17.2, 'O'),
        ]
        bench2_raw = [
            (4374302, 'Amon-Ra St. Brown', 'WR', 'DET', 'MIN', 14.8, 19.4),
            (4569618, 'Garrett Wilson', 'WR', 'NYJ', 'MIA', 0.0, 0.0),
            (3128720, 'Nick Chubb', 'RB', 'HOU', 'JAX', 0.1, 2.9),
            (4361741, 'Brock Purdy', 'QB', 'SF', 'SEA', 42.9, 23.7),
        ]
        ids = [r[0] for r in bench1_raw] + [r[0] for r in bench2_raw]
        images = await get_images(player_ids=ids, team_abbrs=[])
        p = images['players']

        def row(pid, name, pos, team_abbr, opp_abbr, actual, proj, status=None):
            r = {'slot': 'BE', 'position': pos, 'name': name, 'team_abbr': team_abbr, 'opp_abbr': opp_abbr,
                 'headshot_path': p[pid], 'is_logo': False, 'actual': actual, 'proj': proj}
            if status:
                r['status'] = status
            return r

        bench1 = [row(*r) for r in bench1_raw]
        bench2 = [row(*r) for r in bench2_raw]

        logo_urls = {
            'ceedeez': 'https://g.espncdn.com/lm-static/logo-packs/ffl/8bitHeros-JoeyEllis/8bit_football-08.svg',
            'tylers': 'https://i.pinimg.com/564x/ce/f2/0c/cef20cffa0a0c5c9d20e9484392a2534.jpg',
        }
        logos = await get_logos_by_url(list(logo_urls.values()))
        team1 = {'name': 'CeeDeez Nutz', 'owner': 'Ryan', 'record': '6-8', 'logo_path': logos[logo_urls['ceedeez']]}
        team2 = {'name': "Tyler's Mediocre team", 'owner': 'Tyler', 'record': '6-8', 'logo_path': logos[logo_urls['tylers']]}

        buf = render_bench_card({'team1': team1, 'bench1': bench1})
        with open("bench_single_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote bench_single_demo.png")

        buf = render_bench_card({'team1': team1, 'bench1': bench1, 'team2': team2, 'bench2': bench2})
        with open("bench_dual_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote bench_dual_demo.png")

    asyncio.run(demo_bench())

    async def demo_player():
        images = await get_images(player_ids=[4361741], team_abbrs=['sf'])
        data = {
            'name': 'Brock Purdy', 'position': 'QB', 'pro_team': 'SF', 'status': 'Active',
            'headshot_path': images['players'][4361741], 'team_logo_path': images['teams']['sf'],
            'ppg': 24.2, 'total_points': 217.4, 'games_played': 9,
            'highlight_label': 'Week 17', 'highlight_text': "42.9 pts (proj 23.7) · benched by Tyler's Mediocre team",
            'stats': [
                {'value': '241.0', 'label': 'Pass Yds/G'}, {'value': '20', 'label': 'Pass TD'}, {'value': '10', 'label': 'INT'},
                {'value': '69.4%', 'label': 'Completion'}, {'value': '16.3', 'label': 'Rush Yds/G'}, {'value': '3', 'label': 'Rush TD'},
            ],
            'fantasy_team': "Tyler's Mediocre team", 'roster_slot': 'BE',
        }
        buf = render_player_card(data)
        with open("player_card_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote player_card_demo.png")

    asyncio.run(demo_scoreboard())
    async def demo_sleeper():
        rows = [
            (4360423, 'Michael Penix Jr.', 'QB', 'ATL', 5.4, 21.2, 15.4),
            (4688380, 'Cam Ward', 'QB', 'TEN', 6.8, 21.7, 12.7),
            (15864, 'Geno Smith', 'QB', 'LV', 7.3, 21.7, 14.1),
            (4241479, 'Tua Tagovailoa', 'QB', 'MIA', 10.9, 23.6, 14.3),
            (3045147, 'James Conner', 'RB', 'ARI', 19.8, 17.9, 11.1),
        ]
        images = await get_images(player_ids=[r[0] for r in rows], team_abbrs=[])
        players = [
            {'rank': i + 1, 'name': name, 'position': pos, 'sub': f"{team} · {own}% owned",
             'headshot_path': images['players'][pid],
             'metric1_val': f"{proj:.1f}", 'metric1_label': 'proj',
             'metric2_val': f"{avg:.1f}", 'metric2_label': 'avg', 'tag': None, 'tag_type': None}
            for i, (pid, name, pos, team, own, proj, avg) in enumerate(rows)
        ]
        data = {
            'title': 'Sleeper Picks', 'league_name': 'Nutt Sacks', 'subtitle': 'All Positions',
            'header_right_val': '6.8%', 'header_right_sub': 'avg owned',
            'players': players, 'footer_label': None, 'footer_value': None,
        }
        buf = render_player_list_card(data)
        with open("sleeper_card_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote sleeper_card_demo.png")

    async def demo_waiver():
        rows = [
            (3917315, 'Kyler Murray', 'QB', 'ARI', 26.4, 25.1, 'popular'),
            (4595348, 'Malik Nabers', 'WR', 'NYG', 27.4, 21.5, 'popular'),
            (4360423, 'Michael Penix Jr.', 'QB', 'ATL', 5.4, 21.2, 'gem'),
            (4362887, 'Justin Fields', 'QB', 'NYJ', 20.7, 23.9, None),
            (3045147, 'James Conner', 'RB', 'ARI', 19.8, 17.9, None),
        ]
        images = await get_images(player_ids=[r[0] for r in rows], team_abbrs=[])
        players = [
            {'rank': i + 1, 'name': name, 'position': pos, 'sub': f"{team} · {own}% owned",
             'headshot_path': images['players'][pid],
             'metric1_val': f"{proj:.1f}", 'metric1_label': 'proj', 'metric2_val': None, 'metric2_label': None,
             'tag': 'Hidden Gem' if tag == 'gem' else ('Popular' if tag == 'popular' else None), 'tag_type': tag}
            for i, (pid, name, pos, team, own, proj, tag) in enumerate(rows)
        ]
        data = {
            'title': 'Waiver Targets', 'league_name': 'Nutt Sacks', 'subtitle': '0-40% Owned',
            'header_right_val': None, 'header_right_sub': None,
            'players': players, 'footer_label': 'Deepest / Scarcest Position', 'footer_value': 'QB deep · TE scarce',
        }
        buf = render_player_list_card(data)
        with open("waiver_card_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote waiver_card_demo.png")

    asyncio.run(demo_player())
    asyncio.run(demo_sleeper())
    async def demo_compare():
        from image_cache import get_logos_by_url
        logos = await get_logos_by_url([
            'https://g.espncdn.com/lm-static/logo-packs/ffl/8bitHeros-JoeyEllis/8bit_football-08.svg',
            "https://i.pinimg.com/564x/ce/f2/0c/cef20cffa0a0c5c9d20e9484392a2534.jpg",
        ])
        data = {
            'team1': {'name': 'CeeDeez Nutz', 'owner': 'Ryan', 'record': '6-8',
                      'logo_path': logos['https://g.espncdn.com/lm-static/logo-packs/ffl/8bitHeros-JoeyEllis/8bit_football-08.svg']},
            'team2': {'name': "Tyler's Mediocre team", 'owner': 'Tyler', 'record': '6-8',
                      'logo_path': logos["https://i.pinimg.com/564x/ce/f2/0c/cef20cffa0a0c5c9d20e9484392a2534.jpg"]},
            'series_note': 'CeeDeez Nutz leads season series 2-0', 'series_note_warn': False,
            'rows': [
                {'label': 'Points For', 'left_val': '1600.5', 'right_val': '1616.5', 'left_win': True, 'right_win': False},
                {'label': 'Points Against', 'left_val': '1595.1', 'right_val': '1557.8', 'left_win': False, 'right_win': True},
                {'label': 'PPG', 'left_val': '114.3', 'right_val': '115.5', 'left_win': False, 'right_win': True},
                {'label': 'This Week Proj', 'left_val': '125.3', 'right_val': '115.7', 'left_win': True, 'right_win': False},
            ],
        }
        buf = render_compare_card(data)
        with open("compare_card_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote compare_card_demo.png")

    asyncio.run(demo_waiver())
    async def demo_trade():
        from image_cache import get_logos_by_url
        logos = await get_logos_by_url(['https://g.espncdn.com/lm-static/logo-packs/ffl/8bitHeros-JoeyEllis/8bit_football-08.svg'])
        data = {
            'team1_name': 'CeeDeez Nutz', 'team2_name': "Tyler's Mediocre team",
            'team1_logo': logos['https://g.espncdn.com/lm-static/logo-packs/ffl/8bitHeros-JoeyEllis/8bit_football-08.svg'],
            'send': [
                {'name': 'Brian Thomas Jr.', 'position': 'WR', 'avg': 9.9},
                {'name': 'Josh Downs', 'position': 'WR', 'avg': 8.5},
            ],
            'receive': [{'name': 'Breece Hall', 'position': 'RB', 'avg': 13.1}],
            'send_total': 18.4, 'receive_total': 13.1,
            'fairness_label': 'Slightly Uneven', 'trade_type': 'Position Diversification (WR/WR → RB)',
            'injury_notes': ['Injury Risk: Breece Hall is Questionable'],
        }
        buf = render_trade_card(data)
        with open("trade_card_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote trade_card_demo.png")

    asyncio.run(demo_compare())
    async def demo_stats():
        data = {
            'title': 'League Stats', 'league_name': 'Nutt Sacks', 'subtitle': 'Full Season',
            'tiles': [
                {'label': 'Most Consistent', 'team': 'Danger Rangers', 'value': 'σ 13.4', 'sub': None},
                {'label': 'Most Volatile', 'team': "Tyler's Mediocre team", 'value': 'σ 34.3', 'sub': None},
                {'label': 'Best Single Week', 'team': "Tyler's Mediocre team", 'value': '201.2', 'sub': 'pts'},
                {'label': 'Worst Single Week', 'team': 'Swift Nation (Travis version)', 'value': '65.6', 'sub': 'pts'},
                {'label': 'Most Efficient', 'team': 'GOAT', 'value': '.395', 'sub': 'win/1000pf'},
                {'label': 'Unluckiest', 'team': 'Poot Emporium Sevens', 'value': '1778.3 PF', 'sub': '6-8'},
                {'label': 'Toughest Schedule', 'team': 'Danger Rangers', 'value': '1788.1', 'sub': 'PA'},
                {'label': 'Easiest Schedule', 'team': "Tyler's Mediocre team", 'value': '1557.8', 'sub': 'PA'},
            ],
        }
        buf = render_stat_tiles_card(data)
        with open("stats_card_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote stats_card_demo.png")

    asyncio.run(demo_trade())
    async def demo_power():
        data = {
            'league_name': 'Nutt Sacks', 'total_points': 19822.9, 'avg_ppg': 118.0,
            'rankings': [
                {'rank': 1, 'name': 'GOAT', 'record': '10-4', 'ppg': 129.0, 'power': 0.866},
                {'rank': 2, 'name': 'RINO', 'record': '9-5', 'ppg': 124.3, 'power': 0.807},
                {'rank': 3, 'name': 'Team Josh Allen', 'record': '8-6', 'ppg': 129.0, 'power': 0.780},
                {'rank': 4, 'name': 'LaPorta Potty', 'record': '8-6', 'ppg': 120.7, 'power': 0.752},
                {'rank': 5, 'name': 'Danger Rangers', 'record': '7-7', 'ppg': 116.0, 'power': 0.693},
            ],
            'footer_label': 'Top Weekly Proj', 'footer_value': 'CeeDeez Nutz · 125.3',
        }
        buf = render_power_rankings_card(data)
        with open("power_card_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote power_card_demo.png")

    asyncio.run(demo_stats())
    async def demo_pulse():
        data = {
            'league_name': 'Nutt Sacks', 'week_label': 'Week 17', 'league_avg_ppg': 118.0,
            'hot': [
                {'name': 'GOAT', 'ppg': 129.0, 'diff': 11.0},
                {'name': 'Team Josh Allen', 'ppg': 129.0, 'diff': 11.0},
                {'name': 'Poot Emporium Sevens', 'ppg': 127.0, 'diff': 9.0},
            ],
            'cold': [
                {'name': 'Hawg Ball', 'ppg': 106.8, 'diff': -11.2},
                {'name': 'Swift Nation (Travis version)', 'ppg': 107.9, 'diff': -10.1},
                {'name': 'Team SoloMid', 'ppg': 112.2, 'diff': -5.8},
            ],
            'footer_label': 'Season Points Leader', 'footer_value': 'GOAT · 1806.2',
        }
        buf = render_league_pulse_card(data)
        with open("pulse_card_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote pulse_card_demo.png")

    async def demo_league_info():
        data = {
            'league_name': 'Nutt Sacks', 'team_count': 12, 'season': 2026, 'week_label': 'Week 17',
            'info_rows': [
                {'label': 'Scoring Format', 'value': 'Full PPR'},
                {'label': 'Playoff Teams', 'value': '6 (Weeks 15-17)'},
                {'label': 'Regular Season', 'value': '14 Weeks'},
                {'label': 'TD Pass / Reception', 'value': '6 pts / 6 pts'},
                {'label': 'Reception', 'value': '1.0 pt'},
                {'label': 'League Total Points', 'value': '19,822.9'},
                {'label': 'Top Scoring Team', 'value': 'GOAT · 1806.2'},
            ],
            'roster_composition': ['1 QB', '2 RB', '2 WR', '1 TE', '1 FLEX (RB/WR/TE)', '1 D/ST', '1 K', '6 BE'],
        }
        buf = render_league_info_card(data)
        with open("league_info_card_demo.png", "wb") as f:
            f.write(buf.read())
        print("OK: wrote league_info_card_demo.png")

    asyncio.run(demo_power())
    asyncio.run(demo_pulse())
    asyncio.run(demo_league_info())
