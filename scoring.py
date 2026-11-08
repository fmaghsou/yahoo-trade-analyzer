"""Turns one week of raw stats into fantasy points using THIS league's scoring rules."""
import config as C


def _bonus(value, tiers, stack):
    """Yahoo-style yardage bonus. stack=False pays only the highest tier reached."""
    hit = [pts for thr, pts in sorted(tiers.items()) if value >= thr]
    if not hit:
        return 0.0
    return float(sum(hit) if stack else hit[-1])


def _n40(s, exact_key, td_key, share):
    """Count of 40+ yard TDs: exact if the feed has it, else an estimate from total TDs."""
    exact = s.get(exact_key)
    return exact if exact is not None else s.get(td_key, 0.0) * share


def score_week(s):
    """s uses canonical keys: pass_att, pass_cmp, pass_yd, pass_td, pass_int, rush_yd, rush_td,
    rec, rec_yd, rec_td, fum_lost, two_pt, ret_td (+ optional pass/rush/rec_td_40p)."""
    r = C.SCORING
    att, cmp_ = s.get("pass_att", 0.0), s.get("pass_cmp", 0.0)
    py, ry, cy = s.get("pass_yd", 0.0), s.get("rush_yd", 0.0), s.get("rec_yd", 0.0)

    pts = r["pass_cmp"] * cmp_ + r["pass_inc"] * max(att - cmp_, 0.0)
    pts += py / r["pass_yd_per_pt"] + _bonus(py, r["pass_bonus"], C.BONUSES_STACK)
    pts += r["pass_td"] * s.get("pass_td", 0.0) + r["pass_int"] * s.get("pass_int", 0.0)

    pts += ry / r["rush_yd_per_pt"] + _bonus(ry, r["rush_bonus"], C.BONUSES_STACK)
    pts += r["rush_td"] * s.get("rush_td", 0.0)

    pts += r["rec"] * s.get("rec", 0.0) + cy / r["rec_yd_per_pt"]
    pts += _bonus(cy, r["rec_bonus"], C.BONUSES_STACK) + r["rec_td"] * s.get("rec_td", 0.0)

    pts += r["ret_td"] * s.get("ret_td", 0.0) + r["fum_lost"] * s.get("fum_lost", 0.0)
    pts += r["two_pt"] * s.get("two_pt", 0.0)

    n40 = (_n40(s, "pass_td_40p", "pass_td", C.TD40_SHARE["pass"])
           + _n40(s, "rush_td_40p", "rush_td", C.TD40_SHARE["rush"])
           + _n40(s, "rec_td_40p", "rec_td", C.TD40_SHARE["rec"]))
    return pts + r["td40_bonus"] * n40
