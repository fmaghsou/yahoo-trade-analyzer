"""Look and feel: purple header, blue links, pill buttons, underlined tabs.
Styled after Yahoo Fantasy's layout and colors. No Yahoo logo or name is used."""

PURPLE, BLUE = "#6001D2", "#0F69FF"

# Translucent tints are used for panels so the page still reads well if a viewer
# switches Streamlit to dark mode.
CSS = f"""
<style>
html, body, [class*="css"], .stMarkdown, .stButton button {{
    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
}}
.block-container {{ padding-top: 2.2rem; max-width: 1200px; }}

/* Header band */
.fg-header {{
    background: {PURPLE}; color: #fff; border-radius: 12px;
    padding: 18px 22px; margin-bottom: 14px;
    display: flex; align-items: center; gap: 14px;
}}
.fg-header > div:last-child {{ min-width: 0; }}
.fg-badge {{
    width: 46px; height: 46px; border-radius: 50%; background: rgba(255,255,255,.16);
    display: flex; align-items: center; justify-content: center; font-size: 24px; flex: none;
}}
.fg-title {{ font-size: 1.55rem; font-weight: 800; line-height: 1.15; letter-spacing: -.01em; }}
.fg-sub {{ font-size: .9rem; opacity: .88; margin-top: 2px; }}

/* Links */
a, a:visited {{ color: {BLUE}; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

/* Tabs: bold labels, blue underline on the active one */
.stTabs [data-baseweb="tab-list"] {{ gap: 6px; border-bottom: 1px solid rgba(128,128,128,.25); }}
.stTabs [data-baseweb="tab"] {{ font-weight: 600; padding: 10px 14px; }}
.stTabs [aria-selected="true"] {{ color: inherit; font-weight: 800; }}
.stTabs [data-baseweb="tab-highlight"] {{ background-color: {BLUE}; height: 3px; }}

/* Buttons: rounded pills */
.stButton > button {{ border-radius: 999px; font-weight: 700; padding: .45rem 1.3rem; }}

/* Metric tiles and result boxes */
[data-testid="stMetric"] {{
    background: rgba(96,1,210,.05); border: 1px solid rgba(128,128,128,.22);
    border-radius: 10px; padding: 12px 14px;
}}
[data-testid="stMetricLabel"] {{ font-weight: 600; }}
[data-testid="stAlert"] {{ border-radius: 10px; }}
[data-testid="stExpander"] {{ border-radius: 10px; }}

/* No side panel in this app: hide it and its toggle arrow */
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"] {{ display: none; }}

/* Phones: tighter header */
@media (max-width: 640px) {{
    .fg-header {{ padding: 14px 16px; }}
    .fg-title {{ font-size: 1.25rem; }}
    .block-container {{ padding-left: 1rem; padding-right: 1rem; }}
}}
</style>
"""


def header_html(league_name, season):
    return (f'<div class="fg-header"><div class="fg-badge">🛡️</div><div>'
            f'<div class="fg-title">FleeceGuard</div>'
            f'<div class="fg-sub">{league_name} · {season} Season · Trade checker</div>'
            f'</div></div>')
