"""Shotcast app: who creates the best chances? Reads the dbt-built DuckDB warehouse (read-only)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE = Path(os.environ.get("SHOTCAST_WAREHOUSE", ROOT / "warehouse" / "shotcast.duckdb"))
HEALTH = Path(os.environ.get("SHOTCAST_HEALTH", ROOT / "health"))

st.set_page_config(page_title="Shotcast", page_icon="⚽", layout="wide")


@st.cache_data(show_spinner=False)
def query(sql: str, params: tuple = (), _mtime: float = 0.0) -> pd.DataFrame:
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    try:
        return con.execute(sql, list(params)).fetchdf()
    finally:
        con.close()


def q(sql: str, params: tuple = ()) -> pd.DataFrame:
    return query(sql, params, _mtime=WAREHOUSE.stat().st_mtime)


def pitch_figure() -> go.Figure:
    """A 120 x 80 pitch (StatsBomb coordinates), attacking left to right."""
    line = dict(color="#9aa5b1", width=1.5)
    fig = go.Figure()
    shapes = [
        dict(type="rect", x0=0, y0=0, x1=120, y1=80, line=line),
        dict(type="line", x0=60, y0=0, x1=60, y1=80, line=line),
        dict(type="rect", x0=0, y0=18, x1=18, y1=62, line=line),
        dict(type="rect", x0=102, y0=18, x1=120, y1=62, line=line),
        dict(type="rect", x0=0, y0=30, x1=6, y1=50, line=line),
        dict(type="rect", x0=114, y0=30, x1=120, y1=50, line=line),
        dict(type="circle", x0=50, y0=30, x1=70, y1=50, line=line),
    ]
    fig.update_layout(
        shapes=shapes, height=460, margin=dict(l=0, r=0, t=10, b=0),
        xaxis=dict(range=[-2, 122], visible=False, fixedrange=True),
        yaxis=dict(range=[82, -2], visible=False, scaleanchor="x", fixedrange=True),
        plot_bgcolor="#f6f8fa", showlegend=False,
    )
    return fig


def chance_map(df: pd.DataFrame) -> go.Figure:
    fig = pitch_figure()
    for _, r in df.iterrows():
        color = "#d1242f" if r["is_goal"] else "#1f6feb"
        fig.add_trace(go.Scatter(
            x=[r.pass_x, r.pass_end_x], y=[r.pass_y, r.pass_end_y], mode="lines+markers",
            line=dict(color=color, width=1 + 6 * float(r.xg)),
            marker=dict(size=[3, 7], color=color),
            hovertemplate=(f"{r.minute}' to {r.shooter}<br>xG {r.xg:.2f}"
                           f"{' (goal)' if r.is_goal else ''}<extra></extra>"),
        ))
    return fig


def page_creators() -> None:
    st.header("Who creates the most dangerous chances?")
    st.caption("xG assisted = the total expected-goals value of the shots a player's passes set up. "
               "Per-90 numbers compare players with different minutes.")
    comps = q("select distinct competition_label from mart_chance_creators order by 1")[
        "competition_label"
    ].tolist()
    c1, c2, c3 = st.columns([2, 2, 2])
    default = [c for c in comps if c.endswith("World Cup 2022")] or comps[:1]
    sel = c1.multiselect("Competition", comps, default=default)
    min_minutes = c2.slider("Minimum minutes played", 0, 900, 300, step=30)
    metric = c3.selectbox("Rank by", ["xg_assisted_per90", "xg_assisted", "key_passes",
                                      "big_chances_created", "progressive_passes_per90"])
    if not sel:
        st.info("Pick at least one competition.")
        return
    marks = ",".join("?" * len(sel))
    df = q(f"""select player_id, player_name, mode(team) as team, sum(matches) as matches,
                      sum(minutes) as minutes, sum(key_passes) as key_passes,
                      sum(xg_assisted) as xg_assisted, sum(big_chances_created) as big_chances_created,
                      sum(goal_assists) as goal_assists, sum(progressive_passes) as progressive_passes
               from mart_chance_creators where competition_label in ({marks})
               group by player_id, player_name""", tuple(sel))
    df = df[df["minutes"] >= min_minutes].copy()
    df["xg_assisted_per90"] = (df["xg_assisted"] * 90 / df["minutes"]).round(3)
    df["progressive_passes_per90"] = (df["progressive_passes"] * 90 / df["minutes"]).round(2)
    df = df.sort_values(metric, ascending=False)
    if df.empty:
        st.warning("No players meet the minutes filter.")
        return
    top = df.head(15).iloc[::-1]
    fig = go.Figure(go.Bar(x=top[metric], y=top["player_name"] + " (" + top["team"] + ")",
                           orientation="h", marker_color="#1f6feb"))
    fig.update_layout(height=480, margin=dict(l=0, r=10, t=10, b=0), xaxis_title=metric)
    left, right = st.columns([3, 3])
    left.plotly_chart(fig, use_container_width=True)
    right.dataframe(df[["player_name", "team", "minutes", "key_passes", "xg_assisted",
                        "xg_assisted_per90", "goal_assists"]].head(25),
                 hide_index=True, use_container_width=True)

    st.subheader("Where does a player create from?")
    player = st.selectbox("Player", df["player_name"].head(50).tolist())
    pid = int(df.loc[df["player_name"] == player, "player_id"].iloc[0])
    ch = q(f"select * from fct_chances where creator_id = ? and competition_label in ({marks})",
           (pid, *sel))
    st.caption("Each line is a pass that led to a shot. Thicker = higher xG. Red = goal, blue = no goal.")
    st.plotly_chart(chance_map(ch), use_container_width=True)


def page_teams() -> None:
    st.header("Teams: did results match the chances?")
    comps = q("select distinct competition_label from mart_team_performance order by 1")[
        "competition_label"
    ].tolist()
    start = comps.index("FIFA World Cup 2022") if "FIFA World Cup 2022" in comps else 0
    comp = st.selectbox("Competition", comps, index=start)
    df = q("select * from mart_team_performance where competition_label = ? order by xg_diff desc", (comp,))
    fig = go.Figure(go.Scatter(x=df["xg_for"], y=df["goals_for"], mode="markers+text", text=df["team"],
                               textposition="top center", marker=dict(size=10, color="#1f6feb")))
    top = float(max(df["xg_for"].max(), df["goals_for"].max())) + 1
    fig.add_trace(go.Scatter(x=[0, top], y=[0, top], mode="lines", line=dict(dash="dot", color="#9aa5b1")))
    fig.update_layout(height=520, showlegend=False, xaxis_title="xG created", yaxis_title="Goals scored",
                      margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Above the dotted line = scored more than the chances suggested "
               "(clinical or lucky). Below = the reverse.")
    st.dataframe(df[["team", "matches", "goals_for", "goals_against", "xg_for", "xg_against",
                     "xg_diff", "goals_minus_xg"]], hide_index=True, use_container_width=True)


def page_health() -> None:
    st.header("Data health")
    st.caption("What a data engineer checks first: did the pipeline run, and do the tests pass?")
    summary = HEALTH / "dbt_summary.json"
    if summary.exists():
        s = json.loads(summary.read_text())
        a, b, c = st.columns(3)
        a.metric("dbt tests passed", f"{s['tests_passed']} / {s['tests_total']}")
        b.metric("Models built", s["models_ok"])
        c.metric("Last dbt run", str(s.get("generated_at", "?"))[:19].replace("T", " "))
        if s["tests_failed"]:
            st.error("Failing tests: " + ", ".join(s["tests_failed"]))
        if s["tests_warned"]:
            st.warning("Warnings: " + ", ".join(s["tests_warned"]))
    else:
        st.info("No dbt summary yet. Run `make health`.")
    runs = HEALTH / "runs.jsonl"
    if runs.exists():
        st.subheader("Recent pipeline steps")
        rows = [json.loads(x) for x in runs.read_text().splitlines()[-20:]]
        st.dataframe(pd.DataFrame(rows).iloc[::-1].fillna("-"), hide_index=True, use_container_width=True)
    st.subheader("Warehouse size")
    counts = q("""select 'matches' as table_name, count(*) as rows from dim_match
                  union all select 'events', count(*) from stg_events
                  union all select 'shots', count(*) from fct_shots
                  union all select 'chances (shots set up by a pass)', count(*) from fct_chances
                  union all select 'player-match rows', count(*) from fct_player_match""")
    st.dataframe(counts, hide_index=True, use_container_width=True)


def main() -> None:
    st.sidebar.title("⚽ Shotcast")
    if not WAREHOUSE.exists():
        st.error(f"Warehouse not found at {WAREHOUSE}. Run `make all` first.")
        st.stop()
    page = st.sidebar.radio("View", ["Chance creators", "Teams", "Data health"])
    {"Chance creators": page_creators, "Teams": page_teams, "Data health": page_health}[page]()
    st.sidebar.caption("Data: StatsBomb open data. Built with Python, dbt, DuckDB, Airflow.")


main()
