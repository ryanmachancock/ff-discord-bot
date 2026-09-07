"""Empirically measured Discord fenced-code-block wrap widths, per render
context -- never hard-code a wrap-width number anywhere else.

Every live command renders natively (Components V2 Container or classic
discord.Embed); there is no Pillow-image path left in the bot. These three
constants are what every table-building function in bot.py budgets its
column widths against (see bot.py's `_flex_width`), one per render context.
"""

# --- Fenced ```code block``` wrap width -------------------------------
#
# First pass (2026-09-06) measured 49 by reading the live /matchup card's
# <code> clientWidth -- but that was circular: the Container was only as
# wide as our own narrow table made it, so the measurement just reflected
# whatever we'd already guessed, not any real ceiling.
#
# Second pass (2026-09-06, /testwidth command): posted a Components V2
# Container with nothing but a digit-string code block, at n=49..80 chars,
# and read each one's real rendered <code> clientWidth/height from the DOM.
# The container's outer div carries a hard `max-width: min(600px, 100%)`
# (Discord's own CSS class isComponentsV2__*, not something we control),
# and the code element inside it tops out at 566px of content width once
# the container hits that 600px cap. Binary search on n found the actual
# wrap boundary: 65 chars stays on one line (code height 34px), 66 wraps
# to two lines (height 52px). So 65, not 49, is the real per-line ceiling
# for a Components V2 Container -- the old 49 was leaving usable width on
# the table because nothing had ever pushed the container wide enough to
# discover its own cap.
#
# `min(600px, 100%)` means a message column narrower than ~600px (small
# window, mobile) clips to 100% instead -- this number holds for a normal
# desktop width, re-measure via /testwidth if a table wraps unexpectedly
# on a much narrower client.
CODE_BLOCK_MAX_CHARS = 65

# --- Fenced code block wrap width inside a classic discord.Embed -------
#
# CODE_BLOCK_MAX_CHARS above was measured from a Components V2 Container
# (LayoutView + discord.ui.Container), which is a different render context
# from a classic discord.Embed(description=...) -- and they turned out to
# have different real caps. Discovered 2026-09-06 when the standings table
# (an embed, not a Container) wrapped its border badly even though it was
# well under CODE_BLOCK_MAX_CHARS: measured via /testwidth2, the same
# digit-string-at-increasing-n technique as CODE_BLOCK_MAX_CHARS, but
# posted as `discord.Embed(description=f"```\n{line}\n```")`. The embed's
# code block tops out at 488px of content width (vs. the Container's
# 566px) and the real wrap boundary is 56 chars: 56 stays on one line
# (height 34px), 57 wraps (height 52px).
#
# Any table built with a classic discord.Embed description (standings,
# compare, insights, detailed_stats, trade) must budget against this
# constant, not CODE_BLOCK_MAX_CHARS -- only Components V2 Container-based
# commands (matchup, bench, scoreboard, waiver, sleeper) get the wider one.
EMBED_CODE_BLOCK_MAX_CHARS = 56

# --- Fenced code block wrap width inside a discord.Embed that ALSO has a
# --- thumbnail and a field (e.g. /team's roster card) -----------------
#
# /team's roster table wrapped badly even though it was sized under
# EMBED_CODE_BLOCK_MAX_CHARS (56) -- a THIRD render context. Measured
# 2026-09-06 via /testwidth3: a synthetic embed with set_thumbnail() (a
# real attached file, not a raw external URL -- that silently failed to
# render at all on the first attempt, which is why the very first
# measurement attempt didn't reproduce the bug) plus one
# add_field(inline=False). That test found 47 as the boundary and still
# wrapped in the real /team card at 47 -- the synthetic embed didn't
# fully match /team's actual structure (longer multi-line description,
# a footer, and an attached View with nav buttons all plausibly narrow
# it further; not isolated one-by-one, given how long this had already
# taken). Rather than keep chasing the exact structural cause, bisected
# directly against the real /team command instead: 44 confirmed clean
# (no wrap, verified live) with some headroom to spare, so this is a
# slightly conservative real-world-verified value rather than a
# from-first-principles exact boundary.
EMBED_THUMBNAIL_CODE_BLOCK_MAX_CHARS = 44
