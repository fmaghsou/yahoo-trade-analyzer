"""FleeceGuard v2 engine. Pure Python: no network, no Streamlit, deterministic output.

Two lenses decide a trade:
  1. ASSET lens: dynasty market value each side NETS (what it receives minus what it is
     forced to drop), with a consolidation premium for stars.
  2. WIN-NOW lens: change in lineup PPG and playoff probability, from a simulation that uses
     the real remaining schedule.
"""
import random

import config as C


def default_settings():
    return {
        "sd": C.WEEKLY_SD, "n_sims": C.N_SIMS, "shrink_games": C.SHRINK_GAMES,
        "fair_gap": C.FAIR_GAP, "veto_gap": C.VETO_GAP, "power": C.CONSOLIDATION_POWER,
        "win_now_gain": C.WIN_NOW_GAIN, "min_abs_gap": C.MIN_ABS_GAP,
    }


# ---------------------------------------------------------------- player value (PPG) ----
def recent_weighted(season_ppg, last3_ppg):
    """Season average with extra weight on the last 3 games played."""
    return C.W_SEASON * season_ppg + C.W_RECENT * last3_ppg


def shrunk_ppg(season_ppg, last3_ppg, gp, repl_level, k=C.SHRINK_GAMES):
    """Real production, pulled toward replacement level when the sample is small.
    Mental model: a kicker who's 3-for-3 isn't a 100% kicker. 0 games = replacement level."""
    if gp <= 0:
        return repl_level
    w = gp / (gp + k)
    return w * recent_weighted(season_ppg, last3_ppg) + (1 - w) * repl_level


def replacement_levels(fa_ppgs):
    """Replacement PPG per position = Nth best free agent, from real stats.
    Returns None if there's no stats feed (the win-now lens is then switched off)."""
    out = {}
    for pos in C.POSITIONS:
        xs = sorted(fa_ppgs.get(pos, []), reverse=True)
        if len(xs) < C.REPLACEMENT_RANK:
            return None
        out[pos] = xs[C.REPLACEMENT_RANK - 1]
    return out


NO_STATS_REPL = dict.fromkeys(C.POSITIONS, 0.0)


def prepare_players(players, repl, settings):
    """Adds base_ppg (real stats, shrunk) and ppg (base x availability) to each player dict."""
    for p in players:
        base = shrunk_ppg(p.get("season_ppg", 0.0), p.get("last3_ppg", 0.0), p.get("gp", 0),
                          repl[p["pos"]], settings["shrink_games"])
        p["base_ppg"] = base
        p["ppg"] = base * C.AVAILABILITY.get(p.get("avail", "Healthy"), 1.0)


# ------------------------------------------------------------------- lineup solver -----
def lineup_ppg(roster, repl):
    """Best starting lineup PPG. Phantom free agents at replacement level fill any hole."""
    flex_pool, total = [], 0.0
    for pos in C.POSITIONS:
        slots = C.ROSTER_SLOTS.get(pos, 0)
        vals = [p["ppg"] for p in roster if p["pos"] == pos]
        vals += [repl[pos]] * (slots + C.ROSTER_SLOTS["FLEX"])
        vals.sort(reverse=True)
        total += sum(vals[:slots])
        if pos in C.FLEX_POSITIONS:
            flex_pool += vals[slots:]
    flex_pool.sort(reverse=True)
    return total + sum(flex_pool[:C.ROSTER_SLOTS["FLEX"]])


def lineup_starters(roster, repl):
    """The players in the best starting lineup (same logic as lineup_ppg), by object id."""
    starters, flex_pool = set(), []
    for pos in C.POSITIONS:
        slots = C.ROSTER_SLOTS.get(pos, 0)
        ps = sorted((p for p in roster if p["pos"] == pos), key=lambda p: -p["ppg"])
        for p in ps[:slots]:
            if p["ppg"] >= repl[pos]:
                starters.add(id(p))
        if pos in C.FLEX_POSITIONS:
            flex_pool += ps[slots:]
    for p in sorted(flex_pool, key=lambda p: -p["ppg"])[:C.ROSTER_SLOTS["FLEX"]]:
        starters.add(id(p))
    return starters


def _ir_eligible(p):
    return p.get("avail") in C.IR_ELIGIBLE


def _excess(roster):
    """Players over the cap. Up to 2 IR-eligible players sit in IR slots for free."""
    ir_slotted = min(C.IR_SLOTS, sum(1 for p in roster if _ir_eligible(p)))
    return max(0, len(roster) - ir_slotted - C.ACTIVE_OFFENSE_CAP)


def forced_drops(before, keep, incoming, repl):
    """Roster-spot cost: who a team must cut BECAUSE OF THIS TRADE.
    Only the excess the trade creates is charged, so a team already at the limit (or whose
    K/DEF/IR setup differs from our assumption) isn't penalized for its existing roster.
    Dynasty logic: starters are protected; among bench players, cut the lowest market value
    first (a manager keeps a valuable stash), ties go to whoever hurts the lineup least."""
    roster_after = list(keep) + list(incoming)
    n = max(0, _excess(roster_after) - _excess(before))
    pool, dropped = list(keep), []
    for _ in range(n):
        starters = lineup_starters(pool + list(incoming), repl)
        cands = ([p for p in pool if id(p) not in starters and not _ir_eligible(p)]
                 or [p for p in pool if id(p) not in starters] or pool)
        worst = min(cands, key=lambda c: (
            c.get("market") or 0.0,
            -lineup_ppg([p for p in pool if p is not c] + list(incoming), repl)))
        pool.remove(worst)
        dropped.append(worst)
    return dropped


# ------------------------------------------------------------------ asset (dynasty) lens --
def side_score(players, power):
    """Sum of (value/10000)^power. power > 1 gives one star more weight than several mid players."""
    return 10000.0 * sum(((p.get("market") or 0.0) / 10000.0) ** power for p in players)


def _gap_level(net_a, net_b, S):
    """Returns (relative gap, level). Small absolute differences are always FAIR."""
    diff = abs(net_a - net_b)
    gap = diff / max(abs(net_a), abs(net_b), 1.0)
    if diff < S["min_abs_gap"] or gap < S["fair_gap"]:
        return gap, 0
    return gap, (1 if gap < S["veto_gap"] else 2)


# --------------------------------------------------------------- schedule helpers -------
def remaining_schedule(schedule, standings):
    """Drops weeks already counted in standings, so re-uploading standings never double counts."""
    played = max(s["w"] + s["l"] + s.get("t", 0) for s in standings.values())
    return {wk: games for wk, games in schedule.items()
            if played < int(wk) <= C.LAST_REGULAR_SEASON_WEEK}


def validate_league(rosters, standings, schedule):
    """Returns a list of problems (team-name mismatches, broken schedule weeks)."""
    probs, teams = [], set(rosters)
    if set(standings) != teams:
        probs.append(f"Team names differ between rosters and standings: "
                     f"{sorted(teams ^ set(standings))}")
    for wk, games in schedule.items():
        seen = [t for g in games for t in g]
        if sorted(seen) != sorted(teams):
            probs.append(f"Week {wk}: schedule doesn't list every team exactly once "
                         f"({sorted(set(seen) ^ teams) or 'duplicates'})")
    return probs


# --------------------------------------------------------------- playoff simulation -----
def simulate(standings, schedule, means_pre, sd, n, means_post=None, eff_week=None, seed=7):
    """Monte Carlo over the remaining schedule. Same seed => same random draws for pre- and
    post-trade runs, so the difference between them is not noise."""
    rng = random.Random(seed)
    teams = list(standings)
    base_w = {t: standings[t]["w"] + 0.5 * standings[t].get("t", 0) for t in teams}
    base_pf = {t: standings[t]["pf"] for t in teams}
    weeks = sorted(schedule, key=int)
    made = dict.fromkeys(teams, 0)
    for _ in range(n):
        w, pf = dict(base_w), dict(base_pf)
        for wk in weeks:
            m = means_post if (means_post is not None and int(wk) >= eff_week) else means_pre
            for a, b in schedule[wk]:
                sa, sb = rng.gauss(m[a], sd), rng.gauss(m[b], sd)
                pf[a] += sa
                pf[b] += sb
                if sa > sb:
                    w[a] += 1
                else:
                    w[b] += 1
        order = sorted(teams, key=lambda t: (w[t], pf[t]), reverse=True)
        for t in order[:C.PLAYOFF_TEAMS]:
            made[t] += 1
    return {t: made[t] / n for t in teams}


def tier(prob):
    if prob >= C.CONTENDER_PROB:
        return "Contender"
    return "Seller" if prob < C.SELLER_PROB else "Bubble"


# ---------------------------------------------------------------------- context --------
def build_context(rosters, repl, standings, schedule, settings=None, has_market=True,
                  current_week=None):
    """rosters: {team: [player dicts]}. Prepares players and runs the baseline simulation.
    repl: replacement levels from real stats, or None if the stats feed is down.
    current_week: NFL week in progress (from the live feed); a trade lands the week after."""
    settings = settings or default_settings()
    has_stats = repl is not None
    repl = repl if has_stats else NO_STATS_REPL
    for plist in rosters.values():
        prepare_players(plist, repl, settings)
    sched = remaining_schedule(schedule, standings)
    first = min((int(w) for w in sched), default=C.LAST_REGULAR_SEASON_WEEK + 1)
    eff_week = max(first, (current_week or first) + 1)
    lineups = {t: lineup_ppg(r, repl) for t, r in rosters.items()}
    games = sum(s["w"] + s["l"] + s.get("t", 0) for s in standings.values())
    league_avg = sum(s["pf"] for s in standings.values()) / max(1, games)
    # Anchor the model's absolute scale to real league scoring (it excludes K and DEF);
    # team-vs-team differences still come from the player-level model.
    shift = league_avg - sum(lineups.values()) / len(lineups)
    means = {t: v + shift for t, v in lineups.items()}
    probs = simulate(standings, sched, means, settings["sd"], settings["n_sims"])
    return {"rosters": rosters, "repl": repl, "standings": standings, "schedule": sched,
            "settings": settings, "lineups": lineups, "shift": shift, "means": means,
            "probs": probs, "has_market": has_market, "has_stats": has_stats, "eff_week": eff_week,
            "weeks_left": len(sched)}


# ---------------------------------------------------------------------- verdict --------
# Wording is deliberate: none of these tells the league to veto.
LEVELS = ["NO CONCERNS", "ONE-SIDED, BUT ALLOWED", "NEEDS A SECOND LOOK"]
GUIDANCE = [
    "Not grounds for a veto.",
    "One side likely overpaid, but that is not grounds for a veto. Managers are allowed to "
    "make trades that others wouldn't.",
    "Before voting, ask both managers to explain the deal. A second look is not an "
    "automatic veto.",
]


def even_out_hint(winner, keep_winner, diff, power):
    """What the side getting more value could add to balance the trade.
    diff is in star-premium units, so convert it back to a plain dynasty value."""
    need = 10000.0 * (diff / 10000.0) ** (1.0 / power)
    # Suggest up to two of the winner's own players whose value is close to what's needed
    close = sorted((p for p in keep_winner
                    if p.get("market") and abs(p["market"] - need) <= 0.2 * need),
                   key=lambda p: abs(p["market"] - need))[:2]
    eg = f" (for example, {' or '.join(p['name'] for p in close)})" if close else ""
    return (f"To even it out: {winner} could add a player worth about {need:,.0f} in dynasty "
            f"value{eg}.")


def evaluate_trade(ctx, team_a, send_a, team_b, send_b):
    S, repl = ctx["settings"], ctx["repl"]
    ra, rb = ctx["rosters"][team_a], ctx["rosters"][team_b]
    out_a = [p for p in ra if p["name"] in send_a]
    out_b = [p for p in rb if p["name"] in send_b]
    keep_a = [p for p in ra if p["name"] not in send_a]
    keep_b = [p for p in rb if p["name"] not in send_b]
    drops_a = forced_drops(ra, keep_a, out_b, repl)
    drops_b = forced_drops(rb, keep_b, out_a, repl)
    post_a = [p for p in keep_a if p not in drops_a] + out_b
    post_b = [p for p in keep_b if p not in drops_b] + out_a

    # Win-now lens
    lu_pre = {team_a: ctx["lineups"][team_a], team_b: ctx["lineups"][team_b]}
    lu_post = {team_a: lineup_ppg(post_a, repl), team_b: lineup_ppg(post_b, repl)}
    if ctx["schedule"]:
        means_post = dict(ctx["means"])
        for t in (team_a, team_b):
            means_post[t] = lu_post[t] + ctx["shift"]
        probs_post = simulate(ctx["standings"], ctx["schedule"], ctx["means"], S["sd"],
                              S["n_sims"], means_post=means_post, eff_week=ctx["eff_week"])
    else:
        probs_post = dict(ctx["probs"])   # regular season over: odds can't change

    # Asset lens: what each side NETS = value received minus value of players it must cut
    sent = {team_a: side_score(out_a, S["power"]), team_b: side_score(out_b, S["power"])}
    net = {team_a: sent[team_b] - side_score(drops_a, S["power"]),
           team_b: sent[team_a] - side_score(drops_b, S["power"])}
    gap, level = _gap_level(net[team_a], net[team_b], S)
    value_winner = team_a if net[team_a] > net[team_b] else team_b
    value_loser = team_b if value_winner == team_a else team_a

    d_play = {t: probs_post[t] - ctx["probs"][t] for t in (team_a, team_b)}
    sides = {}
    for key, t, out, got, drops in (("a", team_a, out_a, out_b, drops_a),
                                    ("b", team_b, out_b, out_a, drops_b)):
        sides[key] = {"team": t, "sends": out, "receives": got, "drops": drops,
                      "lineup_pre": lu_pre[t], "lineup_post": lu_post[t],
                      "lineup_delta": lu_post[t] - lu_pre[t],
                      "playoff_pre": ctx["probs"][t], "playoff_post": probs_post[t],
                      "playoff_delta": d_play[t], "tier": tier(ctx["probs"][t]),
                      "net_value": net[t]}

    # Data-quality gate: a meaningful player with no market price makes the asset lens unreliable
    issues = []
    if ctx["has_market"]:
        for p in out_a + out_b:
            if p.get("market") is None and p["base_ppg"] > repl[p["pos"]] + 2.0:
                issues.append(f"{p['name']} has no market value in the feed")
    res = {"gap": gap if ctx["has_market"] else None, "value_winner": value_winner,
           "value_loser": value_loser, "sides": sides, "issues": issues, "notes": []}

    if not ctx["has_stats"] and not ctx["has_market"]:
        issues.append("no live stats and no market values loaded")
    if issues:
        res.update(verdict="INCOMPLETE DATA", level=None)
    elif ctx["has_market"]:
        if level > 0 and d_play[value_loser] >= S["win_now_gain"]:
            level -= 1
            res["notes"].append(
                f"{value_loser} gives up more dynasty value but gains "
                f"{d_play[value_loser]*100:.1f} pts of playoff odds, so this reads as a "
                f"defensible win-now move (verdict softened one level).")
        # Context only (never counts against the trade): both sides are giving something up
        if level == 0 and d_play[value_winner] <= -S["win_now_gain"]:
            res["notes"].append(
                f"Context: {value_winner} gets more dynasty value but gives up "
                f"{-d_play[value_winner]*100:.1f} pts of playoff odds this season.")
        res.update(verdict=LEVELS[level], level=level)
        if level > 0:
            keep_w = keep_a if value_winner == team_a else keep_b
            res["notes"].append(even_out_hint(value_winner, keep_w,
                                              abs(net[team_a] - net[team_b]), S["power"]))
        if not ctx["has_stats"]:
            res["notes"].append("Live stats unavailable: verdict uses dynasty value only "
                                "(lineup and playoff numbers are not meaningful).")
    else:
        diff = abs(d_play[team_a] - d_play[team_b]) * 100
        level = 0 if diff < 8 else (1 if diff < 20 else 2)
        res.update(verdict=LEVELS[level], level=level)
        res["notes"].append("Market values unavailable: verdict uses the win-now lens only, "
                            "so treat it as a rough guide.")

    if not ctx["schedule"]:
        res["notes"].append("Regular season is over, so playoff odds don't move.")
    res["guidance"] = (GUIDANCE[res["level"]] if res["level"] is not None else
                       "Not enough data to judge this trade. Check the Data check tab.")
    res["reasons"] = _reasons(res)
    return res


def _reasons(res):
    out, a, b = [], res["sides"]["a"], res["sides"]["b"]
    if res["gap"] is not None:
        out.append(f"Dynasty value: {res['value_winner']} nets more, by {res['gap']:.0%} "
                   f"({max(a['net_value'], b['net_value']):,.0f} vs "
                   f"{min(a['net_value'], b['net_value']):,.0f}, after the star premium "
                   f"and forced drops).")
    for s in (a, b):
        out.append(f"{s['team']} ({s['tier']}): lineup {s['lineup_pre']:.1f} to "
                   f"{s['lineup_post']:.1f} PPG ({s['lineup_delta']:+.1f}); playoff odds "
                   f"{s['playoff_pre']:.0%} to {s['playoff_post']:.0%}.")
        if s["drops"]:
            out.append(f"{s['team']} must drop: {', '.join(p['name'] for p in s['drops'])}.")
    out.extend(res["notes"])
    out.extend(f"Data issue: {i}." for i in res["issues"])
    return out


def asset_only(players_a_sends, players_b_sends, settings=None):
    """Asset lens alone (used by the backtest, where roster context at the time is unknown)."""
    S = settings or default_settings()
    a_nets = side_score(players_b_sends, S["power"])   # A receives what B sends
    b_nets = side_score(players_a_sends, S["power"])
    gap, level = _gap_level(a_nets, b_nets, S)
    return {"gap": gap, "level": level, "verdict": LEVELS[level], "a_is_ahead": a_nets > b_nets}


def whatsapp_text(res):
    a, b = res["sides"]["a"], res["sides"]["b"]
    names = lambda ps: ", ".join(p["name"] for p in ps) or "nothing"  # noqa: E731
    lines = [f"🛡️ FleeceGuard: {res['verdict']}",
             res["guidance"],
             f"{a['team']} sends: {names(a['sends'])}",
             f"{b['team']} sends: {names(b['sends'])}"]
    if res["gap"] is not None:
        lines.append(f"Dynasty value gap: {res['gap']:.0%} in favor of {res['value_winner']}")
    for s in (a, b):
        lines.append(f"{s['team']}: playoff odds {s['playoff_pre']:.0%} to {s['playoff_post']:.0%}, "
                     f"lineup {s['lineup_delta']:+.1f} PPG")
        if s["drops"]:
            lines.append(f"{s['team']} must drop: {names(s['drops'])}")
    lines.extend(res["notes"])
    return "\n".join(lines)
