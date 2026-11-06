import json
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="FleeceGuard | Fantasy Trade Analyzer",
    page_icon="🛡️",
    layout="wide",
)

st.title("🛡️ FleeceGuard")
st.caption("Fantasy Trade Arbitrator & Lineup Optimizer")

# --- 1. LEAGUE CONFIGURATION ---
ROSTER_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}
WAIVER_BASELINES = {"QB": 14.0, "RB": 7.5, "WR": 9.0, "TE": 6.5}

# Pre-loaded League Rosters (Initial template)
DEFAULT_LEAGUE = {
    "Team Alpha": [
        {"name": "Patrick Mahomes", "pos": "QB", "ros_ppg": 19.5},
        {"name": "Christian McCaffrey", "pos": "RB", "ros_ppg": 18.0},
        {"name": "Derrick Henry", "pos": "RB", "ros_ppg": 15.2},
        {"name": "CeeDee Lamb", "pos": "WR", "ros_ppg": 16.8},
        {"name": "AJ Brown", "pos": "WR", "ros_ppg": 14.5},
        {"name": "Travis Kelce", "pos": "TE", "ros_ppg": 12.0},
        {"name": "Deebo Samuel", "pos": "WR", "ros_ppg": 11.8},
        {"name": "Najee Harris", "pos": "RB", "ros_ppg": 10.2},
    ],
    "Team Bravo": [
        {"name": "Josh Allen", "pos": "QB", "ros_ppg": 21.0},
        {"name": "Breece Hall", "pos": "RB", "ros_ppg": 14.8},
        {"name": "Kenneth Walker III", "pos": "RB", "ros_ppg": 13.5},
        {"name": "Tyreek Hill", "pos": "WR", "ros_ppg": 16.0},
        {"name": "Amon-Ra St. Brown", "pos": "WR", "ros_ppg": 15.5},
        {"name": "Trey McBride", "pos": "TE", "ros_ppg": 11.5},
        {"name": "Amari Cooper", "pos": "WR", "ros_ppg": 10.5},
    ],
    "Team Charlie": [
        {"name": "Lamar Jackson", "pos": "QB", "ros_ppg": 20.5},
        {"name": "Saquon Barkley", "pos": "RB", "ros_ppg": 16.2},
        {"name": "Bijan Robinson", "pos": "RB", "ros_ppg": 15.8},
        {"name": "Justin Jefferson", "pos": "WR", "ros_ppg": 17.2},
        {"name": "Nico Collins", "pos": "WR", "ros_ppg": 14.0},
        {"name": "George Kittle", "pos": "TE", "ros_ppg": 11.0},
    ],
}

# Preserve edited rosters across user sessions
if "league_rosters" not in st.session_state:
    st.session_state.league_rosters = DEFAULT_LEAGUE

# --- 2. SIDEBAR ROSTER IMPORTER & EDITOR ---
with st.sidebar:
    st.header("⚙️ Roster Manager")
    st.write("View, edit, or import team rosters for your league.")

    active_team = st.selectbox(
        "Select Team to Edit", list(st.session_state.league_rosters.keys())
    )

    with st.expander(f"📥 Paste / Update Roster for {active_team}"):
        st.caption(
            "Format per line: `Player Name, Position, PPG`\n\nExample:\n`Jalen Hurts, QB, 19.2`\n`Bucky Irving, RB, 12.1`"
        )
        pasted_text = st.text_area("Paste Players:", height=150)
        if st.button("Apply Pasted Roster"):
            new_players = []
            lines = pasted_text.strip().split("\n")
            for line in lines:
                parts = [p.strip() for p in line.split(",") if p.strip()]
                if len(parts) >= 3:
                    try:
                        new_players.append(
                            {
                                "name": parts[0],
                                "pos": parts[1].upper(),
                                "ros_ppg": float(parts[2]),
                            }
                        )
                    except ValueError:
                        pass
            if new_players:
                st.session_state.league_rosters[active_team] = new_players
                st.success(
                    f"Updated {active_team} with {len(new_players)} players!"
                )
                st.rerun()

    # Display active roster table in sidebar
    current_df = pd.DataFrame(st.session_state.league_rosters[active_team])
    st.subheader(f"Current {active_team} Roster")
    st.dataframe(current_df, use_container_width=True, hide_index=True)

    if st.button("🔄 Reset All Rosters to Default"):
        st.session_state.league_rosters = DEFAULT_LEAGUE
        st.rerun()

# --- 3. TRADE EVALUATION LOGIC ---
def calculate_optimal_starting_ppg(roster_list):
    by_pos = {"QB": [], "RB": [], "WR": [], "TE": []}
    for p in roster_list:
        pos = p["pos"]
        if pos in by_pos:
            by_pos[pos].append(p["ros_ppg"])

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
        flex_candidates.extend(by_pos[pos][slots:])

    flex_slots = ROSTER_SLOTS.get("FLEX", 0)
    flex_candidates.sort(reverse=True)
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


# --- 4. MAIN APP UI ---
st.subheader("1. Select Trading Teams")
col1, col2 = st.columns(2)

teams_list = list(st.session_state.league_rosters.keys())

with col1:
    team_a_name = st.selectbox("Team A (Initiating)", teams_list, index=0)
with col2:
    available_team_b = [t for t in teams_list if t != team_a_name]
    team_b_name = st.selectbox("Team B (Target)", available_team_b, index=0)

team_a_roster = st.session_state.league_rosters[team_a_name]
team_b_roster = st.session_state.league_rosters[team_b_name]

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
        st.subheader("📊 FleeceGuard Verdict & Lineup Impact")

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