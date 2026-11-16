"""Trade ideas: for one team, find 2-3 realistic trades that improve its lineup.

How it works:
  1. Measure each starting slot (QB, RB1, RB2, ...) against the league average to find
     the team's weak spots.
  2. Try every 1-for-1, 2-for-1, and 1-for-2 swap with every other team.
  3. Keep swaps that improve YOUR lineup, where player values are close (fair enough to
     offer) and the other team's lineup doesn't drop by more than 2 points a week. The other
     team doesn't have to come out ahead, but a trade that guts their lineup won't be accepted.
  4. Check the best few with the full trade checker (roster drops, playoff odds, verdict)
     and keep only NO CONCERNS results, one per trading partner.
"""
from itertools import combinations

import config as C
import engine as eng

SLOT_NAMES = {"QB": ["QB"], "RB": ["RB1", "RB2"], "WR": ["WR1", "WR2"], "TE": ["TE"]}
MIN_GAIN = 0.5          # your lineup must improve by at least this many points a week
MAX_PARTNER_LOSS = 2.0  # the other team's lineup may drop by at most this many points a week
MAX_CHECKS = 25         # how many top candidates get the full (slower) trade check


def lineup_slots(roster, repl):
    """Each starting slot's weekly points, using the same rules as engine.lineup_ppg.
    A slot filled by a free agent counts at replacement level."""
    slots, flex_pool = {}, []
    for pos in C.POSITIONS:
        n = C.ROSTER_SLOTS[pos]
        vals = sorted([p["ppg"] for p in roster if p["pos"] == pos], reverse=True)
        vals += [repl[pos]] * (n + C.ROSTER_SLOTS["FLEX"])
        for name, v in zip(SLOT_NAMES[pos], vals[:n]):
            slots[name] = v
        if pos in C.FLEX_POSITIONS:
            flex_pool += vals[n:]
    for i, v in enumerate(sorted(flex_pool, reverse=True)[:C.ROSTER_SLOTS["FLEX"]], start=1):
        slots[f"FLEX{i}"] = v
    return slots


def weak_spots(ctx, team, n=2):
    """The team's starting slots furthest below the league average, as (slot, mine, avg)."""
    all_slots = {t: lineup_slots(r, ctx["repl"]) for t, r in ctx["rosters"].items()}
    mine = all_slots[team]
    out = []
    for slot, v in mine.items():
        avg = sum(s[slot] for s in all_slots.values()) / len(all_slots)
        if avg - v >= 1.0:                   # ignore gaps too small to matter
            out.append((slot, v, avg))
    return sorted(out, key=lambda x: x[1] - x[2])[:n]


def _tradeable(p, has_market):
    """Players we can safely suggest: identified, and priced when values are loaded."""
    if p.get("match") == "unmatched":
        return False
    return p.get("market") is not None or not has_market


def _groups(players):
    """All single players and pairs from a roster."""
    singles = [(p,) for p in players]
    return singles, list(combinations(players, 2))


def find_trade_ideas(ctx, team, n=3):
    """Returns {"weak": [...], "ideas": [...], "note": str or None}."""
    if not ctx["has_stats"]:
        return {"weak": [], "ideas": [],
                "note": "Live stats aren't loaded, so trade ideas can't be calculated. "
                        "Check the Data check tab."}
    S, repl = ctx["settings"], ctx["repl"]
    mine = [p for p in ctx["rosters"][team] if _tradeable(p, ctx["has_market"])]
    my_singles, my_pairs = _groups(mine)
    base_me = ctx["lineups"][team]

    # ---- Step 2-3: fast screen of every swap (no roster drops or playoff odds yet) ----------
    candidates = []
    for other, roster in ctx["rosters"].items():
        if other == team:
            continue
        theirs = [p for p in roster if _tradeable(p, ctx["has_market"])]
        their_singles, their_pairs = _groups(theirs)
        base_them = ctx["lineups"][other]
        for give_set, get_set in ((my_singles, their_singles), (my_pairs, their_singles),
                                  (my_singles, their_pairs)):
            for give in give_set:
                give_val = eng.side_score(give, S["power"])
                for get in get_set:
                    # Cheapest check first: player values must be close
                    get_val = eng.side_score(get, S["power"])
                    if eng._gap_level(get_val, give_val, S)[1] != 0:
                        continue
                    give_ids = {id(p) for p in give}
                    get_ids = {id(p) for p in get}
                    new_me = eng.lineup_ppg([p for p in ctx["rosters"][team]
                                             if id(p) not in give_ids] + list(get), repl)
                    gain_me = new_me - base_me
                    if gain_me < MIN_GAIN:
                        continue
                    new_them = eng.lineup_ppg([p for p in roster if id(p) not in get_ids]
                                              + list(give), repl)
                    gain_them = new_them - base_them
                    if gain_them < -MAX_PARTNER_LOSS:
                        continue
                    # Rank mainly by your gain, with a small bonus when the other team also
                    # improves (more likely to be accepted) and a small cost for bigger deals
                    score = gain_me + 0.3 * gain_them - 0.3 * (len(give) + len(get) - 2)
                    candidates.append((score, other, give, get))

    candidates.sort(key=lambda c: -c[0])

    # ---- Step 4: full check on the best few, one idea per trading partner -----------------
    ideas, used_partners, checks = [], set(), 0
    for score, other, give, get in candidates:
        if len(ideas) >= n or checks >= MAX_CHECKS:
            break
        if other in used_partners:
            continue
        checks += 1
        give_names = [p["name"] for p in give]
        get_names = [p["name"] for p in get]
        res = eng.evaluate_trade(ctx, team, give_names, other, get_names)
        me, them = res["sides"]["a"], res["sides"]["b"]
        if (res["level"] != 0 or me["lineup_delta"] < MIN_GAIN
                or them["lineup_delta"] < -MAX_PARTNER_LOSS):
            continue                          # roster drops or the full check changed the picture
        used_partners.add(other)
        ideas.append({"partner": other, "give": give_names, "get": get_names,
                      "gain_me": me["lineup_delta"], "gain_them": them["lineup_delta"],
                      "result": res, "why": _why(team, other, give, get, me, them)})
    note = None
    if not ideas:
        note = ("No fair trades found that improve your lineup right now. That usually "
                "means your lineup has no obvious hole, or the players who would help you "
                "are worth more than what you could offer.")
    return {"weak": weak_spots(ctx, team), "ideas": ideas, "note": note}


def _why(team, other, give, get, me, them):
    """One plain sentence explaining why the trade fits both teams."""
    POS = {"QB": "quarterback", "RB": "running back", "WR": "receiver", "TE": "tight end"}
    get_pos = eng._join(sorted({POS[p["pos"]] for p in get}))
    give_pos = eng._join(sorted({POS[p["pos"]] for p in give}))
    if len(give) == 2 and len(get) == 1:
        shape = "You turn two of your players into one better starter. "
    elif len(give) == 1 and len(get) == 2:
        shape = "You get two useful players for one. "
    else:
        shape = ""
    if get_pos != give_pos:
        fit = f"You get {get_pos} help, and {other} gets {give_pos} help."
    else:
        fit = f"Both teams swap {give_pos}s that fit their lineups better."
    odds = ""
    if abs(me["playoff_delta"]) >= 0.03:
        odds = (f" Your chance of making the playoffs goes from {me['playoff_pre']:.0%} "
                f"to {me['playoff_post']:.0%}.")
    d = them["lineup_delta"]
    if d >= 0.5:
        partner = f"and {eng.poss(other)} improves by {d:.1f}."
    elif d <= -0.5:
        partner = (f"{eng.poss(other)} lineup drops by {-d:.1f} points a week, but they "
                   f"get fair value back.")
    else:
        partner = f"and {eng.poss(other)} stays about the same."
    joiner = ", " if d > -0.5 else ". "
    return (f"{shape}{fit} Your starting lineup improves by {me['lineup_delta']:.1f} points "
            f"a week{joiner}{partner}{odds}")


def slot_label(slot):
    """'RB2' -> 'second running back', for plain-language weak-spot text."""
    names = {"QB": "quarterback", "RB": "running back", "WR": "receiver", "TE": "tight end",
             "FLEX": "flex spot"}
    order = {"1": "top ", "2": "second "}
    pos = slot.rstrip("12")
    num = slot[len(pos):]
    if pos == "FLEX":
        return f"{'first' if num == '1' else 'second'} flex spot"
    return f"{order.get(num, '')}{names[pos]}"
