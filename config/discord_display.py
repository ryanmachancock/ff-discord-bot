"""Empirically measured Discord fenced-code-block wrap width -- never
hard-code a wrap-width number anywhere else.

Every table-bearing command renders as a Components V2 Container now
(LayoutView + discord.ui.Container + TextDisplay) -- /standings, /compare,
/detailed_stats, and /insights were the last ones still on a classic
discord.Embed, converted this session for exactly this reason: an Embed's
code block only gets a 56-char budget (44 if it also carries a thumbnail
and a field, as /team's roster card did), and both were narrow enough that
completely ordinary names -- not just genuine outliers -- were wrapping
onto their own line. A Container gets 65, measured below.
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
