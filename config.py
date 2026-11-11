"""FleeceGuard v2 settings. Everything league-specific or tunable lives here."""
import datetime as dt

SEASON = 2026
LEAGUE_NAME = "The Fam League"   # shown in the header
POSITIONS = ("QB", "RB", "WR", "TE")
FLEX_POSITIONS = ("RB", "WR", "TE")

# Starting lineup (K and DEF are ignored: they don't move trade value)
ROSTER_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2}

# Roster cap. Yahoo: 8 offensive starters + K + DEF + 4 BN + 2 IR.
# ASSUMES every team carries exactly one K and one DEF, so 12 non-IR offensive players fit.
ACTIVE_OFFENSE_CAP = 12
IR_SLOTS = 2
# Statuses we treat as IR-slot eligible. ASSUMPTION: Yahoo allows "Out" in IR slots; verify.
IR_ELIGIBLE = ("IR", "Out")

# League type: redraft with ONE rookie keeper per team (keeping him costs a draft round).
# Almost every player goes back into next year's draft, so trades are judged on
# REST-OF-SEASON value, not long-term (dynasty) value. Keeper value is noted, never scored.
KEEPER_RULE = "one rookie per team, at the cost of a draft round"

LAST_REGULAR_SEASON_WEEK = 14
PLAYOFF_TEAMS = 8
TRADE_DEADLINE = dt.date(2026, 11, 28)
# Yahoo holds a trade for 2 days of league review. A trade proposed Tuesday-Friday clears
# before Sunday's games, so it counts for the current week; later ones count from next week.
TRADE_COUNTS_THIS_WEEK_DAYS = (1, 2, 3, 4)   # Monday is 0

# ---- League scoring (from Yahoo settings) -------------------------------------------
SCORING = {
    "pass_cmp": 0.25, "pass_inc": -0.25, "pass_yd_per_pt": 25.0,
    "pass_td": 4.0, "pass_int": -1.0, "pass_bonus": {275: 1.0, 375: 3.0, 450: 5.0},
    "rush_yd_per_pt": 10.0, "rush_td": 6.0, "rush_bonus": {75: 2.0, 100: 4.0, 150: 6.0},
    "rec": 1.0, "rec_yd_per_pt": 10.0, "rec_td": 6.0, "rec_bonus": {75: 2.0, 100: 4.0},
    "ret_td": 6.0, "fum_lost": -2.0, "two_pt": 2.0, "td40_bonus": 2.0,
}
# UNVERIFIED: do bonus tiers stack (100 rush yds = 2 + 4) or does only the top tier count?
# Check one real box score from your league, then flip this.
BONUSES_STACK = False
# Used only when the stats feed has no exact 40+ yard TD counts (share of TDs that go 40+)
TD40_SHARE = {"pass": 0.12, "rush": 0.07, "rec": 0.15}

# ---- Player value model -------------------------------------------------------------
# No hardcoded player numbers anywhere. A player's PPG comes only from real stats.
# Small samples are pulled toward replacement level (the best free agents at the position,
# measured from real stats) as if he had this many replacement-level games. A player who
# has played 8 games keeps 80% of his own production; one game keeps 33%.
SHRINK_GAMES = 2
W_SEASON, W_RECENT = 0.6, 0.4   # blend of season-long PPG and last-3-games PPG
REPLACEMENT_RANK = 3     # replacement level = Nth best free agent at the position
FA_MIN_GAMES = 3
RANK_MIN_GAMES = 3       # min games to get a positional rank (e.g. "RB6")

# Rest-of-season availability multipliers (tunable guesses, not facts)
AVAILABILITY = {"Healthy": 1.0, "Questionable": 0.93, "Doubtful": 0.60, "Out": 0.30, "IR": 0.0}

# ---- Trade verdict ------------------------------------------------------------------
# Design goal: the tool answers "is there any reason to block this trade?", and the answer
# should usually be no. Most trades should land in the first tier; the top tier should be rare.
# Wrongly blocking a good-faith trade is worse than letting a mildly bad one through.
#
# Star premium: >1 rewards one star over several mid players. Kept mild (was 1.6) because the
# market values already come from real trades, which include some of this premium.
CONSOLIDATION_POWER = 1.3
FAIR_GAP = 0.25             # player-value gap under this = NO CONCERNS (was 0.15)
VETO_GAP = 0.45             # over this = NEEDS A SECOND LOOK (was 0.30)
# Swaps of two low-value players can show a big % gap on tiny numbers. Below this absolute
# difference (in star-premium units; roughly one ~2000-value bench player) a trade is
# always NO CONCERNS.
MIN_ABS_GAP = 1200
# "Fills a need" rule: if the team giving up more value still improves its own starting lineup
# by this many points a week (or its playoff odds by WIN_NOW_GAIN), the result is softened one
# level. Trading from depth to fix a weak spot is a normal, healthy trade.
LINEUP_GAIN = 2.0
WIN_NOW_GAIN = 0.06

# ---- Playoff simulation -------------------------------------------------------------
WEEKLY_SD = 24.0            # weekly team-score volatility (assumption)
N_SIMS = 4000
CONTENDER_PROB = 0.60
SELLER_PROB = 0.15          # below this playoff chance a team is a "Long shot"

# Name equivalences. Applied to BOTH Yahoo and feed names, so either spelling matches.
NAME_ALIASES = {
    "j croskey merritt": "jacory croskey merritt",
    "bam knight": "zonovan knight",
    "tank dell": "nathaniel dell",
}
