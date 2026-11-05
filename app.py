import streamlit as st
import pandas as pd

st.set_page_config(page_title="FleeceGuard | Fantasy Trade Analyzer", page_icon="🛡️", layout="centered")

# --- HEADER & BRANDING ---
st.title("🛡️ FleeceGuard")
st.caption("The Roster-Aware Trade Arbitrator & Lineup Optimizer")
st.write("Analyze starting lineup point changes, roster depth, and VORP before accepting or voting on trades!")

# --- 1. LEAGUE CONFIGURATION ---
ROSTER_SLOTS = {'QB': 1, 'RB': 2, 'WR': 2, 'TE': 1, 'FLEX': 1}
WAIVER_BASELINES = {'QB': 13.5, 'RB': 7.0, 'WR': 7.5, 'TE': 5.5}

# --- 2. PRE-LOADED ROSTER & PROJECTION DATA ---
@st.cache_data
def load_league_data():
    return {
        "Team Alpha": [
            {"name": "Josh Allen", "pos": "QB", "ros_ppg": 21.5},
            {"name": "Breece Hall", "pos": "RB", "ros_ppg": 14.5},
            {"name": "Chuba Hubbard", "pos": "RB", "ros_ppg": 9.5},
            {"name": "Justin Jefferson", "pos": "WR", "ros_ppg": 17.0},
            {"name": "Puka Nacua", "pos": "WR", "ros_ppg": 15.2},
            {"name": "Tee Higgins", "pos": "WR", "ros_ppg": 12.8},
            {"name": "Trey McBride", "pos": "TE", "ros_ppg": 11.2},
        ],
        "Team Bravo": [
            {"name": "Patrick Mahomes", "pos": "QB", "ros_ppg": 18.2},
            {"name": "Saquon Barkley", "pos": "RB", "ros_ppg": 16.0},
            {"name": "Zack Moss", "pos": "RB", "ros_ppg": 8.0},
            {"name": "Amon-Ra St. Brown", "pos": "WR", "ros_ppg": 16.5},
            {"name": "Devonta Smith", "pos": "WR", "ros_ppg": 13.0},
            {"name": "George Kittle", "pos": "TE", "ros_ppg": 12.0},
            {"name": "Calvin Ridley", "pos": "WR", "ros_ppg": 10.5},
        ]
    }

league_teams = load_league_data()

# --- 3. LINEUP OPTIMIZATION & TRADE LOGIC ---
def calculate_optimal_starting_ppg(roster_list):
    by_pos = {'QB': [], 'RB': [], 'WR': [], 'TE': []}
    for p in roster_list:
        by_pos[p['pos']].append(p['ros_ppg'])

    for pos in by_pos:
        by_pos[pos].sort(reverse=True)

    total_ppg = 0.0
    flex_candidates = []

    for pos in ['QB', 'RB', 'WR', 'TE']:
        slots = ROSTER_SLOTS.get(pos, 0)
        starters = by_pos[pos][:slots]
        while len(starters) < slots:
            starters.append(WAIVER_BASELINES[pos])
        total_ppg += sum(starters)
        flex_candidates.extend(by_pos[pos][slots:])

    flex_slots = ROSTER_SLOTS.get('FLEX', 0)
    flex_candidates.sort(reverse=True)
    total_ppg += sum(flex_candidates[:flex_slots])

    return round(total_ppg, 2)

def evaluate_trade(team_a_roster, team_b_roster, sending_from_a, sending_from_b):
    pre_a = calculate_optimal_starting_ppg(team_a_roster)
    pre_b = calculate_optimal_starting_ppg(team_b_roster)

    post_a_roster = [p for p in team_a_roster if p['name'] not in sending_from_a] + \
                    [p for p in team_b_roster if p['name'] in sending_from_b]
    post_b_roster = [p for p in team_b_roster if p['name'] not in sending_from_b] + \
                    [p for p in team_a_roster if p['name'] in sending_from_a]

    post_a = calculate_optimal_starting_ppg(post_a_roster)
    post_b = calculate_optimal_starting_ppg(post_b_roster)

    return {
        "team_a": {"pre": pre_a, "post": post_a, "diff": round(post_a - pre_a, 2)},
        "team_b": {"pre": pre_b, "post": post_b, "diff": round(post_b - pre_b, 2)}
    }

# --- 4. STREAMLIT UI ---
st.subheader("1. Select Trading Teams")
col1, col2 = st.columns(2)

with col1:
    team_a_name = st.selectbox("Team A Initiating Trade", list(league_teams.keys()), index=0)
with col2:
    team_b_name = st.selectbox("Team B Target", [t for t in league_teams.keys() if t != team_a_name], index=0)

team_a_roster = league_teams[team_a_name]
team_b_roster = league_teams[team_b_name]

st.subheader("2. Select Players Involved")
t_col1, t_col2 = st.columns(2)

with t_col1:
    giving_a = st.multiselect(f"Players sent from {team_a_name}:", [p['name'] for p in team_a_roster])
with t_col2:
    giving_b = st.multiselect(f"Players sent from {team_b_name}:", [p['name'] for p in team_b_roster])

if st.button("🛡️ Run FleeceGuard Analysis", type="primary"):
    if not giving_a or not giving_b:
        st.warning("Please select at least one player on each side of the trade.")
    else:
        results = evaluate_trade(team_a_roster, team_b_roster, giving_a, giving_b)
        
        st.markdown("---")
        st.subheader("📊 FleeceGuard Verdict & Lineup Impact")

        m1, m2 = st.columns(2)
        diff_a = results['team_a']['diff']
        diff_b = results['team_b']['diff']

        with m1:
            st.metric(
                label=f"{team_a_name} Starting Lineup PPG",
                value=f"{results['team_a']['post']} PPG",
                delta=f"{diff_a:+} PPG/week"
            )
        with m2:
            st.metric(
                label=f"{team_b_name} Starting Lineup PPG",
                value=f"{results['team_b']['post']} PPG",
                delta=f"{diff_b:+} PPG/week"
            )

        st.markdown("---")
        st.subheader("⚖️ League Veto / Collusion Guidance")
        if diff_a >= -1.0 and diff_b >= -1.0:
            st.success("✅ **FAIR TRADE:** Both teams gain or suffer negligible starting point impact. No veto recommended.")
        elif abs(diff_a - diff_b) > 7.0:
            st.error("🚨 **FLEECE ALERT:** Highly unbalanced starting lineup impact. Review closely before approving!")
        else:
            st.info("⚖️ **ACCEPTABLE MARGIN:** Minor scoring shift, likely balancing roster depth or positional needs.")