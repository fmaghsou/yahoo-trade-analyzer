"""Data layer. Every network call fails soft: the app keeps working and reports what's missing.

Sources (none need a login):
  - Sleeper  : player database (age, injury status) and weekly stats
  - nflverse : backup weekly stats (CSV on GitHub)
  - FantasyCalc / Dynasty Dealer : dynasty market values (first one that answers wins)
NOTE: these endpoints could not be reached from the build environment. The app's
'Data check' tab shows exactly what loaded, so verify it on first run.
"""
import csv
import difflib
import io
import re
from collections import defaultdict

import requests

import config as C
import engine as eng
from scoring import score_week

SLEEPER = "https://api.sleeper.app/v1"
# Sleeper has two unofficial stats URLs; the second is the newer one. Both shapes are handled.
SLEEPER_STATS = [f"{SLEEPER}/stats/nfl/regular/{{season}}/{{week}}",
                 "https://api.sleeper.com/stats/nfl/{season}/{week}?season_type=regular"]
FANTASYCALC = "https://api.fantasycalc.com/values/current?isDynasty=true&numQbs=1&numTeams=14&ppr=1"
DYNASTY_DEALER = "https://www.dynastydealer.com/api/player-values"
NFLVERSE = ["https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv",
            "https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats_{season}.csv"]
HEADERS = {"User-Agent": "FleeceGuard/2.0"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _get(url, timeout=60, as_text=False):
    try:
        r = requests.get(url, timeout=timeout, headers=HEADERS)
        r.raise_for_status()
        return (r.text if as_text else r.json()), None
    except Exception as e:  # noqa: BLE001 - we want to report any failure, not crash
        return None, f"{url} -> {type(e).__name__}: {e}"


# ------------------------------------------------------------------ names --------------
def normalize_name(name):
    s = (name or "").lower().replace(".", "").replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    out = " ".join(t for t in s.split() if t not in _SUFFIXES)
    return C.NAME_ALIASES.get(out, out)


def availability_key(injury_status, status):
    st = (status or "").lower()
    if any(x in st for x in ("injured reserve", "physically unable", "non football", "suspended")):
        return "IR"
    inj = (injury_status or "").strip()
    if inj in ("IR", "PUP", "Sus", "NFI"):
        return "IR"
    return inj if inj in C.AVAILABILITY else "Healthy"


# ------------------------------------------------------------------ Sleeper ------------
def load_sleeper_players():
    data, err = _get(f"{SLEEPER}/players/nfl", timeout=120)
    if err or not isinstance(data, dict):
        return {}, err or "players endpoint returned unexpected data"
    out = {}
    for pid, p in data.items():
        pos = p.get("position")
        if pos not in C.POSITIONS:
            continue
        name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        if not name:
            continue
        out[str(pid)] = {"pid": str(pid), "name": name, "pos": pos, "team": p.get("team"),
                         "age": p.get("age"), "norm": normalize_name(name),
                         "avail": availability_key(p.get("injury_status"), p.get("status"))}
    return out, None


def load_state():
    data, err = _get(f"{SLEEPER}/state/nfl", timeout=20)
    return (data if isinstance(data, dict) else None), err


def _canon_sleeper(row):
    f = lambda k: float(row.get(k) or 0.0)  # noqa: E731
    c = {"pass_att": f("pass_att"), "pass_cmp": f("pass_cmp"), "pass_yd": f("pass_yd"),
         "pass_td": f("pass_td"), "pass_int": f("pass_int"), "rush_yd": f("rush_yd"),
         "rush_td": f("rush_td"), "rec": f("rec"), "rec_yd": f("rec_yd"), "rec_td": f("rec_td"),
         "fum_lost": f("fum_lost"), "two_pt": f("pass_2pt") + f("rush_2pt") + f("rec_2pt"),
         "ret_td": f("kr_td") + f("pr_td")}
    for k in ("pass_td_40p", "rush_td_40p", "rec_td_40p"):
        if k in row:
            c[k] = f(k)
    return c


def _sleeper_rows(data):
    """Yields (player_id, stats_row) from either Sleeper response shape:
    old = {pid: {stat: value}}, new = [{"player_id": pid, "stats": {stat: value}}]."""
    if isinstance(data, dict):
        for pid, row in data.items():
            if isinstance(row, dict):
                yield str(pid), row
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("player_id") and isinstance(item.get("stats"), dict):
                yield str(item["player_id"]), item["stats"]


def load_stats_sleeper(season, last_week):
    weekly, errs, got = defaultdict(list), [], 0
    for wk in range(1, last_week + 1):
        rows = []
        for tmpl in SLEEPER_STATS:
            data, err = _get(tmpl.format(season=season, week=wk))
            rows = list(_sleeper_rows(data)) if not err else []
            if rows:
                break
            errs.append(err or f"{tmpl.format(season=season, week=wk)}: empty")
        if not rows:
            continue
        got += 1
        for pid, row in rows:
            c = _canon_sleeper(row)
            if row.get("gp") or any(v for k, v in c.items()):
                weekly[pid].append((wk, c))
    return weekly, got, errs


def load_stats_nflverse(season, last_week, name_index):
    """Backup stats source. Rows are matched to Sleeper ids by normalized name + position."""
    errs = []
    for url in NFLVERSE:
        text, err = _get(url.format(season=season), timeout=120, as_text=True)
        if err:
            errs.append(err)
            continue
        weekly, weeks = defaultdict(list), set()
        for r in csv.DictReader(io.StringIO(text)):
            if r.get("season_type", "REG") != "REG":
                continue
            try:
                wk = int(r["week"])
            except (KeyError, ValueError):
                continue
            pos = r.get("position")
            if wk > last_week or pos not in C.POSITIONS:
                continue
            # first(): the column was renamed between file versions; use whichever exists
            first = lambda *ks: next((float(r[k] or 0) for k in ks if k in r), 0.0)  # noqa: E731
            total = lambda *ks: sum(float(r.get(k) or 0) for k in ks)  # noqa: E731
            c = {"pass_att": first("attempts"), "pass_cmp": first("completions"),
                 "pass_yd": first("passing_yards"), "pass_td": first("passing_tds"),
                 "pass_int": first("passing_interceptions", "interceptions"),
                 "rush_yd": first("rushing_yards"), "rush_td": first("rushing_tds"),
                 "rec": first("receptions"), "rec_yd": first("receiving_yards"),
                 "rec_td": first("receiving_tds"),
                 "fum_lost": total("rushing_fumbles_lost", "receiving_fumbles_lost", "sack_fumbles_lost"),
                 "two_pt": total("passing_2pt_conversions", "rushing_2pt_conversions",
                                 "receiving_2pt_conversions"),
                 "ret_td": first("special_teams_tds")}
            pids = name_index.get((normalize_name(r.get("player_display_name") or r.get("player_name")), pos))
            if pids:
                weekly[pids[0]].append((wk, c))
                weeks.add(wk)
        if weekly:
            return weekly, len(weeks), errs
        errs.append(f"{url}: no usable rows")
    return {}, 0, errs


# ------------------------------------------------------------------ market values ------
def load_market_values():
    data, err1 = _get(FANTASYCALC)
    if not err1 and isinstance(data, list):
        vals = {}
        for it in data:
            sid = (it.get("player") or {}).get("sleeperId")
            if sid:
                vals[str(sid)] = float(it.get("value") or 0.0)
        if vals:
            return vals, "FantasyCalc", None
    data, err2 = _get(DYNASTY_DEALER)
    if not err2 and isinstance(data, dict):
        vals = {str(p["sleeper_id"]): float(p.get("current_value") or 0.0)
                for p in data.get("players", []) if p.get("sleeper_id")}
        if vals:
            return vals, "Dynasty Dealer", None
    return {}, None, f"FantasyCalc: {err1 or 'no usable rows'} | Dynasty Dealer: {err2 or 'no usable rows'}"


# ------------------------------------------------------------------ matching -----------
def _best(pids, sleeper):
    """Prefer a player who is on an NFL team (skips retired players with the same name)."""
    return sorted(pids, key=lambda p: (sleeper[p]["team"] is None, p))[0]


def _active_count(pids, sleeper):
    return sum(1 for p in pids if sleeper[p]["team"])


def match_pid(name, pos, live):
    n = normalize_name(name)
    idx, sl = live["name_index"], live["sleeper"]
    if (n, pos) in idx:
        pids = idx[(n, pos)]
        how = "ambiguous (same name, same position)" if _active_count(pids, sl) > 1 else "exact"
        return _best(pids, sl), how
    close = difflib.get_close_matches(n, live["by_pos"].get(pos, []), n=1, cutoff=0.88)
    if close:
        return _best(idx[(close[0], pos)], sl), f"fuzzy ({close[0]})"
    return None, "unmatched"


def find_player(name, live):
    """Find any NFL player by name (used by the backtest for players no longer on a roster)."""
    n = normalize_name(name)
    for pos in C.POSITIONS:
        if (n, pos) in live["name_index"]:
            return _best(live["name_index"][(n, pos)], live["sleeper"])
    return None


def position_ranks(ppg, sleeper):
    """Season positional rank by total points (e.g. RB6), like Yahoo's 'Rank' column."""
    by_pos = defaultdict(list)
    for pid, (gp, season_ppg, _) in ppg.items():
        sp = sleeper.get(pid)
        if sp and gp >= C.RANK_MIN_GAMES:
            by_pos[sp["pos"]].append((gp * season_ppg, pid))
    ranks = {}
    for pos, xs in by_pos.items():
        for i, (_, pid) in enumerate(sorted(xs, reverse=True), start=1):
            ranks[pid] = f"{pos}{i}"
    return ranks


# ------------------------------------------------------------------ orchestration ------
def load_live(season=C.SEASON, last_week_override=None):
    """Everything that needs the network. Cached by the app for an hour."""
    errors = []
    sleeper, err = load_sleeper_players()
    if err:
        errors.append(err)
    state, err = load_state()
    if err:
        errors.append(err)
    current_week = int(state["week"]) if state and str(state.get("week", "")).isdigit() else None
    # Stats only through COMPLETED weeks (the current week may be in progress)
    last_week = last_week_override or (max(0, min(18, current_week - 1)) if current_week else 8)

    name_index = defaultdict(list)
    for pid, p in sleeper.items():
        name_index[(p["norm"], p["pos"])].append(pid)
    by_pos = {pos: [k[0] for k in name_index if k[1] == pos] for pos in C.POSITIONS}

    weekly, weeks_loaded, stats_source = {}, 0, None
    if sleeper:
        weekly, weeks_loaded, errs = load_stats_sleeper(season, last_week)
        stats_source = "Sleeper" if weeks_loaded else None
        if weeks_loaded == 0:
            errors.extend(errs[:2])
            weekly, weeks_loaded, errs = load_stats_nflverse(season, last_week, name_index)
            stats_source = "nflverse" if weeks_loaded else None
            errors.extend(errs[:2])
        elif weeks_loaded < last_week:
            errors.append(f"Sleeper stats: only {weeks_loaded} of {last_week} weeks loaded")

    ppg = {}
    for pid, wk in weekly.items():
        pts = [score_week(c) for _, c in sorted(wk, key=lambda x: x[0])]
        if pts:
            ppg[pid] = (len(pts), sum(pts) / len(pts), sum(pts[-3:]) / len(pts[-3:]))

    market, market_source, err = load_market_values()
    if err:
        errors.append(err)
    return {"sleeper": sleeper, "name_index": dict(name_index), "by_pos": by_pos, "ppg": ppg,
            "ranks": position_ranks(ppg, sleeper), "market": market,
            "market_source": market_source, "stats_source": stats_source,
            "weeks_loaded": weeks_loaded, "last_week": last_week,
            "current_week": current_week, "errors": errors}


def build_table(rosters, live, settings):
    """Joins rosters.json (names + positions only; any other fields are ignored) to live data."""
    sleeper, market = live["sleeper"], live["market"]
    players, rostered, unmatched, flagged = {}, set(), [], []
    for team, entries in rosters.items():
        plist = []
        for e in entries:
            pid, how = match_pid(e["name"], e["pos"], live) if sleeper else (None, "no player feed")
            sp = sleeper.get(pid, {})
            gp, season_ppg, last3 = live["ppg"].get(pid, (0, 0.0, 0.0))
            if pid:
                rostered.add(pid)
                if how != "exact":
                    flagged.append((team, e["name"], how))
            elif sleeper:
                unmatched.append((team, e["name"]))
            plist.append({"name": e["name"], "pos": e["pos"], "pid": pid, "nfl_team": sp.get("team"),
                          "age": sp.get("age"), "avail": sp.get("avail", "Healthy"), "gp": gp,
                          "season_ppg": season_ppg, "last3_ppg": last3,
                          "rank": live["ranks"].get(pid),
                          "market": market.get(pid) if (pid and market) else None, "match": how})
        players[team] = plist

    # Free-agent pool (healthy, on an NFL team, not rostered in this league) sets replacement level
    fa = {pos: [] for pos in C.POSITIONS}
    for pid, (gp, season_ppg, last3) in live["ppg"].items():
        sp = sleeper.get(pid)
        if pid in rostered or gp < C.FA_MIN_GAMES or not sp or not sp["team"] or sp["avail"] != "Healthy":
            continue
        fa[sp["pos"]].append(eng.recent_weighted(season_ppg, last3))
    return {"players": players, "fa": fa, "unmatched": unmatched, "flagged": flagged}
