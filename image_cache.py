"""Local disk cache for ESPN player headshots and NFL team logos.

The cache is never a gate on whether an image appears in a render — callers
should always call get_images() for everyone on the card, every time. A cache
hit returns instantly; a miss fetches once and caches it (positive AND
negative results), so a player either always has their real headshot or
always falls back to initials, never inconsistently depending on what's been
rendered before.
"""

import os
import io
import hashlib
import asyncio
import urllib.parse
import aiohttp
from PIL import Image
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPM
from dotenv import load_dotenv

load_dotenv()

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache", "images")
PLAYER_DIR = os.path.join(CACHE_DIR, "players")
TEAM_DIR = os.path.join(CACHE_DIR, "teams")
GENERIC_DIR = os.path.join(CACHE_DIR, "generic")

HEADSHOT_URL = "https://a.espncdn.com/i/headshots/nfl/players/full/{id}.png"
TEAM_LOGO_URL = "https://a.espncdn.com/i/teamlogos/nfl/500/{abbr}.png"

# Some custom fantasy team logos are hosted on ESPN's authenticated
# image API (mystique-api.fantasy.espn.com) rather than its public static
# CDN, and 401 without the same league session cookies used for the main
# API. Harmless to attach only when the request is actually going to an
# espn.com host -- other sites just won't recognize these cookie names.
ESPN_COOKIES = {"SWID": os.getenv("ESPN_SWID"), "espn_s2": os.getenv("ESPN_S2")}

os.makedirs(PLAYER_DIR, exist_ok=True)
os.makedirs(TEAM_DIR, exist_ok=True)
os.makedirs(GENERIC_DIR, exist_ok=True)


async def _resolve(session, url, cache_path):
    """Return cache_path if the image exists or can be fetched, else None.
    Caches negative results too (a `.none` marker) so a player ESPN has no
    photo for isn't re-requested on every single render."""
    if os.path.exists(cache_path):
        return cache_path
    miss_marker = cache_path + ".none"
    if os.path.exists(miss_marker):
        return None

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status != 200 or not resp.headers.get("Content-Type", "").startswith("image/"):
                open(miss_marker, "wb").close()
                return None
            data = await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        # ponytail: no retry — a transient ESPN hiccup just falls back to
        # initials for this render; the next render tries again since we
        # don't write a .none marker on network errors, only on real 404s.
        return None

    with open(cache_path, "wb") as f:
        f.write(data)
    return cache_path


async def get_player_headshot(session, player_id) -> str | None:
    cache_path = os.path.join(PLAYER_DIR, f"{player_id}.png")
    return await _resolve(session, HEADSHOT_URL.format(id=player_id), cache_path)


async def get_team_logo(session, team_abbr) -> str | None:
    abbr = team_abbr.lower()
    cache_path = os.path.join(TEAM_DIR, f"{abbr}.png")
    return await _resolve(session, TEAM_LOGO_URL.format(abbr=abbr), cache_path)


def _rasterize_svg(data: bytes) -> bytes | None:
    """A decent fraction of custom fantasy team logos are SVGs, which Pillow
    can't decode at all -- svglib+reportlab render the common case (flat
    shapes/icons, which team logos are) to a real raster image. Not a full
    SVG implementation (complex filters/gradients may not render right),
    but good enough that we tried it before giving up. Returns None on any
    failure so the caller falls back to the normal miss path."""
    try:
        drawing = svg2rlg(io.BytesIO(data))
        if drawing is None:
            return None
        buf = io.BytesIO()
        renderPM.drawToFile(drawing, buf, fmt="PNG")
        return buf.getvalue()
    except Exception:
        return None


async def _resolve_url(session, url):
    """Like _resolve(), but for an arbitrary URL (custom fantasy team logos
    are hosted anywhere -- ESPN CDN, redbubble, pinimg, hand-drawn SVGs...).
    Cached by a hash of the URL since there's no stable ID to key on.
    Anything that's neither a Pillow-readable raster nor an SVG we can
    rasterize (a broken/non-image response) is treated as a miss and cached
    negatively, same as a real 404 -- callers always get a real raster
    image or a clean None, never a crash."""
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    cache_path = os.path.join(GENERIC_DIR, f"{key}.png")
    if os.path.exists(cache_path):
        return cache_path
    miss_marker = cache_path + ".none"
    if os.path.exists(miss_marker):
        return None

    cookies = ESPN_COOKIES if urllib.parse.urlsplit(url).hostname.endswith("espn.com") else None
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5), cookies=cookies) as resp:
            if resp.status != 200:
                open(miss_marker, "wb").close()
                return None
            data = await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        rasterized = _rasterize_svg(data)
        if rasterized is None:
            open(miss_marker, "wb").close()
            return None
        try:
            img = Image.open(io.BytesIO(rasterized))
            img.load()
        except Exception:
            open(miss_marker, "wb").close()
            return None

    img.convert("RGBA").save(cache_path, format="PNG")
    return cache_path


async def get_logos_by_url(urls) -> dict:
    """Resolve a list of arbitrary logo URLs to local cache paths. Same
    never-a-gate contract as get_images(): callers ask for every URL, every
    render; a cache hit returns instantly and a miss (or an SVG Pillow can't
    read) always falls back to the same result, never inconsistently.

    Returns {url: path_or_None}.
    """
    async with aiohttp.ClientSession() as session:
        urls = list(dict.fromkeys(u for u in urls if u))
        results = await asyncio.gather(*(_resolve_url(session, u) for u in urls))
    return dict(zip(urls, results))


async def get_images(player_ids, team_abbrs) -> dict:
    """Concurrently resolve every headshot/logo needed for one card.

    Returns {'players': {player_id: path_or_None}, 'teams': {abbr: path_or_None}}.
    """
    async with aiohttp.ClientSession() as session:
        player_ids = list(dict.fromkeys(player_ids))  # de-dupe, preserve order
        team_abbrs = list(dict.fromkeys(team_abbrs))

        player_results, team_results = await asyncio.gather(
            asyncio.gather(*(get_player_headshot(session, pid) for pid in player_ids)),
            asyncio.gather(*(get_team_logo(session, abbr) for abbr in team_abbrs)),
        )

    return {
        "players": dict(zip(player_ids, player_results)),
        "teams": dict(zip(team_abbrs, team_results)),
    }


if __name__ == "__main__":
    async def demo():
        result = await get_images(
            player_ids=[4361741, 999999999],  # Brock Purdy (real), bogus ID (should miss)
            team_abbrs=["sf", "dal"],
        )
        assert result["players"][4361741] and os.path.exists(result["players"][4361741]), "known-good headshot should resolve"
        assert result["players"][999999999] is None, "bogus player id should miss, not raise"
        assert result["teams"]["sf"] and os.path.exists(result["teams"]["sf"]), "known-good team logo should resolve"

        logos = await get_logos_by_url([
            "https://a.espncdn.com/i/teamlogos/nfl/500/sf.png",  # real raster, should resolve
            "https://g.espncdn.com/lm-static/ffl/images/default_logos/1.svg",  # SVG, should rasterize
            "https://g.espncdn.com/lm-static/ffl/images/default_logos/does-not-exist-999.svg",  # real 404, should miss cleanly
        ])
        raster_url, svg_url, broken_url = list(logos.keys())
        assert logos[raster_url] and os.path.exists(logos[raster_url]), "raster logo URL should resolve"
        assert logos[svg_url] and os.path.exists(logos[svg_url]), "SVG should now rasterize to a real image"
        assert logos[broken_url] is None, "a real 404 should still miss cleanly, not crash"
        print("OK:", result, logos)

    asyncio.run(demo())
