import json
import os
import streamlit as st

st.set_page_config(
    page_title="FleeceGuard | Fantasy Trade Arbitrator",
    page_icon="🛡️",
    layout="wide",
)

st.title("🛡️ FleeceGuard")
st.caption("Fantasy Trade Arbitrator & Lineup Optimizer")

# --- 1. LEAGUE ROSTER LAYOUT CONFIGURATION ---
# Configured for: 1 QB, 2 RB, 2 WR, 1 TE, 2 W/R/T FLEX
ROSTER_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2}

# --- 2. HARDCODED CUSTOM LEAGUE SCORING RULES ---
LEAGUE_SCORING_RULES = {
    "completions": 0.25,
    "incompletions": -0.25,
    "passing_yards_per_point": 25.0,
    "passing_td": 4.0,
    "interceptions": -1.0,
    "passing_bonuses": {275: 1.0, 375: 3.0, 450: 5.0},
    "passing_td_40plus_bonus": 2.0,
    "rushing_yards_per_point": 10.0,
    "rushing_td": 6.0,
    "rushing_bonuses": {75: 2.0, 100: 4.0, 150: 6.0},
    "rushing_td_40plus_bonus": 2.0,
    "receptions": 1.0,
    "receiving_yards_per_point": 10.0,
    "receiving_td": 6.0,
    "receiving_bonuses": {75: 2.0, 100: 4.0},
    "receiving_td_40plus_bonus": 2.0,
    "fumbles_lost": -2.0,
    "two_point_conversions": 2.0,
}


# --- 3. LOAD CENTRAL ROSTERS FROM JSON ---
@st.cache_data(ttl=60)
def load_central_rosters():
    if os.path.exists("rosters.json"):
        try:
            with open("rosters.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            st.error(f"Error reading rosters.json: {e}")
            return {}
    return {}


league_rosters = load_central_rosters()

if not league_rosters:
    st.error(
        "No roster data loaded. Please ensure `rosters.json` is committed to GitHub."
    )
    st.stop()

# --- 4. SIDEBAR LEAGUE SETTINGS ---
st.sidebar.header("⚙️ League Settings")

scoring_format = st.sidebar.selectbox(
    "Scoring Preset",
    ["Custom Bonus League (Default)", "Full PPR (1.0)", "Half PPR (0.5)"],
    index=0,
)

BASELINES_BY_FORMAT = {
    "Custom Bonus League (Default)": {
        "QB": 18.5,
        "RB": 9.5,
        "WR": 10.5,
        "TE": 7.0,
    },
    "Full PPR (1.0)": {"QB": 14.0, "RB": 8.0, "WR": 9.0, "TE": 6.5},
    "Half PPR (0.5)": {"QB": 14.0, "RB": 7.0, "WR": 7.5, "TE": 5.5},
}

WAIVER_BASELINES = BASELINES_BY_FORMAT[scoring_format]

st.sidebar.markdown("---")
st.sidebar.subheader("Active Starter Layout")
st.sidebar.write("• 1 QB")
st.sidebar.write("• 2 RB")
st.sidebar.write("• 2 WR")
st.sidebar.write("• 1 TE")
st.sidebar.write("• **2 FLEX (W/R/T)**")

st.sidebar.markdown("---")
st.sidebar.subheader("Waiver Baseline Floors")
st.sidebar.json(WAIVER_BASELINES)


# --- 5. OPTIMAL LINEUP SOLVER ---
def calculate_optimal_starting_ppg(roster_list):
    by_pos = {"QB": [], "RB": [], "WR": [], "TE": []}
    for p in roster_list:
        pos = p.get("pos", "")
        if pos in by_pos:
            by_pos[pos].append(p.get("ros_ppg", 0.0))

    for pos in by_pos:
        by_pos[pos].sort(reverse=True)

    total_ppg = 0.0
    flex_candidates = []

    for pos in ["QB", "RB", "WR", "TE"]:
        slots = ROSTER_SLOTS.get(pos, 0)
        starters = by_pos[pos][:slots]
        while len(starters) < slots:
            starters.append(WAIVER_BASELINES[pos])
        total_ppg += sum(starters)

        # Only RB, WR, TE bench players are eligible for W/R/T FLEX slots
        if pos != "QB":
            flex_candidates.extend(by_pos[pos][slots:])

    flex_slots = ROSTER_SLOTS.get("FLEX", 0)
    flex_candidates.sort(reverse=True)

    while len(flex_candidates) < flex_slots:
        flex_candidates.append(
            (WAIVER_BASELINES["RB"] + WAIVER_BASELINES["WR"]) / 2.0
        )

    total_ppg += sum(flex_candidates[:flex_slots])

    return round(total_ppg, 2)


def evaluate_trade(
    team_a_roster, team_b_roster, sending_from_a, sending_from_b
):
    pre_a = calculate_optimal_starting_ppg(team_a_roster)
    pre_b = calculate_optimal_starting_ppg(team_b_roster)

    post_a_roster = [
        p for p in team_a_roster if p["name"] not in sending_from_a
    ] + [p for p in team_b_roster if p["name"] in sending_from_b]
    post_b_roster = [
        p for p in team_b_roster if p["name"] not in sending_from_b
    ] + [p for p in team_a_roster if p["name"] in sending_from_a]

    post_a = calculate_optimal_starting_ppg(post_a_roster)
    post_b = calculate_optimal_starting_ppg(post_b_roster)

    return {
        "team_a": {
            "pre": pre_a,
            "post": post_a,
            "diff": round(post_a - pre_a, 2),
        },
        "team_b": {
            "pre": pre_b,
            "post": post_b,
            "diff": round(post_b - pre_b, 2),
        },
    }


# --- 6. MAIN TRADE UI ---
st.subheader("1. Select Trading Teams")
col1, col2 = st.columns(2)

teams_list = list(league_rosters.keys())

with col1:
    team_a_name = st.selectbox("Team A (Initiating)", teams_list, index=0)
with col2:
    available_team_b = [t for t in teams_list if t != team_a_name]
    team_b_name = st.selectbox(
        "Team B (Target)", available_team_b, index=0 if available_team_b else 0
    )

team_a_roster = league_rosters[team_a_name]
team_b_roster = league_rosters[team_b_name]

st.subheader("2. Select Traded Players")
t_col1, t_col2 = st.columns(2)

with t_col1:
    giving_a = st.multiselect(
        f"Players sent from {team_a_name}:", [p["name"] for p in team_a_roster]
    )
with t_col2:
    giving_b = st.multiselect(
        f"Players sent from {team_b_name}:", [p["name"] for p in team_b_roster]
    )

if st.button("🛡️ Run FleeceGuard Analysis", type="primary"):
    if not giving_a or not giving_b:
        st.warning(
            "Please select at least one player on each side of the trade."
        )
    else:
        results = evaluate_trade(
            team_a_roster, team_b_roster, giving_a, giving_b
        )

        st.markdown("---")
        st.subheader(
            f"📊 FleeceGuard Verdict & Lineup Impact ({scoring_format})"
        )

        m1, m2 = st.columns(2)
        diff_a = results["team_a"]["diff"]
        diff_b = results["team_b"]["diff"]

        with m1:
            st.metric(
                label=f"{team_a_name} Starting Lineup PPG",
                value=f"{results['team_a']['post']} PPG",
                delta=f"{diff_a:+} PPG/week",
            )
        with m2:
            st.metric(
                label=f"{team_b_name} Starting Lineup PPG",
                value=f"{results['team_b']['post']} PPG",
                delta=f"{diff_b:+} PPG/week",
            )

        st.markdown("---")
        st.subheader("⚖️ League Veto / Collusion Guidance")
        if diff_a >= -1.0 and diff_b >= -1.0:
            st.success(
                "✅ **FAIR TRADE:** Both teams gain or suffer negligible starting point impact. No veto recommended."
            )
        elif abs(diff_a - diff_b) > 7.0:
            st.error(
                "🚨 **FLEECE ALERT:** Highly unbalanced starting lineup impact. Review closely before approving!"
            )
        else:
            st.info(
                "⚖️ **ACCEPTABLE MARGIN:** Minor scoring shift, likely balancing roster depth or positional needs."
            )