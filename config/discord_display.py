"""Empirically measured Discord inline-image display dimensions.

All image-card layout math (image_render.py's layout solver) imports these
constants -- never hard-code a display-box number anywhere else.

## How these were measured (2026-09-06)

Posted via the bot itself (not a manual browser upload -- the delivery path
matters, see below) into the #ff test channel: a 2000x1000 "wide" grid PNG
and a 1000x2000 "tall" grid PNG, each sent two ways:
  1. as a bare attachment (`interaction.followup.send(file=...)`)
  2. as an embed image (`embed.set_image(url="attachment://...")`)

Then, in the same chrome-devtools browser session viewing that channel, the
actual rendered `<img>` elements' `clientWidth`/`clientHeight` (CSS pixels)
were read directly from the DOM -- more precise than counting grid squares
in a screenshot, and confirmed against the grid overlay visually. A third
real data point (the actual tall /teststandings card, aspect ratio 0.619)
was measured the same way and agreed with the tall grid's height-bound
number exactly, which is what gives BARE_HEIGHT confidence beyond one
image's rounding.

Measurement conditions: desktop Chrome, member list hidden, browser window
~1000px wide (chat column narrower than a typical maximized desktop client
-- these numbers scale with the actual message-column width, they are NOT
universal Discord constants). DPR=1 in that browser; CSS px is what matters
for the font-legibility comparison (Discord's own body text is also
specified in CSS px, so comparing like-for-like is correct regardless of
the viewer's actual device pixel ratio).

Raw measurements (CSS px), by delivery method and source aspect ratio:
  wide (2000x1000) bare:   514 x 257  (width-bound)
  tall (1000x2000) bare:   176 x 352  (height-bound)
  standings (1632x2636) bare: 218 x 352  (height-bound, confirms 352 above)
  wide (2000x1000) embed:  399 x 200  (width-bound)
  tall (1000x2000) embed:  150 x 300  (height-bound)

Surprise finding: the bare-attachment box is LARGER in both dimensions
than the embed box. For a tall card (which is what standings/roster cards
are), bare attachment is the better delivery method, not embed -- contrary
to the initial assumption that embeds would help. /teststandings uses a
bare attachment on purpose because of this.

Mobile has NOT been measured yet -- these constants are desktop-only until
a phone screenshot of the same /testgrid output is measured the same way.
"""

# Bare attachment (discord.File via followup.send/channel.send) -- larger
# box than embed for tall content, so this is what image_render.py's cards
# should target.
BARE_ATTACHMENT_WIDTH = 514
BARE_ATTACHMENT_HEIGHT = 352

# Embed set_image -- smaller box than bare attachment in both dimensions.
# Kept for reference / in case a future card is wide rather than tall.
EMBED_IMAGE_WIDTH = 399
EMBED_IMAGE_HEIGHT = 300

# Discord's own markdown body text renders around 16px on desktop -- an
# image card's text should be at least that large on screen to not look
# like a downgrade from a plain text embed. 18-20 can be used for a single
# hero number that should read as emphasized/poster-style.
MIN_SCREEN_FONT = 16

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
# Same caveat as the image constants: `min(600px, 100%)` means a message
# column narrower than ~600px (small window, mobile) clips to 100% instead
# -- this number holds for a normal desktop width, re-measure via
# /testwidth if a table wraps unexpectedly on a much narrower client.
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
