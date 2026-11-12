"""FleeceGuard - league trade checker. Run with: streamlit run app.py"""
import copy
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

import backtest as bt
import config as C
import data_sources as ds
import engine as eng
import theme

st.set_page_config(page_title="FleeceGuard", page_icon="🛡️", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown(theme.CSS, unsafe_allow_html=True)
HERE = Path(__file__).parent


@st.cache_data(ttl=60)
def read_json(name):
    p = HERE / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


@st.cache_data(ttl=3600, show_spinner="Loading live stats, injuries, and player values...")
def live_data(completed_weeks):
    return ds.load_live(completed_weeks_hint=completed_weeks)


rosters_raw = read_json("rosters.json")
standings_raw = read_json("standings.json")
schedule_raw = read_json("schedule.json")
if not (rosters_raw and standings_raw and schedule_raw):
    st.error("Missing rosters.json, standings.json, or schedule.json in the repo.")
    st.stop()

standings = {t["team"]: t for t in standings_raw["teams"]}
schedule = schedule_raw["weeks"]
problems = eng.validate_league(rosters_raw, standings, schedule)
if problems:
    st.error("League files don't line up. Fix these before trusting any verdict:")
    for p in problems:
        st.write("- " + p)
    st.stop()

st.markdown(theme.header_html(C.LEAGUE_NAME, C.SEASON), unsafe_allow_html=True)
st.caption("Trade checker: rest-of-season player value + lineup impact + playoff odds. "
           "A reference for the league vote, not a replacement for it.")
with st.expander("What the results mean"):
    st.write(f"- **NO CONCERNS**: the value gap is under {C.FAIR_GAP:.0%}, or too small to "
             "matter. Not grounds for a veto.\n"
             f"- **ONE-SIDED, BUT ALLOWED**: one side likely overpaid ({C.FAIR_GAP:.0%}-"
             f"{C.VETO_GAP:.0%} gap). Still not grounds for a veto.\n"
             f"- **NEEDS A SECOND LOOK**: a very large gap (over {C.VETO_GAP:.0%}). Ask both "
             "managers to explain before voting. Not an automatic veto.\n"
             "- **Trade value** is what a player is worth in a trade for the rest of this "
             "season. It comes from FantasyCalc, which tracks thousands of real trades. "
             "Players are shown by rank, so \"#6 WR in trade value\" means the 6th most "
             "valuable receiver.\n"
             "- **Lineup numbers** show how each team's starting lineup changes. They never "
             "count against a trade, and a trade that fixes a weak spot gets credit for it.\n"
             "- **The gap** weights stars a little extra, because one great player is worth "
             "more than two decent ones. So it can be somewhat larger than the raw value "
             "numbers in the players table suggest.\n"
             f"- **Keepers** ({C.KEEPER_RULE}) are mentioned when a rookie is in the deal, "
             "but they are not scored.")
if dt.date.today() > C.TRADE_DEADLINE:
    st.info(f"The trade deadline ({C.TRADE_DEADLINE:%b %d}) has passed. "
            "Use this to review past trades or plan for next season.")

# ---- settings ---------------------------------------------------------------------------
# Fixed on purpose: thresholds live in config.py so every manager sees the same result.
# (There used to be sliders here; anyone could move them and screenshot a different verdict.)
S = eng.default_settings()

games_played = max(t["w"] + t["l"] + t.get("t", 0) for t in standings.values())
live = live_data(games_played)
table = ds.build_table(rosters_raw, live, S)
repl = eng.replacement_levels(table["fa"]) if live["ppg"] else None   # None = no stats feed
has_market = bool(live["market"])          # True only when FantasyCalc values loaded

# A trade proposed Tuesday-Friday clears the 2-day review before Sunday, so it counts for the
# current NFL week; one proposed Saturday-Monday counts from the following week.
today = dt.datetime.now(ZoneInfo("America/New_York"))
trade_week = None
if live["current_week"]:
    this_week = today.weekday() in C.TRADE_COUNTS_THIS_WEEK_DAYS
    trade_week = live["current_week"] + (0 if this_week else 1)

# Rebuilt every rerun on purpose: it's fast, and caching it once served stale results.
ctx = eng.build_context(copy.deepcopy(table["players"]), repl, standings, schedule, S,
                        has_market, live["current_week"], trade_week)
value_label = {"market": live["market_source"],
               "estimate": "FleeceGuard estimate (FantasyCalc unavailable)"}.get(
                   ctx["value_source"], "none")

if ctx["value_source"] == "estimate":
    st.warning("FantasyCalc player values could not be loaded, so FleeceGuard is using its own "
               "rougher estimate. See the Data check tab.")
elif not live["stats_source"] or not has_market:
    st.warning("Some live data failed to load, so results are less reliable. See the Data check tab.")

tab_trade, tab_back, tab_teams, tab_data = st.tabs(
    ["Trade analyzer", "Backtest", "Teams & playoff odds", "Data check"])


def player_rows(players):
    return [{"Player": p["name"], "Pos": p["pos"], "NFL": p["nfl_team"] or "-",
             "Age": p["age"] or "-", "Status": p["avail"], "Rank": p["rank"] or "-",
             "GP": p["gp"], "Season PPG": round(p["season_ppg"], 1),
             "Model PPG": round(p["ppg"], 1),
             "Value rank": p.get("value_rank") or "-",
             "Value (0-10,000)": "-" if p["market"] is None else f"{p['market']:,.0f}",
             "Rookie": "Yes" if p.get("rookie") else ""} for p in players]


# ---- Trade analyzer ---------------------------------------------------------------------
with tab_trade:
    teams = list(rosters_raw)
    c1, c2 = st.columns(2)
    ta = c1.selectbox("Proposing team", teams, index=0)
    tb = c2.selectbox("Receiving team", [t for t in teams if t != ta], index=0)
    names = lambda t: [p["name"] for p in ctx["rosters"][t]]  # noqa: E731
    s1, s2 = st.columns(2)
    send_a = s1.multiselect(f"{ta} sends", names(ta))
    send_b = s2.multiselect(f"{tb} sends", names(tb))
    key = (ta, tb, tuple(send_a), tuple(send_b), json.dumps(S, sort_keys=True))

    if st.button("Run analysis", type="primary"):
        if not send_a or not send_b:
            st.warning("Pick at least one player on each side.")
        else:
            st.session_state["result"] = (key, eng.evaluate_trade(ctx, ta, send_a, tb, send_b))

    # Kept in session state so the result survives reruns (e.g. clicking the WhatsApp box);
    # dropped automatically if the selected trade or settings change.
    saved = st.session_state.get("result")
    if saved and saved[0] == key:
        res = saved[1]
        # Green / blue / orange. Nothing is red: no result is an accusation.
        box = {0: st.success, 1: st.info, 2: st.warning, None: st.info}[res["level"]]
        box(f"**{res['headline']}**\n\n{res['guidance']}")
        # One row per question, in plain words
        if res["summary"]:
            st.markdown("| | |\n|---|---|\n" +
                        "\n".join(f"| **{k}** | {v} |" for k, v in res["summary"]))
        for line in res["details"]:
            st.write("- " + line)
        st.subheader("This season, after the trade")
        m1, m2 = st.columns(2)
        for col, k in ((m1, "a"), (m2, "b")):
            s = res["sides"][k]
            col.metric(f"{s['team']} starting lineup ({s['tier']})",
                       f"{s['lineup_post']:.1f} pts/week", f"{s['lineup_delta']:+.1f}")
            col.metric(f"{s['team']} playoff odds", f"{s['playoff_post']:.0%}",
                       f"{s['playoff_delta']*100:+.1f} pts")
        st.subheader("Players in the deal")
        st.dataframe(player_rows(res["sides"]["a"]["sends"] + res["sides"]["b"]["sends"]))
        st.text_area("Copy for WhatsApp", eng.whatsapp_text(res), height=200)

# ---- Backtest ----------------------------------------------------------------------------
with tab_back:
    st.write("Your past trades, judged on **player value only** with today's rest-of-season "
             "values (hindsight, and no roster context). Compare the bot to the league's vote.")
    if not has_market:
        st.warning("No player values loaded, so the backtest can't run. See the Data check tab.")
    else:
        rows, warns = bt.run_backtest(live, S)
        # Yahoo's history doesn't say who proposed a past trade, so these are just numbered
        st.dataframe([{"#": r["id"], "Date": r["date"], "Team 1": r["team_a"],
                       "Team 1 gets": r["a_gets"], "Team 2": r["team_b"],
                       "Team 2 gets": r["b_gets"], "League": r["league"],
                       "Bot": r["bot"], "Gap": "-" if r["gap"] is None else f"{r['gap']:.0%}",
                       "Bot says ahead": r["winner"] or "-"} for r in rows])
        for w in warns:
            st.caption("⚠️ " + w)

# ---- Teams ----------------------------------------------------------------------------------
with tab_teams:
    order = sorted(rosters_raw, key=lambda t: -ctx["probs"][t])
    st.dataframe([{"Team": t, "Record": f"{standings[t]['w']}-{standings[t]['l']}",
                   "PF": standings[t]["pf"], "Lineup PPG (model)": round(ctx["lineups"][t], 1),
                   "Playoff odds": f"{ctx['probs'][t]:.0%}", "Tier": eng.tier(ctx["probs"][t])}
                  for t in order])
    st.caption(f"{ctx['weeks_left']} regular-season weeks simulated. Weekly score volatility "
               "and K/DEF scoring are assumptions, so treat odds as a guide.")
    pick = st.selectbox("View roster", list(rosters_raw))
    st.dataframe(player_rows(sorted(ctx["rosters"][pick], key=lambda p: -p["ppg"])))

# ---- Data check -----------------------------------------------------------------------------
with tab_data:
    # Live data is cached for an hour; this forces a fresh download (e.g. after injury news)
    if st.button("Refresh live data"):
        st.cache_data.clear()
        st.session_state.pop("result", None)
        st.rerun()
    st.write(f"**Stats source:** {live['stats_source'] or 'none'} "
             f"({live['weeks_loaded']} of {live['last_week']} weeks) | "
             f"**Player values:** {value_label} | "
             f"**NFL week:** {live['current_week'] or 'unknown'}")
    st.write("**Replacement level (PPG, from real free agents):**",
             {k: round(v, 1) for k, v in ctx["repl"].items()} if repl else "not available (no stats)")
    if table["unmatched"]:
        st.error("Unmatched roster players (no live stats or player value). "
                 "Fix the spelling in rosters.json or add an alias in config.py:")
        st.write(table["unmatched"])
    if table["flagged"]:
        st.warning("Name matches to double-check:")
        st.write(table["flagged"])
    no_value = [(t, p["name"]) for t, ps in ctx["rosters"].items() for p in ps
                if p["pid"] and p["market"] is None and p["ppg"] > ctx["repl"][p["pos"]]]
    if has_market and no_value:
        st.warning("Useful players with no player value in the feed:")
        st.write(no_value)
    if not live["errors"] and not table["unmatched"]:
        st.success("Everything loaded.")
    for e in live["errors"]:
        st.caption("⚠️ " + e)

if (live["market_source"] or "").startswith("FantasyCalc"):
    st.caption("Rest-of-season player values from [FantasyCalc](https://www.fantasycalc.com)")
st.caption("FleeceGuard is an independent league tool. It is not affiliated with Yahoo.")
