"""Fast, local, no-Discord smoke tests for bot.py's ASCII-table building
code.

Run: `python tools/test_tables.py` from the repo root (or from anywhere --
this script adds the repo root to sys.path itself). Runs in well under a
second and needs no pm2 restart, no live league data, and no browser --
the whole point is to replace the "restart bot, drive a real Discord
client with chrome-devtools, eyeball the render" loop for changes to any
table-building function in bot.py.

Everything here imports and calls the REAL functions from bot.py -- never
reimplements or copy-pastes their layout logic -- so this only ever tests
production code, and a regression in bot.py shows up here directly.

Covers, in order:
  1. The low-level layout primitives (_cell_lines, _table_row, _flex_width)
     directly, with synthetic inputs -- these are what the historical
     truncation/off-by-one/glued-column bugs actually lived in.
  2. _frame_table's border/width math, including an ANSI-color-escape case
     (colors count toward len() but not toward visible width -- a real
     historical bug).
  3. The higher-level table-building functions that take plain data and
     return a string/embed without needing a live league or interaction
     object: _roster_table, _matchup_table, _compare_embed.

No pytest/unittest -- plain functions and asserts, run in sequence from
__main__.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402  (import bot.py's real table-building code -- never reimplemented here)

# The two real historical outlier names that broke this bot's tables before
# the shared primitives existed -- used throughout instead of made-up
# strings so these tests actually re-exercise the cases that bit us.
LONG_TEAM_NAME = "Swift Nation (Travis version)"   # 30 chars
LONG_PLAYER_NAME = "Michael Pittman Jr."           # 20 chars


def _vlen(s):
    return bot._visible_len(s)


def _table_lines_from(text):
    """Pull just the bordered-table's physical lines out of a rendered
    string. Works both for a bare _frame_table() return value and for a
    full embed description that has prose (header line, "-# note") before
    or after the ```...``` fenced block -- everything strictly between the
    opening fence's newline and the matching closing fence."""
    start = text.index("```")
    open_end = text.index("\n", start) + 1
    close = text.index("```", open_end)
    body = text[open_end:close]
    return [l for l in body.split("\n") if l]


def _assert_uniform_width(lines, what):
    widths = {_vlen(l) for l in lines}
    assert len(widths) == 1, f"{what}: lines must all share one visible width, got {widths} across {len(lines)} lines"
    return widths.pop()


# --- 1. Low-level primitives --------------------------------------------

def test_table_row():
    """_table_row: the two historical bug classes it exists to prevent --
    truncation of an overflowing flex cell, and two adjacent columns
    gluing together with zero gap once one of them overflows its own
    padding width."""
    # non-flex cells: one physical line, no ellipsis
    rows = bot._table_row([("QB", 5), ("22.1", 6, '>')])
    assert len(rows) == 1
    assert "…" not in rows[0]

    # flex cell that fits: one line, with a real (non-zero) gap to its neighbor
    rows = bot._table_row([("Bob", 10, '<', True), ("5.0", 6, '>')])
    expected = f"{'Bob':<10} {'5.0':>6}"
    assert rows == [expected], f"got {rows!r}"
    assert rows[0][10] == " ", "must be a real separating gap, not glued content"

    # flex cell that overflows: full text alone on its own line; the rest
    # of that row (the other, non-overflowing column) drops to a second
    # physical line instead of gluing onto the name
    rows = bot._table_row([(LONG_TEAM_NAME, 6, '<', True), ("22.1", 6, '>')])
    assert len(rows) == 2, f"expected 2 physical lines, got {rows!r}"
    assert rows[0].rstrip() == LONG_TEAM_NAME, f"overflow line must be just the name, got {rows[0]!r}"
    assert "…" not in rows[0]
    assert "22.1" in rows[1] and LONG_TEAM_NAME not in rows[1]

    # two independent flex columns can each overflow in the same call --
    # each name must get its OWN physical line, never share one. Sharing
    # was the real production bug: two long names on one line made that
    # line far wider than the table's normal rows, long enough to blow
    # past Discord's own code-block width cap and get hard-wrapped
    # mid-row by Discord itself, which is what a broken-looking
    # scoreboard border in production actually was.
    long_col2 = "Tyler's Extremely Mediocre Fantasy Football Team, LLC"
    rows = bot._table_row([(LONG_TEAM_NAME, 6, '<', True), (long_col2, 6, '<', True)])
    assert len(rows) == 3, f"two independent overflows should be 3 physical lines, got {rows!r}"
    assert not any(LONG_TEAM_NAME in l and long_col2 in l for l in rows), \
        f"two overflowing names must never share a physical line, got {rows!r}"
    assert any(l.strip() == LONG_TEAM_NAME for l in rows)
    assert any(l.strip() == long_col2 for l in rows)
    assert not any("…" in l for l in rows)
    print("OK: _table_row never truncates, never glues columns, and never shares a line between two overflowing cells")


def test_flex_width():
    """_flex_width: sums real column widths (plus separators and the
    shared border overhead) instead of a hand-counted constant that can
    silently drift from the real format string -- spot-check the
    arithmetic against a few hand-computed cases, including the exact
    shapes _roster_table/_matchup_table/detailed_stats actually use."""
    OVERHEAD = bot.TABLE_BORDER_OVERHEAD

    # detailed_stats' rankings table: RK(3) REC(7) PPG(7) PWR(6), 1 flex col
    got = bot._flex_width(56, 3, 7, 7, 6, sep=" ", flex_cols=1)
    want = 56 - (3 + 7 + 7 + 6) - 4 - OVERHEAD  # 4 separators between 5 columns
    assert got == want, f"got {got}, want {want}"

    # _roster_table's shape: SLOT(5) OPP(6) PTS(7) PROJ(7) ST(3), 1 flex col
    got = bot._flex_width(44, 5, 6, 7, 7, 3, sep=" ", flex_cols=1)
    want = 44 - (5 + 6 + 7 + 7 + 3) - 5 - OVERHEAD  # 5 separators between 6 columns
    assert got == want, f"got {got}, want {want}"

    # matches the exact assertion documented in bot.py's own _test_table_row
    got = bot._flex_width(65, 5, 6, 6, sep=" ", flex_cols=1)
    want = 65 - 5 - 6 - 6 - 3 - OVERHEAD
    assert got == want, f"got {got}, want {want}"

    # two flex columns add one more separator than one flex column would
    got_1flex = bot._flex_width(65, 5, 6, sep=" ", flex_cols=1)
    got_2flex = bot._flex_width(65, 5, 6, sep=" ", flex_cols=2)
    assert got_2flex == got_1flex - 1, f"adding a second flex column should cost exactly one more separator: {got_1flex} vs {got_2flex}"
    print("OK: _flex_width arithmetic matches hand-computed expectations")


# --- 2. _frame_table -----------------------------------------------------

def test_frame_table():
    """_frame_table: every line (top/bottom border, mid-group rule,
    content) must render at the same visible width, and every content
    line (not the plain-dash borders) must be bound by real │ characters
    at both ends."""
    header = "AAAA BBBB"
    rows = ["1    2   ", "33   44  "]
    rendered = bot._frame_table([header], rows)
    assert rendered.startswith("```\n") and rendered.endswith("\n```")

    lines = _table_lines_from(rendered)
    width = _assert_uniform_width(lines, "_frame_table (plain)")

    content_lines = [l for l in lines if l.startswith("│")]
    border_lines = [l for l in lines if not l.startswith("│")]
    assert content_lines, "expected at least one │-bound content line"
    assert border_lines, "expected at least one plain dash border/rule line"
    for l in content_lines:
        assert l.startswith("│ ") and l.endswith(" │"), f"content line not bound by │ at both ends: {l!r}"
    for l in border_lines:
        assert set(l) == {"─"}, f"border line should be solid dashes, got {l!r}"
    print(f"OK: _frame_table plain case -- {len(lines)} lines, all width {width}, borders and content correctly framed")


def test_frame_table_ansi():
    """_frame_table(lang='ansi'): ANSI color escapes count toward raw
    len() but must NOT count toward the padding/width math, or colored
    rows drift out of alignment with plain ones -- a real historical bug
    (standings' streak column)."""
    RED, RESET = "\x1b[31m", "\x1b[0m"
    plain_row = f"Team Alpha   3-1   {120.4:>5.1f}"
    colored_row = f"{RED}Team Bravo{RESET}   1-3   {RED}{88.2:>5.1f}{RESET}"

    # fixture sanity: same visible width, but the colored row is physically
    # longer because of the embedded escape codes
    assert _vlen(plain_row) == _vlen(colored_row), "fixture rows must be visibly equal width"
    assert len(colored_row) > len(plain_row), "fixture must actually carry raw ANSI bytes"

    header = "TEAM".ljust(len(plain_row))
    rendered = bot._frame_table([header], [plain_row, colored_row], lang="ansi")
    assert rendered.startswith("```ansi\n")

    lines = _table_lines_from(rendered)
    width = _assert_uniform_width(lines, "_frame_table (ansi)")
    raw_lens = {len(l) for l in lines}
    assert len(raw_lens) > 1, "sanity: the ansi row's raw character count should differ from the plain rows despite equal visible width"
    print(f"OK: _frame_table(lang='ansi') keeps visible width {width} aligned despite raw-length differences from color escapes")


# --- 3. Higher-level table-building functions ----------------------------

def test_roster_table():
    """_roster_table (shared by /team's starters and bench sections):
    a genuinely long outlier player name must appear verbatim, never
    truncated, and every rendered line must still line up."""
    assert bot._roster_table([]) == "_Empty_"

    rows = [
        {'slot': 'QB', 'name': 'Josh Allen', 'opp_abbr': 'MIA', 'actual': 24.5, 'proj': 21.0, 'status': None},
        {'slot': 'WR', 'name': LONG_PLAYER_NAME, 'opp_abbr': 'DAL', 'actual': 9.2, 'proj': 11.4, 'status': 'Q'},
        {'slot': 'RB', 'name': 'CeeDee Lamb', 'opp_abbr': 'PHI', 'actual': 15.1, 'proj': 14.0, 'status': None},
    ]
    table = bot._roster_table(rows)
    assert "…" not in table
    assert LONG_PLAYER_NAME in table, "full outlier name must appear verbatim"

    lines = _table_lines_from(table)
    width = _assert_uniform_width(lines, "_roster_table")
    print(f"OK: _roster_table -- outlier name preserved, {len(lines)} lines all width {width}")


class _FakePlayer:
    """Bare stand-in for espn_api's player/box-score objects -- only the
    attributes _matchup_table and its helpers actually read."""

    def __init__(self, name, position, slot_position, projected_points, points):
        self.name = name
        self.position = position
        self.slot_position = slot_position
        self.projected_points = projected_points
        self.points = points


def test_matchup_table():
    """_matchup_table (side-by-side /matchup card): exercises both an
    only-one-side-overflows row (the exact case that broke the versus
    divider -- see below) and a values-line-alignment check, without
    truncating anything and while the whole table stays aligned."""
    starters1 = [
        _FakePlayer("Josh Allen", "QB", "QB", 24.5, 26.1),
        # only side 1 overflows on this row -- the exact scenario that
        # actually broke: the "│" divider vanished (blank-padded like
        # ordinary row data) on the one physical line where only ONE
        # side needed to wrap, not both.
        _FakePlayer(LONG_PLAYER_NAME, "WR", "WR", 11.4, 9.2),
        _FakePlayer("Bills D/ST", "D/ST", "D/ST", 8.0, 6.0),
    ]
    starters2 = [
        _FakePlayer("Patrick Mahomes", "QB", "QB", 23.0, 25.4),
        _FakePlayer("Rashee Rice", "WR", "WR", 12.0, 10.5),
        # only side 2 overflows here -- the mirror image of the row above
        _FakePlayer(LONG_TEAM_NAME, "D/ST", "D/ST", 7.0, 9.0),
    ]
    table = bot._matchup_table(starters1, starters2, pregame=False)
    assert "…" not in table
    assert LONG_PLAYER_NAME in table, "side-1 outlier name must appear verbatim"
    assert LONG_TEAM_NAME in table, "side-2 outlier name must appear verbatim"

    lines = _table_lines_from(table)
    width = _assert_uniform_width(lines, "_matchup_table")
    # The versus divider is a repeat=True cell specifically so it stays
    # continuous down every content line, including the physical line
    # where a name overflowed -- a real bug had it vanish (blank-padded
    # like ordinary row data) on exactly those rows, making the border
    # look broken. Checking "│" in l is NOT enough: _frame_table's OWN
    # outer "│ ... │" wraps every line regardless, so a line missing
    # only the *middle* divider still contains two pipes and that check
    # never fires (confirmed by hand: this exact assertion silently
    # passed with the bug still present). Count pipes instead -- every
    # content line must have the same count as the fullest line (header/
    # data/total, which show name + divider + name), one fewer means the
    # middle divider specifically went missing. Rule/border lines are
    # pure dashes by design and legitimately have fewer, so only check
    # lines with real content.
    content_lines = [l for l in lines if any(ch not in "─│ " for ch in l)]
    full_count = max(l.count("│") for l in content_lines)
    missing = [l for l in content_lines if l.count("│") < full_count]
    assert not missing, f"_matchup_table: divider must appear on every content line, missing on {missing!r}"
    print(f"OK: _matchup_table -- both sides overflowed independently, {len(lines)} lines all width {width}, divider never vanishes")


def test_compare_embed():
    """_compare_embed (/compare and /compare_cross_league): one row per
    team (PF/PA/PPG/PROJ as columns, like /standings' TEAM column), not
    one row per stat with both team names sharing a header -- so a real
    outlier name (LONG_TEAM_NAME, 30 chars) must now fit on a single line
    with no overflow at all, and an even more extreme synthetic name must
    still never truncate even when it does overflow."""
    data = {
        'team1': {'name': LONG_TEAM_NAME, 'record': '8-3'},
        'team2': {'name': 'Tyler is Fine', 'record': '5-6', 'league': 'Dynasty'},
        'series_note': "Alpha leads the season series 2-1",
        'current_week': 5,
        'rows': [
            {'abbrev': 'PPG', 'left_val': '112.4', 'right_val': '104.9'},
            {'abbrev': 'WIN%', 'left_val': '72.7', 'right_val': '45.5'},
            {'abbrev': 'STRK', 'left_val': 'W3', 'right_val': 'L1'},
        ],
    }
    embed = bot._compare_embed(data)
    desc = embed.description
    assert "…" not in desc
    assert LONG_TEAM_NAME in desc, "full outlier team name must appear verbatim"

    lines = _table_lines_from(desc)
    _assert_uniform_width(lines, "_compare_embed (realistic outlier)")
    assert any(LONG_TEAM_NAME in l and "112.4" in l for l in lines), \
        "a realistic 30-char outlier name should fit on the SAME line as its own stats now, not overflow onto its own bare line"

    # An even more extreme name must still never truncate, even though it
    # does overflow onto its own line at this length.
    extreme_name = "The " + LONG_TEAM_NAME * 2
    data2 = {**data, 'team1': {'name': extreme_name, 'record': '8-3'}}
    desc2 = bot._compare_embed(data2).description
    assert "…" not in desc2
    assert extreme_name in desc2, "even an extreme outlier name must appear verbatim, never truncated"
    width = _assert_uniform_width(_table_lines_from(desc2), "_compare_embed (extreme outlier)")
    print(f"OK: _compare_embed -- realistic outlier fits inline, extreme outlier still never truncates, width {width}")


TESTS = [
    test_table_row,
    test_flex_width,
    test_frame_table,
    test_frame_table_ansi,
    test_roster_table,
    test_matchup_table,
    test_compare_embed,
]


def _safe(s):
    """Windows' console is often cp1252, which can't encode the box-
    drawing/emoji characters these tables are full of -- a failure
    message containing one used to crash the *reporting* of the
    failure, hiding the real assertion behind a UnicodeEncodeError.
    Escape anything the console can't show instead of crashing on it."""
    return str(s).encode(sys.stdout.encoding or "ascii", errors="backslashreplace").decode(sys.stdout.encoding or "ascii")


if __name__ == "__main__":
    failures = []
    for t in TESTS:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failures.append((t.__name__, e))
            print(_safe(f"FAIL  {t.__name__}: {e}"))
        print()

    if failures:
        print(f"{len(failures)}/{len(TESTS)} test(s) FAILED:")
        for name, e in failures:
            print(_safe(f"  - {name}: {e}"))
        sys.exit(1)

    print(f"All {len(TESTS)} tests passed.")
    sys.exit(0)
