"""Renders the /card team card as a PNG instead of a Discord embed.

Discord embeds can't do real typography, layout, or a bold broadcast-style
banner — this draws the card as an image with Pillow so the card looks like
an actual sports graphic instead of fighting Discord's field/box formatting.
"""

import io
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 800, 500

NAVY = (18, 20, 28)
BANNER = (20, 90, 180)
TEXT = (255, 255, 255)
SUBTEXT = (220, 230, 255)
MUTED = (150, 157, 168)
ACCENT = (255, 181, 45)
TRACK = (40, 45, 58)

_FONT_CANDIDATES = {
    'regular': [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ],
    'bold': [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ],
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
    # ponytail: no TTF found on this machine (e.g. a bare Pi image without
    # fonts-dejavu-core) — fall back to Pillow's built-in bitmap font so the
    # card still renders, just without nice typography.
    font = ImageFont.load_default(size=size) if hasattr(ImageFont, "load_default") else ImageFont.load_default()
    _font_cache[key] = font
    return font


def _tw(draw, text, font):
    return draw.textlength(text, font=font)


def render_team_card(data: dict) -> io.BytesIO:
    """data keys: team_name, owner_name, record, rank, total_teams, league_name,
    current_week, avg_points, league_max_avg, week_proj, league_max_proj,
    consistency, power_rating, star_players (list of {name, position, projected}),
    recent_scores (list of float, oldest first)."""

    img = Image.new("RGB", (WIDTH, HEIGHT), NAVY)
    draw = ImageDraw.Draw(img)

    f_huge = _font('bold', 40)
    f_sub = _font('regular', 18)
    f_label = _font('bold', 13)
    f_bold = _font('bold', 16)
    f_dial = _font('bold', 24)

    margin = 40

    # Banner
    draw.rectangle((0, 0, WIDTH, 120), fill=BANNER)
    name = data['team_name'].upper()
    # Shrink the title if a long team name would run into the power dial.
    max_title_w = WIDTH - margin - 130 - margin
    title_font = f_huge
    while _tw(draw, name, title_font) > max_title_w and title_font.size > 22:
        title_font = _font('bold', title_font.size - 2)
    draw.text((margin, 26), name, font=title_font, fill=TEXT)
    subtitle = f"{data['record']} • RANK #{data['rank']} OF {data['total_teams']} • {data['owner_name'].upper()}"
    draw.text((margin, 76), subtitle, font=f_sub, fill=SUBTEXT)

    # Power rating dial, top-right on the banner
    cx, cy, r = WIDTH - 100, 60, 44
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=NAVY)
    pct = max(0.0, min(1.0, data['power_rating'] / 100))
    draw.arc((cx - r, cy - r, cx + r, cy + r), start=-90, end=-90 + 360 * pct, fill=ACCENT, width=8)
    rating_str = f"{data['power_rating']:.0f}"
    draw.text((cx - _tw(draw, rating_str, f_dial) / 2, cy - 15), rating_str, font=f_dial, fill=TEXT)

    # Metric bars
    y = 150
    bar_w = WIDTH - margin * 2
    metrics = [
        ("AVG PTS", data['avg_points'], data['league_max_avg'] or 1, f"{data['avg_points']:.1f}"),
        (f"WEEK {data.get('current_week', '')} PROJ".strip(), data['week_proj'], data['league_max_proj'] or 1, f"{data['week_proj']:.1f}"),
        ("CONSISTENCY", data['consistency'], 100, f"{data['consistency']:.0f}%"),
    ]
    for label, value, max_value, value_str in metrics:
        draw.text((margin, y), label, font=f_label, fill=ACCENT)
        draw.text((WIDTH - margin - _tw(draw, value_str, f_bold), y), value_str, font=f_bold, fill=TEXT)
        pct = max(0.0, min(1.0, value / max_value))
        draw.rectangle((margin, y + 22, WIDTH - margin, y + 34), fill=TRACK)
        draw.rectangle((margin, y + 22, margin + int(bar_w * pct), y + 34), fill=ACCENT)
        y += 52

    y += 14
    draw.line((margin, y, WIDTH - margin, y), fill=TRACK, width=2)
    y += 24

    col_w = (WIDTH - margin * 2 - 30) // 2

    # Star players (left column)
    draw.text((margin, y), "STAR PLAYERS", font=f_label, fill=ACCENT)
    py = y + 28
    for i, player in enumerate(data['star_players'][:3]):
        draw.text((margin, py), str(i + 1), font=_font('bold', 18), fill=ACCENT)
        draw.text((margin + 26, py + 1), f"{player['name']} ({player['position']})", font=f_sub, fill=TEXT)
        pts = f"{player['projected']:.1f}"
        draw.text((margin + col_w - _tw(draw, pts, f_bold), py + 2), pts, font=f_bold, fill=ACCENT)
        py += 32
    if not data['star_players']:
        draw.text((margin, py), "No data available", font=f_sub, fill=MUTED)

    # Recent form (right column) — simple bar chart of last few weeks
    form_x = margin + col_w + 30
    draw.text((form_x, y), "RECENT FORM", font=f_label, fill=ACCENT)
    scores = data['recent_scores'][-5:]
    if scores:
        chart_top = y + 28
        chart_h = 70
        max_score = max(scores) or 1
        bar_gap = 8
        bw = (col_w - bar_gap * (len(scores) - 1)) / len(scores) if len(scores) > 1 else col_w
        for i, score in enumerate(scores):
            h = max(4, int((score / max_score) * chart_h))
            x0 = form_x + i * (bw + bar_gap)
            draw.rectangle((x0, chart_top + chart_h - h, x0 + bw, chart_top + chart_h), fill=ACCENT)
    else:
        draw.text((form_x, y + 28), "No data available", font=f_sub, fill=MUTED)

    # Footer
    footer = f"{data['league_name']} • WEEK {data.get('current_week', '?')}".upper()
    draw.text((margin, HEIGHT - 34), footer, font=f_label, fill=MUTED)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
