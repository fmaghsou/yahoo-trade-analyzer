"""FleeceGuard engine. Pure Python: no network, no Streamlit, deterministic output.

Two things are measured, both for the REST OF THIS SEASON (the league keeps only one rookie
per team, so long-term value is not scored):
  1. PLAYER VALUE: what each side nets in rest-of-season trade value (what it receives minus
     what it is forced to drop), with a mild premium for stars.
  2. LINEUP IMPACT: change in each team's starting lineup and playoff odds, from a simulation
     that uses the real remaining schedule.
"""
import random

import config as C


def default_settings():
    return {
        "sd": C.WEEKLY_SD, "n_sims": C.N_SIMS, "shrink_games": C.SHRINK_GAMES,
        "fair_gap": C.FAIR_GAP, "veto_gap": C.VETO_GAP, "power": C.CONSOLIDATION_POWER,
        "win_now_gain": C.WIN_NOW_GAIN, "lineup_gain": C.LINEUP_GAIN,
        "min_abs_gap": C.MIN_ABS_GAP,
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
    Starters are protected; among bench players, the lowest-value one is cut first, and
    ties go to whoever hurts the lineup least."""
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


# ------------------------------------------------------------------ player value ---------
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


def estimate_values(rosters, repl):
    """Backup when the market feed is down. A player's value = his weekly points above a
    replacement-level player at his position (already adjusted for injuries), scaled so the
    best rostered player is 10,000. Cruder than real trade prices, but honest and local."""
    players = [p for ps in rosters.values() for p in ps]
    above = {id(p): max(0.0, p["ppg"] - repl[p["pos"]]) for p in players}
    top = max(above.values(), default=0.0)
    if top <= 0:
        return False
    for p in players:
        p["market"] = 10000.0 * above[id(p)] / top
    for pos in C.POSITIONS:
        ranked = sorted((p for p in players if p["pos"] == pos), key=lambda p: -p["market"])
        for i, p in enumerate(ranked, start=1):
            p["value_rank"] = f"{pos}{i}"
    return True


def tier(prob):
    if prob >= C.CONTENDER_PROB:
        return "Contender"
    return "Long shot" if prob < C.SELLER_PROB else "Bubble"


# ---------------------------------------------------------------------- context --------
def build_context(rosters, repl, standings, schedule, settings=None, has_market=True,
                  current_week=None, trade_week=None):
    """rosters: {team: [player dicts]}. Prepares players and runs the baseline simulation.
    repl: replacement levels from real stats, or None if the stats feed is down.
    current_week: NFL week in progress (from the live feed).
    trade_week: first week a trade made today would count (the app works this out from the
    day of the week); defaults to the week after the current one."""
    settings = settings or default_settings()
    has_stats = repl is not None
    repl = repl if has_stats else NO_STATS_REPL
    for plist in rosters.values():
        prepare_players(plist, repl, settings)
    # Where player values come from: the market feed, or our own estimate if the feed is down
    value_source = "market" if has_market else None
    if not has_market and has_stats and estimate_values(rosters, repl):
        has_market, value_source = True, "estimate"
    sched = remaining_schedule(schedule, standings)
    first = min((int(w) for w in sched), default=C.LAST_REGULAR_SEASON_WEEK + 1)
    eff_week = max(first, trade_week if trade_week else (current_week or first) + 1)
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
            "weeks_left": len(sched), "value_source": value_source}


# ---------------------------------------------------------------------- verdict --------
# Wording is deliberate: none of these tells the league to veto.
LEVELS = ["NO CONCERNS", "ONE-SIDED, BUT ALLOWED", "NEEDS A SECOND LOOK"]


def _join(items):
    """'A', 'A and B', or 'A, B, and C'."""
    items = list(items)
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _label(p):
    """'Amon-Ra St. Brown (#4 WR in trade value)', or just the name if he has no rank."""
    vr = p.get("value_rank")
    if not vr:
        return p["name"]
    pos, num = vr.rstrip("0123456789"), vr[len(vr.rstrip("0123456789")):]
    return f"{p['name']} (#{num} {pos} in trade value)"


def _in_short(value_even, winner, loser, n_winner_gets, d_lineup, has_stats):
    """One plain sentence: who gets the better players, and what happens to the lineups."""
    value_part = ("The players are worth about the same" if value_even else
                  f"{winner} gets the more valuable {'player' if n_winner_gets == 1 else 'players'}")
    if not has_stats:
        return value_part + "."
    w_up, l_up = d_lineup[winner] >= 0.5, d_lineup[loser] >= 0.5
    w_down, l_down = d_lineup[winner] <= -0.5, d_lineup[loser] <= -0.5
    if w_up and l_up:
        lineup_part = "both starting lineups get better"
    elif l_up and w_down:
        lineup_part = f"{loser} gets the better lineup, while {winner}'s lineup gets worse"
    elif l_up:
        lineup_part = f"{loser} gets the better lineup"
    elif w_up and l_down:
        lineup_part = f"its lineup gets better too, while {loser}'s gets worse"
    elif w_up:
        lineup_part = "its lineup gets better too" if not value_even else f"{winner}'s lineup gets better"
    elif w_down and l_down:
        lineup_part = "both starting lineups get a little worse"
    elif l_down:
        lineup_part = f"{loser}'s lineup gets worse"
    elif w_down:
        lineup_part = f"{winner}'s lineup gets worse"
    else:
        lineup_part = "neither starting lineup changes much"
    return f"{value_part}, and {lineup_part}."


def _guidance(level, loser, lineup_only=None):
    """lineup_only: (team gaining more, other team) when player values could not be loaded."""
    if level is None:
        return "Not enough data to judge this trade. Check the Data check tab."
    if lineup_only:
        gainer, other = lineup_only
        return ("Player values could not be loaded, so this is judged on lineups only. " +
                ["Both teams come out about the same this season. Not grounds for a veto.",
                 f"{gainer} gains noticeably more this season than {other}, but that is not "
                 "grounds for a veto.",
                 f"{gainer} gains far more this season than {other}. Before voting, ask both "
                 "managers to explain the deal."][level])
    return ["Not grounds for a veto.",
            f"On paper, {loser} gives up more than it gets back. That is not grounds for a "
            "veto. Managers are allowed to make trades that others wouldn't.",
            "Before voting, ask both managers to explain the deal. A second look is not an "
            "automatic veto."][level]


def even_out_hint(winner, keep_winner, diff, power):
    """What the side getting more value could add to balance the trade, in plain words.
    diff is in star-premium units, so convert it back to a plain player value first."""
    need = 10000.0 * (diff / 10000.0) ** (1.0 / power)
    kind = ("a star" if need >= 6000 else "a solid starter" if need >= 3000
            else "a useful bench player" if need >= 1200 else "a minor piece")
    # Name up to two of the winner's own players whose value is close to what's needed
    close = sorted((p for p in keep_winner
                    if p.get("market") and abs(p["market"] - need) <= 0.25 * need),
                   key=lambda p: abs(p["market"] - need))[:2]
    eg = f", someone about as valuable as {' or '.join(p['name'] for p in close)}" if close else ""
    return f"To even it out: {winner} could add {kind}{eg}."


def _lineup_phrase(s, short=False):
    """short=True drops the words 'starting lineup' (for the second team in a sentence)."""
    d = s["lineup_delta"]
    if abs(d) < 0.5:
        move = "stays about the same"
    else:
        move = f"{'improves' if d > 0 else 'drops'} by {abs(d):.1f} points a week"
    odds = ""
    if abs(s["playoff_delta"]) >= 0.03:      # skip tiny moves like 91% to 92%
        odds = (f", and its chance of making the playoffs goes from {s['playoff_pre']:.0%} "
                f"to {s['playoff_post']:.0%}")
    return f"{s['team']}'s {'' if short else 'starting lineup '}{move}{odds}"


def _lineups_text(a, b):
    if abs(a["lineup_delta"]) < 0.5 and abs(b["lineup_delta"]) < 0.5:
        return "Neither starting lineup changes much."
    return f"{_lineup_phrase(a)}. {_lineup_phrase(b, short=True)}."


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

    # Lineup impact
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

    # Player value: what each side NETS = value received minus value of players it must cut
    sent = {team_a: side_score(out_a, S["power"]), team_b: side_score(out_b, S["power"])}
    net = {team_a: sent[team_b] - side_score(drops_a, S["power"]),
           team_b: sent[team_a] - side_score(drops_b, S["power"])}
    gap, level = _gap_level(net[team_a], net[team_b], S)
    value_level = level                      # before any softening
    value_winner = team_a if net[team_a] > net[team_b] else team_b
    value_loser = team_b if value_winner == team_a else team_a

    d_play = {t: probs_post[t] - ctx["probs"][t] for t in (team_a, team_b)}
    d_lineup = {t: lu_post[t] - lu_pre[t] for t in (team_a, team_b)}
    sides = {}
    for key, t, out, got, drops in (("a", team_a, out_a, out_b, drops_a),
                                    ("b", team_b, out_b, out_a, drops_b)):
        sides[key] = {"team": t, "sends": out, "receives": got, "drops": drops,
                      "lineup_pre": lu_pre[t], "lineup_post": lu_post[t],
                      "lineup_delta": d_lineup[t],
                      "playoff_pre": ctx["probs"][t], "playoff_post": probs_post[t],
                      "playoff_delta": d_play[t], "tier": tier(ctx["probs"][t]),
                      "net_value": net[t]}
    by_team = {sides["a"]["team"]: sides["a"], sides["b"]["team"]: sides["b"]}

    # Data-quality gate. A traded player we can't identify would silently count as worth
    # nothing and could flip the result, so that always stops the verdict.
    issues, uncounted = [], []
    for p in out_a + out_b:
        if p.get("match") == "unmatched":
            issues.append(f"{p['name']} could not be matched to the player database (check "
                          f"the spelling in rosters.json)")
        elif ctx["has_market"] and p.get("market") is None:
            if p["base_ppg"] > repl[p["pos"]] + 2.0:
                issues.append(f"{p['name']} has no player value in the feed")
            else:
                uncounted.append(p["name"])      # minor player: counted as zero, but say so
    res = {"gap": gap if ctx["has_market"] else None, "value_winner": value_winner,
           "value_loser": value_loser, "sides": sides, "issues": issues, "notes": []}

    if not ctx["has_stats"] and not ctx["has_market"]:
        issues.append("no live stats and no player values loaded")
    fills_need, lineup_only = False, None
    if issues:
        res.update(verdict="INCOMPLETE DATA", level=None)
    elif ctx["has_market"]:
        # "Fills a need": the side giving up more value still gets better this season
        fills_need = level > 0 and (d_lineup[value_loser] >= S["lineup_gain"]
                                    or d_play[value_loser] >= S["win_now_gain"])
        if fills_need:
            level -= 1
            res["notes"].append(
                f"{value_loser} gives up the more valuable side, but the trade fixes a weak "
                f"spot in its lineup (about {d_lineup[value_loser]:.1f} more points a week). "
                f"That is a normal, sensible trade.")
        res.update(verdict=LEVELS[level], level=level)
        if level > 0:
            keep_w = keep_a if value_winner == team_a else keep_b
            res["notes"].append(even_out_hint(value_winner, keep_w,
                                              abs(net[team_a] - net[team_b]), S["power"]))
        if not ctx["has_stats"]:
            res["notes"].append("Live stats unavailable: this result uses player value only "
                                "(lineup and playoff numbers are not meaningful).")
    else:
        diff = abs(d_play[team_a] - d_play[team_b]) * 100
        level = 0 if diff < 8 else (1 if diff < 20 else 2)
        res.update(verdict=LEVELS[level], level=level)
        gainer = team_a if d_play[team_a] >= d_play[team_b] else team_b
        lineup_only = (gainer, team_b if gainer == team_a else team_a)
        res["notes"].append("Player values unavailable: this result uses lineup impact only, "
                            "so treat it as a rough guide.")

    if uncounted and res["level"] is not None:
        res["notes"].append(f"{_join(uncounted)} "
                            f"{'has' if len(uncounted) == 1 else 'have'} no player value in "
                            f"the feed and {'is' if len(uncounted) == 1 else 'are'} counted "
                            f"as zero.")
    if ctx["value_source"] == "estimate" and res["level"] is not None:
        res["notes"].append("Player values here are FleeceGuard's own estimate (weekly points "
                            "above a replacement-level player), because FantasyCalc could not "
                            "be reached. Treat the result as a rough guide.")

    if not ctx["schedule"]:
        res["notes"].append("Regular season is over, so playoff odds don't move.")
    rookies = [p["name"] for p in out_a + out_b if p.get("rookie")]
    if rookies:
        res["notes"].append(
            f"Keeper note: {_join(rookies)} {'is a rookie' if len(rookies) == 1 else 'are rookies'}"
            f" and could be kept next season ({C.KEEPER_RULE}). That is not counted here.")

    # ---- plain-language summary: one row per question -------------------------------------
    summary = []
    if issues:
        pass                                  # incomplete data: don't show misleading rows
    elif res["gap"] is not None:
        w, l = by_team[value_winner], by_team[value_loser]
        w_gets, l_gets = w["receives"], l["receives"]
        w_names, l_names = _join(_label(p) for p in w_gets), _join(_label(p) for p in l_gets)
        diff_abs = abs(net[team_a] - net[team_b])
        value_even = value_level == 0 and (diff_abs < S["min_abs_gap"] or gap < 0.10)
        if value_even:
            value_text = (f"The two sides are worth about the same on the trade market. "
                          f"{value_winner} gets {w_names}. {value_loser} gets {l_names}.")
        else:
            size = "slightly more" if value_level == 0 else "more"
            if len(w_gets) == 1 and len(l_gets) == 1:
                value_text = (f"{w_names} is worth {size} on the trade market than {l_names}. "
                              f"The gap is about {gap:.0%}. {value_winner} gets "
                              f"{w_gets[0]['name']}.")
            else:
                value_text = (f"What {value_winner} gets is worth {size} on the trade market "
                              f"than what {value_loser} gets. The gap is about {gap:.0%}. "
                              f"{value_winner} gets {w_names}. {value_loser} gets {l_names}.")
        summary.append(("In short", _in_short(value_even, value_winner, value_loser,
                                               len(w_gets), d_lineup, ctx["has_stats"])))
        summary.append(("Trade value", value_text))
    if ctx["has_stats"] and not issues:
        summary.append(("Starting lineups", _lineups_text(sides["a"], sides["b"])))
    # Names ending in "Jr." would otherwise leave a doubled period at the end of a sentence
    res["summary"] = [(k, v.replace("..", ".")) for k, v in summary]

    res["headline"] = res["verdict"] + (": each side gets something"
                                        if fills_need and res["level"] == 0 else "")
    res["guidance"] = _guidance(res["level"], value_loser, lineup_only)
    details = [f"{s['team']} must drop {_join(p['name'] for p in s['drops'])} to make room."
               for s in (sides["a"], sides["b"]) if s["drops"]]
    res["details"] = [d.replace("..", ".") for d in
                      details + res["notes"] + [f"Data issue: {i}." for i in issues]]
    res["reasons"] = [f"{k}: {v}" for k, v in res["summary"]] + res["details"]
    return res


def asset_only(players_a_sends, players_b_sends, settings=None):
    """Player value alone (used by the backtest, where rosters at the time are unknown)."""
    S = settings or default_settings()
    a_nets = side_score(players_b_sends, S["power"])   # A receives what B sends
    b_nets = side_score(players_a_sends, S["power"])
    gap, level = _gap_level(a_nets, b_nets, S)
    return {"gap": gap, "level": level, "verdict": LEVELS[level], "a_is_ahead": a_nets > b_nets}


def whatsapp_text(res):
    a, b = res["sides"]["a"], res["sides"]["b"]
    names = lambda ps: ", ".join(p["name"] for p in ps) or "nothing"  # noqa: E731
    lines = [f"🛡️ FleeceGuard: {res['headline']}",
             res["guidance"],
             "",
             f"{a['team']} sends: {names(a['sends'])}",
             f"{b['team']} sends: {names(b['sends'])}",
             ""]
    lines += [f"{k}: {v}" for k, v in res["summary"]]
    lines += res["details"]
    return "\n".join(lines)
