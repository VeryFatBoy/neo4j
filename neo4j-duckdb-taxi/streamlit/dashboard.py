"""
NYC Taxi Dashboard
------------------

Streamlit dashboard for the neo4j-duckdb-taxi demo.
All trip data comes from Neo4j via Cypher; zone centroids are read from data/raw/zone_centroids.json (produced by Notebook 1).

Run with:

    streamlit run dashboard.py
"""

import folium
import json
import os
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from neo4j import GraphDatabase

# Page config
st.set_page_config(
    page_title="NYC Taxi Dashboard",
    page_icon="🚕",
    layout="wide",
)

# Validated palettes
# Cab type (2-color, all checks pass)
CAB_COLORS = {
    "Yellow": "#D97706",
    "Green":  "#0891B2",
}

# Borough (6-color, all checks pass)
# Manhattan, Brooklyn, Queens, Bronx, Staten Island, EWR
BOROUGH_COLORS = {
    "Manhattan":     "#2563EB",
    "Brooklyn":      "#DB2777",
    "Queens":        "#0891B2",
    "Bronx":         "#D97706",
    "Staten Island": "#7C3AED",
    "EWR":           "#65A30D",
}
BOROUGH_COLOR_DEFAULT = "#94A3B8"

# Paths
_HERE = os.path.dirname(os.path.abspath(__file__))
CENTROIDS_PATH = os.path.join(_HERE, "..", "data", "raw", "zone_centroids.json")

# Configuration
NEO4J_URI      = os.environ["NEO4J_URI"]
NEO4J_USERNAME = os.environ["NEO4J_USERNAME"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]
NEO4J_DATABASE = os.environ["NEO4J_DATABASE"]
CARTO_API_KEY  = os.environ["CARTO_API_KEY"]

MAP_TILES = (
    f"https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}.png?key={CARTO_API_KEY}"
    if CARTO_API_KEY
    else "CartoDB positron"
)
MAP_ATTR = (
    "&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors "
    "&copy; <a href='https://carto.com/attributions'>CARTO</a>"
)

# Neo4j connection
@st.cache_resource
def get_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

def run_query(cypher, params=None):
    with get_driver().session(database=NEO4J_DATABASE) as session:
        return session.run(cypher, **(params or {})).data()

# Data queries
@st.cache_data(ttl=300)
def load_zone_trip_counts(rel_types: tuple) -> pd.DataFrame:
    """Total trip count per pickup zone, summed across selected relationship types."""
    rows = []
    for rt in rel_types:
        result = run_query(f"""
            MATCH (pu:Zone)-[t:{rt}]->()
            RETURN pu.locationId AS locationId,
                   pu.zone       AS zone,
                   pu.borough    AS borough,
                   sum(t.count)  AS tripCount
            ORDER BY tripCount DESC
        """)
        rows.extend(result)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.groupby(["locationId", "zone", "borough"], as_index=False)["tripCount"].sum()
    df = df.sort_values("tripCount", ascending=False).reset_index(drop=True)
    return df

@st.cache_data(ttl=300)
def load_top_routes(rel_type: str, n: int) -> pd.DataFrame:
    result = run_query(f"""
        MATCH (pu:Zone)-[t:{rel_type}]->(do:Zone)
        RETURN pu.zone AS puZone,
               do.zone AS doZone,
               t.count AS tripCount,
               t.avgFare AS avgFare,
               t.avgDistance AS avgDistance,
               t.avgDurationMinutes AS avgDurationMinutes
        ORDER BY t.count DESC
        LIMIT $n
    """, {"n": n})
    return pd.DataFrame(result)

@st.cache_data(ttl=300)
def load_fare_distance(rel_type: str) -> pd.DataFrame:
    result = run_query(f"""
        MATCH (pu:Zone)-[t:{rel_type}]->(do:Zone)
        WHERE t.avgFare IS NOT NULL AND t.avgDistance IS NOT NULL
          AND t.avgFare > 0 AND t.avgDistance > 0
        RETURN pu.zone    AS puZone,
               pu.borough AS borough,
               t.avgFare  AS avgFare,
               t.avgDistance AS avgDistance,
               t.count    AS tripCount
    """)
    return pd.DataFrame(result)

@st.cache_data(ttl=300)
def load_yellow_green_comparison(n: int) -> pd.DataFrame:
    result = run_query("""
        MATCH (pu:Zone)-[y:YELLOW_TRIP]->(do:Zone)
        MATCH (pu)-[g:GREEN_TRIP]->(do)
        RETURN pu.zone     AS puZone,
               do.zone     AS doZone,
               y.count     AS yellowTrips,
               y.avgFare   AS yellowAvgFare,
               g.count     AS greenTrips,
               g.avgFare   AS greenAvgFare
        ORDER BY y.count DESC
        LIMIT $n
    """, {"n": n})
    return pd.DataFrame(result)

@st.cache_data(ttl=300)
def load_graph_summary() -> dict:
    row = run_query("""
        MATCH (z:Zone)               WITH count(z) AS zones
        MATCH ()-[y:YELLOW_TRIP]->() WITH zones, count(y) AS yellow
        MATCH ()-[g:GREEN_TRIP]->()  RETURN zones, yellow, count(g) AS green
    """)
    return row[0] if row else {}

# Centroids
@st.cache_data
def load_centroids() -> dict:
    if not os.path.exists(CENTROIDS_PATH):
        return {}
    with open(CENTROIDS_PATH) as f:
        return json.load(f)

# Sidebar
st.sidebar.title("Filters")

cab_choice = st.sidebar.radio(
    "Vehicle type",
    options=["Both", "Yellow", "Green"],
    index=0,
)

top_n = st.sidebar.slider(
    "Top N routes / zones",
    min_value=5,
    max_value=20,
    value=10,
    step=1,
)

st.sidebar.markdown("---")
st.sidebar.caption("Data: NYC TLC January 2024")

# Determine active relationship types
if cab_choice == "Both":
    rel_types = ("YELLOW_TRIP", "GREEN_TRIP")
    active_rel = "YELLOW_TRIP"          # for single-type charts
elif cab_choice == "Yellow":
    rel_types = ("YELLOW_TRIP",)
    active_rel = "YELLOW_TRIP"
else:
    rel_types = ("GREEN_TRIP",)
    active_rel = "GREEN_TRIP"

# Header
st.title("NYC Taxi — January 2024")
st.caption(
    "Graph traversal via Neo4j Cypher · Analytical aggregation via DuckDB embedded in Neo4j"
)

# KPI tiles
summary = load_graph_summary()
k1, k2, k3 = st.columns(3)
k1.metric("Zone nodes", f"{summary.get('zones', 0):,}")
k2.metric("Yellow trip routes", f"{summary.get('yellow', 0):,}")
k3.metric("Green trip routes", f"{summary.get('green', 0):,}")

st.markdown("---")

# Map
st.subheader("Pickup zones by trip volume")

centroids = load_centroids()
zone_counts = load_zone_trip_counts(rel_types)

if not zone_counts.empty and centroids:
    max_count = zone_counts["tripCount"].max()

    m = folium.Map(
        location=[40.7128, -73.9],
        zoom_start=10,
        tiles=MAP_TILES,
        attr=MAP_ATTR,
    )

    for _, row in zone_counts.iterrows():
        key = str(row["locationId"])
        if key not in centroids:
            continue
        lat, lon = centroids[key]
        color_hex = BOROUGH_COLORS.get(row["borough"], BOROUGH_COLOR_DEFAULT)
        # Radius scaled 4–18px by trip share
        radius = 4 + 14 * (row["tripCount"] / max_count)
        folium.CircleMarker(
            location=[lat, lon],
            radius=radius,
            color="white",
            weight=1,
            fill=True,
            fill_color=color_hex,
            fill_opacity=0.85,
            tooltip=f"<b>{row['zone']}</b><br>{row['borough']}<br>Trips: {int(row['tripCount']):,}",
        ).add_to(m)

    map_slot = st.empty()
    map_slot.empty()
    with map_slot:
        components.html(m._repr_html_(), height=500)

    # Borough legend
    legend_cols = st.columns(len(BOROUGH_COLORS))
    for col, (borough, hex_color) in zip(legend_cols, BOROUGH_COLORS.items()):
        col.markdown(
            f'<span style="display:inline-block;width:12px;height:12px;'
            f'border-radius:50%;background:{hex_color};margin-right:4px;"></span>'
            f'<span style="font-size:12px">{borough}</span>',
            unsafe_allow_html=True,
        )
else:
    st.info("Map unavailable — run Notebook 1 to generate zone_centroids.json and Notebook 2 to build the graph.")

st.markdown("---")

# Charts row
col_left, col_right = st.columns(2)

# Chart 1 — Top N pickup zones (horizontal bar)
with col_left:
    cab_label = cab_choice if cab_choice != "Both" else "All"
    st.subheader(f"Top {top_n} pickup zones — {cab_label}")

    top_zones = zone_counts.head(top_n)
    if not top_zones.empty:
        bar_colors = [
            BOROUGH_COLORS.get(b, BOROUGH_COLOR_DEFAULT)
            for b in top_zones["borough"]
        ]
        fig1 = go.Figure(go.Bar(
            x=top_zones["tripCount"],
            y=top_zones["zone"],
            orientation="h",
            marker_color=bar_colors,
            marker_line_width=0,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Trips: %{x:,}<extra></extra>"
            ),
        ))
        fig1.update_layout(
            xaxis_title="Trip count",
            yaxis=dict(autorange="reversed", tickfont=dict(size=11)),
            margin=dict(l=0, r=16, t=8, b=40),
            height=380,
            plot_bgcolor="#ffffff",
            paper_bgcolor="#ffffff",
            font=dict(color="#1e293b"),
            xaxis=dict(gridcolor="#e2e8f0", gridwidth=1, zeroline=False),
        )
        st.plotly_chart(fig1, use_container_width=True)
    else:
        st.info("No data — build the graph first.")

# Chart 2 — Avg fare vs avg distance scatter
with col_right:
    st.subheader(f"Avg fare vs avg distance — {cab_choice}")

    if cab_choice == "Both":
        fare_y = load_fare_distance("YELLOW_TRIP")
        fare_g = load_fare_distance("GREEN_TRIP")
        traces = []
        for df_c, cab_name in [(fare_y, "Yellow"), (fare_g, "Green")]:
            if df_c.empty:
                continue
            traces.append(go.Scatter(
                x=df_c["avgDistance"],
                y=df_c["avgFare"],
                mode="markers",
                name=cab_name,
                marker=dict(
                    color=CAB_COLORS[cab_name],
                    size=6,
                    opacity=0.6,
                    line=dict(width=1, color="#ffffff"),
                ),
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Borough: %{customdata[1]}<br>"
                    "Avg distance: %{x:.2f} mi<br>"
                    "Avg fare: $%{y:.2f}<extra></extra>"
                ),
                customdata=df_c[["puZone", "borough"]].values,
            ))
        fig2 = go.Figure(traces)
    else:
        fare_df = load_fare_distance(active_rel)
        cab_name = "Yellow" if active_rel == "YELLOW_TRIP" else "Green"
        fig2 = go.Figure(go.Scatter(
            x=fare_df["avgDistance"] if not fare_df.empty else [],
            y=fare_df["avgFare"]     if not fare_df.empty else [],
            mode="markers",
            name=cab_name,
            marker=dict(
                color=CAB_COLORS[cab_name],
                size=6,
                opacity=0.6,
                line=dict(width=1, color="#ffffff"),
            ),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Borough: %{customdata[1]}<br>"
                "Avg distance: %{x:.2f} mi<br>"
                "Avg fare: $%{y:.2f}<extra></extra>"
            ),
            customdata=fare_df[["puZone", "borough"]].values if not fare_df.empty else [],
        ))

    fig2.update_layout(
        xaxis_title="Avg trip distance (miles)",
        yaxis_title="Avg fare (USD)",
        margin=dict(l=0, r=16, t=8, b=40),
        height=380,
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        font=dict(color="#1e293b"),
        xaxis=dict(gridcolor="#e2e8f0", gridwidth=1, zeroline=False),
        yaxis=dict(gridcolor="#e2e8f0", gridwidth=1, zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig2, use_container_width=True)

# Chart 3 — Yellow vs green avg fare comparison
st.subheader(f"Yellow vs green avg fare — top {top_n} shared routes")

comp_df = load_yellow_green_comparison(top_n)
if not comp_df.empty:
    comp_df["route"] = comp_df["puZone"].str[:18] + " → " + comp_df["doZone"].str[:18]

    fig3 = go.Figure()
    fig3.add_trace(go.Bar(
        name="Yellow",
        x=comp_df["route"],
        y=comp_df["yellowAvgFare"],
        marker_color=CAB_COLORS["Yellow"],
        marker_line_width=0,
        hovertemplate="Yellow avg fare: $%{y:.2f}<extra></extra>",
    ))
    fig3.add_trace(go.Bar(
        name="Green",
        x=comp_df["route"],
        y=comp_df["greenAvgFare"],
        marker_color=CAB_COLORS["Green"],
        marker_line_width=0,
        hovertemplate="Green avg fare: $%{y:.2f}<extra></extra>",
    ))
    fig3.update_layout(
        barmode="group",
        xaxis_tickangle=-35,
        xaxis_title=None,
        yaxis_title="Avg fare (USD)",
        margin=dict(l=0, r=16, t=8, b=120),
        height=400,
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        font=dict(color="#1e293b"),
        xaxis=dict(tickfont=dict(size=10), gridcolor="#e2e8f0"),
        yaxis=dict(gridcolor="#e2e8f0", gridwidth=1, zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig3, use_container_width=True)
else:
    st.info("No shared routes found — build the graph first.")

# Top routes table
st.markdown("---")
st.subheader(f"Top {top_n} routes — {cab_choice}")

routes_df = load_top_routes(active_rel, top_n)
if not routes_df.empty:
    routes_df = routes_df.rename(columns={
        "puZone":            "Pickup zone",
        "doZone":            "Dropoff zone",
        "tripCount":         "Trips",
        "avgFare":           "Avg fare ($)",
        "avgDistance":       "Avg distance (mi)",
        "avgDurationMinutes":"Avg duration (min)",
    })
    st.dataframe(
        routes_df.style.format({
            "Trips":              "{:,.0f}",
            "Avg fare ($)":       "{:.2f}",
            "Avg distance (mi)":  "{:.2f}",
            "Avg duration (min)": "{:.1f}",
        }),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No route data — build the graph first.")
