"""Reads The Fam League straight from the Yahoo Fantasy API (read-only).

Credentials live in Streamlit Secrets, never in this public repo:
    [yahoo]
    client_id = "..."
    client_secret = "..."
    refresh_token = "..."
    league_key = "470.l.516735"

Mental model: the refresh token is a valet key. Each load trades it for a one-hour access
token, then reads standings, rosters, and the remaining schedule. Nothing is ever written
back to Yahoo.
"""
import datetime as dt
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import requests

import config as C

TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
API = "https://fantasysports.yahooapis.com/fantasy/v2"
REDIRECT_URI = "https://localhost:8080"   # must match the redirect registered on the Yahoo app
TIMEOUT = 20
REQUIRED = ("client_id", "client_secret", "refresh_token", "league_key")


class YahooError(Exception):
    """A problem talking to Yahoo. Messages are safe to show on screen (no secrets)."""


# ------------------------------------------------------------------------- helpers -----
def clean_name(s):
    """Yahoo sometimes uses curly apostrophes (DQ’s). Straighten them so names match the
    rest of the app, and collapse stray spaces."""
    return " ".join((s or "").replace("’", "'").replace("‘", "'").split())


def _text(el, path, default=None):
    x = el.find(path) if el is not None else None
    return x.text if (x is not None and x.text is not None) else default


def _num(el, path, cast=float):
    try:
        return cast(_text(el, path))
    except (TypeError, ValueError):
        return cast(0)


def _strip_ns(root):
    """Yahoo's XML has a namespace on every tag; drop it so paths stay readable."""
    for el in root.iter():
        el.tag = el.tag.split("}", 1)[-1]
    return root


# ---------------------------------------------------------------------------- auth -----
def access_token(cfg):
    """Swap the saved refresh token for a fresh one-hour access token."""
    try:
        r = requests.post(TOKEN_URL, auth=(cfg["client_id"], cfg["client_secret"]),
                          data={"grant_type": "refresh_token",
                                "refresh_token": cfg["refresh_token"],
                                "redirect_uri": cfg.get("redirect_uri", REDIRECT_URI)},
                          timeout=TIMEOUT)
    except requests.RequestException as e:
        raise YahooError(f"Couldn't reach Yahoo sign-in ({type(e).__name__}).") from None
    if r.status_code != 200:
        try:
            code = str(r.json().get("error", ""))[:60]
        except ValueError:
            code = ""
        raise YahooError(f"Yahoo sign-in refused the saved credentials (HTTP {r.status_code} "
                         f"{code}). Check the Client ID, Client Secret, and refresh token in "
                         "Streamlit Secrets.")
    tok = r.json().get("access_token")
    if not tok:
        raise YahooError("Yahoo sign-in answered without an access token.")
    return tok


def _get(token, path):
    """GET one API path and return the parsed XML root (namespace removed)."""
    try:
        r = requests.get(f"{API}/{path}", headers={"Authorization": f"Bearer {token}"},
                         timeout=TIMEOUT)
    except requests.RequestException as e:
        raise YahooError(f"Couldn't reach the Yahoo Fantasy API ({type(e).__name__}).") from None
    if r.status_code != 200:
        why = ""
        try:
            why = _text(_strip_ns(ET.fromstring(r.content)), ".//description", "")[:120]
        except ET.ParseError:
            pass
        raise YahooError(f"Yahoo Fantasy API error on '{path.split('/')[0]}' "
                         f"(HTTP {r.status_code}) {why}".strip())
    return _strip_ns(ET.fromstring(r.content))


# ------------------------------------------------------------------------- parsers -----
# Kept separate from the network calls so they can be tested on saved responses.
def parse_standings(root):
    """Returns (league info, standings rows, {team name: team_key})."""
    lg = root.find("league")
    if lg is None or lg.find("standings") is None:
        raise YahooError("Yahoo standings response had no league standings in it.")
    info = {"name": clean_name(_text(lg, "name")),
            "current_week": _num(lg, "current_week", int) or None}
    rows, keys = [], {}
    for t in lg.find("standings").iter("team"):
        name = clean_name(_text(t, "name"))
        ts = t.find("team_standings")
        ot = ts.find("outcome_totals") if ts is not None else None
        pf = _num(ts, "points_for") or _num(t, "team_points/total")
        rows.append({"team": name, "w": _num(ot, "wins", int), "l": _num(ot, "losses", int),
                     "t": _num(ot, "ties", int), "pf": round(pf, 2),
                     "pa": round(_num(ts, "points_against"), 2)})
        keys[name] = _text(t, "team_key")
    if len(keys) != len(rows) or not all(keys.values()):
        raise YahooError("Yahoo standings had duplicate or missing team names.")
    return info, rows, keys


def parse_roster(root):
    """Offensive players only (QB/RB/WR/TE), same shape as rosters.json plus a few extras.
    K and DEF are skipped on purpose: the app doesn't value them."""
    out = []
    for p in root.iter("player"):
        pos = _text(p, "primary_position")
        if pos not in C.POSITIONS:
            continue
        out.append({"name": clean_name(_text(p, "name/full")), "pos": pos,
                    "slot": _text(p, "selected_position/position"),   # e.g. BN, IR, WR
                    "nfl_team": _text(p, "editorial_team_abbr")})
    return out


def parse_matchups(root):
    """[[team, team], ...] for one week's scoreboard."""
    games = []
    for m in root.iter("matchup"):
        names = [clean_name(_text(t, "name")) for t in m.iter("team")]
        if len(names) != 2:
            raise YahooError("A Yahoo matchup didn't have exactly two teams.")
        games.append(names)
    return games


# ---------------------------------------------------------------------------- main -----
def fetch_league(cfg):
    """Everything the app needs, in the same shapes as the backup JSON files.
    Raises YahooError on any problem, so the caller can fall back to the files."""
    missing = [k for k in REQUIRED if not cfg.get(k)]
    if missing:
        raise YahooError(f"Streamlit Secrets [yahoo] is missing: {', '.join(missing)}.")
    token = access_token(cfg)
    lk = cfg["league_key"]

    info, rows, keys = parse_standings(_get(token, f"league/{lk}/standings"))
    played = max(r["w"] + r["l"] + r["t"] for r in rows)
    weeks = list(range(played + 1, C.LAST_REGULAR_SEASON_WEEK + 1))

    # 14 roster calls + a few scoreboard calls, run side by side so loading stays quick
    with ThreadPoolExecutor(max_workers=6) as pool:
        roster_jobs = {t: pool.submit(_get, token, f"team/{k}/roster") for t, k in keys.items()}
        week_jobs = {w: pool.submit(_get, token, f"league/{lk}/scoreboard;week={w}") for w in weeks}
        rosters = {t: parse_roster(j.result()) for t, j in roster_jobs.items()}
        schedule = {str(w): parse_matchups(j.result()) for w, j in week_jobs.items()}

    empty = [t for t, ps in rosters.items() if not ps]
    if empty:
        raise YahooError(f"Yahoo returned empty rosters for: {', '.join(empty)}.")
    now = dt.datetime.now(ZoneInfo("America/New_York"))
    return {"rosters": rosters,
            "standings": {"_note": "Live from Yahoo", "teams": rows},
            "schedule": {"_note": "Live from Yahoo", "weeks": schedule},
            "league_name": info["name"], "current_week": info["current_week"],
            "fetched_at": now.strftime("%a %b %d, %I:%M %p ET").replace(" 0", " ")}


def roster_diff(live_rosters, file_rosters):
    """What Yahoo has that the backup files don't, and the reverse. Used on the Data check
    tab to confirm the two agree (and to show how stale the files are)."""
    out = []
    for team in sorted(set(live_rosters) | set(file_rosters)):
        a = {p["name"] for p in live_rosters.get(team, [])}
        b = {p["name"] for p in file_rosters.get(team, [])}
        if a != b:
            out.append({"Team": team, "Only on Yahoo": ", ".join(sorted(a - b)) or "-",
                        "Only in backup file": ", ".join(sorted(b - a)) or "-"})
    return out
