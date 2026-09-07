# ff-discord-bot: visual style standard

This file documents the target look for every data-bearing command's output.
Read this before touching any command's rendering code.

## The goal: match `/scoreboard` and `/matchup`

Those two commands are the finished reference. Every other data command
should look and feel like them: clean bordered tables, real whitespace and
separation between logical groups, numbers easy to scan at a glance, nothing
glued together, nothing truncated, nothing wrapped mid-word by Discord.

Exact current output, captured live (2026-09-07) as the concrete bar to hit:

**`/scoreboard`** — one stacked block per game (team name, then score,
one team per line), a thin rule between every game, single outer border:

```
─────────────────────────────────────────────────────────────────
│ Team SoloMid                                              0.0 │
│ Foot Ball                                                 0.0 │
│ ───────────────────────────────────────────────────────────── │
│ CeeDeez Nutz                                              0.0 │
│ Hawg Ball                                                 0.0 │
│ ───────────────────────────────────────────────────────────── │
│ Poot Emporium Sevens                                      0.0 │
│ RINO                                                      0.0 │
│ ───────────────────────────────────────────────────────────── │
│ Swift Nation (Travis version)                             0.0 │
│ Tyler's Mediocre team                                     0.0 │
─────────────────────────────────────────────────────────────────
```

**`/matchup`** — one dense side-by-side table, POS/PTS header (no team
names duplicated in the header — they're already in the message text above
the table), divider running continuously top to bottom, total row at the
bottom:

```
─────────────────────────────────────────────────────────────
│ POS                      PTS │                        PTS │
│ ───────────────────────────────────────────────────────── │
│ QB     Justin Herbert   22.8 │  Matthew Stafford     21.6 │
│ RB     Bijan Robinson   19.3 │  Chase Brown          16.0 │
│ WR     Drake London     15.1 │  Zay Flowers          14.2 │
│ D/ST   Browns            4.8 │  Jaguars               8.2 │
│ ───────────────────────────────────────────────────────── │
│        TOTAL           126.8 │  TOTAL               118.5 │
─────────────────────────────────────────────────────────────
```

Note what makes these work: every column is sized to its REAL content (no
column is wider than the longest value it actually holds), there's a real
gap between columns, the border is a single consistent width top to bottom,
and nothing in the table repeats information already visible elsewhere on
the card.

## Non-negotiable rules

- **Never truncate, ever.** No ellipsis, no cut-off names, not even for
  genuine outliers (a 30-character joke team name must still render in
  full). This was an explicit, absolute product requirement, not a
  guideline to weigh against other concerns.
- **Never let a table cell glue to its neighbor.** Two columns must always
  have a real separating gap, even when one of them overflows its normal
  width.
- **Never let one long value break the whole table's border.** A single
  wide line in a bordered table drags every other line's border width up to
  match it (`_frame_table` sizes the border to the widest line); an
  overflowing name must not carry unrelated columns' blank padding along
  with it onto that line, or the combined line can exceed Discord's real
  render-width cap and get hard-wrapped by Discord itself, breaking the
  border on every row, not just the long one.
- **Don't duplicate data.** If something is already shown once (a team name
  in the message header, a rank already printed in the card body), don't
  print it again inside a table or a button label just to have a label.
  Duplicated info is usually also the thing that breaks first when the
  duplicate can't fit.
- **Size columns to real content, not round numbers.** A column budgeted
  wider than anything it will ever hold just starves whatever column
  actually needed the space (this was the actual bug behind `/team`'s
  roster table wrapping every single player name, not just outliers).

## The shared primitives (bot.py)

Every table in the bot should go through these three functions rather than
hand-rolled format strings — the two historical bug classes (glued columns,
drifting hand-counted width constants) are structurally impossible when
built this way:

- **`_table_row(cells, sep=" ")`** — one logical row as 1+ physical lines.
  `cells` is a list of `(text, width)`, `(text, width, align)`,
  `(text, width, align, flex)`, or `(text, width, align, flex, repeat)`
  tuples. A `flex` cell that's too long for its width gets its own bare
  line (just that cell's text, nothing else glued onto it) instead of
  truncating or forcing every column onto one overlong line. A `repeat`
  cell (e.g. a "│" divider between two independent flex columns) renders
  on every physical line instead of only the last one, so the divider
  never visibly vanishes on the one row where a name happened to overflow.
- **`_frame_table(*groups, lang="")`** — wraps one or more line-groups in
  the shared `─`/`│` border, with a plain rule inserted between groups
  (this is what gives `/scoreboard` its thin rule between games).
- **`_flex_width(budget, *fixed_widths, sep=" ", flex_cols=1)`** — computes
  how much room is left for the flex (name) column(s) given the table's
  real character budget and the OTHER columns' real widths, summed from
  actual numbers instead of a hand-counted comment that can silently drift.

Character budgets live in `config/discord_display.py`, measured empirically
against real rendered Discord output, not guessed:

- `CODE_BLOCK_MAX_CHARS` (65) — Components V2 Container (`/matchup`,
  `/scoreboard`, `/waiver`, `/sleeper`).
- `EMBED_CODE_BLOCK_MAX_CHARS` (56) — classic `discord.Embed` description
  (`/standings`, `/compare`, `/insights`, `/detailed_stats`).
- `EMBED_THUMBNAIL_CODE_BLOCK_MAX_CHARS` (44) — an embed that ALSO has a
  thumbnail and a field (`/team`'s roster card — the tightest budget in
  the bot, easy to accidentally starve if a column is oversized).

`TABLE_BORDER_OVERHEAD` (4, in `bot.py`) is the `"│ "` / `" │"` the border
adds to every line — always budgeted against, never added back in by hand.

## Testing workflow

- **`python tools/test_tables.py`** — fast (<1s), no Discord/pm2 needed.
  Imports `bot.py` directly and exercises the real table-building functions
  with synthetic data, including known outlier names. Run this after any
  change to a table-building function or its column widths.
- **`python -c "import bot; bot._test_table_row()"`** — the primitive's own
  inline self-test, run at bot startup too.
- **Live verification still matters.** The local tests check the Python
  string output; they can't catch Discord itself hard-wrapping a line that's
  technically "correct" by the numbers but too wide for the real render
  width. After a local pass, restart the live bot
  (`tools/restart_bot.ps1`, must run elevated) and check the actual
  command in Discord via chrome-devtools browser automation against the
  `#ff` test channel. Pull the RAW rendered text with
  `document.querySelectorAll('code')` via `evaluate_script` rather than
  trusting a screenshot alone — screenshots can look "off" at a glance in
  ways that are actually fine, and can look fine at a glance in ways that
  are actually broken (glued text, a divider on the wrong side of a name).
- A stale message from before the last bot restart has no live `View`
  behind it — clicking its buttons always fails with "didn't respond in
  time". Test buttons on a freshly-sent message, not one left over from
  before a restart.

## Where things stand (2026-09-07)

Every command renders natively (Components V2 Container or classic Embed).
There is no Pillow-image path anywhere in the bot: `image_render.py`, and
the `/testteam`/`/teststandings`/`/testgrid` prototype commands that were
its only callers, were deleted outright (the image-style A/B was decided
against). `image_cache.py` (`get_images`/`get_logos_by_url`) still exists
and is still used — that's small per-player headshots and team logos shown
as a `discord.ui.Thumbnail`/`Section` accessory inside real commands
(`/player`, `/team`, `/waiver`, `/sleeper`), not a rendered card.

Every command in the bot has been reviewed against the `/scoreboard` /
`/matchup` style bar at least once; `/compare`, `/compare_cross_league`,
and `/trade` all needed real fixes to get there (see bot.py's inline
comments on `_compare_embed` and `/trade`'s `trade_table` for what broke
and why). `/compare`'s table keeps a header row repeating both team names
on purpose — real user feedback overrode the "don't duplicate the header"
instinct below: unlike `/matchup`, `/compare`'s rows are bare label/number
pairs with nothing else to anchor a column to, so dropping the header left
the table ambiguous on its own.
