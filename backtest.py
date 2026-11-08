"""Runs past league trades through the ASSET lens and compares to what the league voted.
Hindsight caveat: uses today's market values, not values on the trade date."""
import json
from pathlib import Path

import data_sources as ds
import engine as eng

HERE = Path(__file__).parent


def _player(name, live):
    pid = ds.find_player(name, live)
    sp = live["sleeper"].get(pid, {}) if pid else {}
    return {"name": name, "pid": pid, "market": live["market"].get(pid) if pid else None,
            "age": sp.get("age")}


def run_backtest(live, settings=None):
    """Returns (rows, warnings). One row per past trade."""
    trades = json.loads((HERE / "past_trades.json").read_text(encoding="utf-8"))["trades"]
    rows, warnings = [], []
    for t in trades:
        a_gets = [_player(n, live) for n in t["a_receives"]]
        b_gets = [_player(n, live) for n in t["b_receives"]]
        missing = [p["name"] for p in a_gets + b_gets if p["market"] is None]
        if missing:
            warnings.append(f"Trade {t['id']}: no market value for {', '.join(missing)}")
        # Team A receives a_gets, so team B is the one GIVING a_gets (and vice versa)
        r = eng.asset_only(b_gets, a_gets, settings)
        ahead = t["team_a"] if r["a_is_ahead"] else t["team_b"]
        rows.append({"id": t["id"], "date": t["date"], "team_a": t["team_a"],
                     "a_gets": ", ".join(t["a_receives"]), "team_b": t["team_b"],
                     "b_gets": ", ".join(t["b_receives"]), "league": t["outcome"],
                     "bot": "INCOMPLETE" if missing else r["verdict"],
                     "gap": None if missing else r["gap"],
                     "winner": None if missing else ahead})
    return rows, warnings
