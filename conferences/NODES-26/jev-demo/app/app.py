import html
import math
import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

import core
from config_validator import load_config

HERE = Path(__file__).resolve().parent
CFG, ERRORS = load_config(HERE / "config.yaml")   # paths inside are relative to this folder too
st.set_page_config(page_title=CFG.get("app", {}).get("title", "Transit routing"), layout="wide")
if ERRORS:
    st.error("Configuration problem:\n\n" + "\n".join(f"- {e}" for e in ERRORS))
    st.stop()

RED, AMBER, GREEN, GREY = [210, 40, 40], [245, 180, 0], [30, 160, 70], [190, 190, 190]
PALETTE = ["#0072CE", "#E67800", "#8E44C8", "#00968F"]          # one colour per proposed route
RGB = [[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in PALETTE]
RC = CFG["routes"]


@st.cache_resource
def get_graph():
    return core.Graph(CFG)


@st.cache_data
def get_data():
    return core.load_data(CFG["paths"])


graph = get_graph()
coords, edges, data_error = get_data()
if data_error:
    get_data.clear()   # fix the file and reload the page: a failure isn't kept
ss = st.session_state
for key, default in (("status", {}), ("result", None), ("notice", None), ("scenario", None), ("routes", []),
                     ("jkey", None), ("sweep", None), ("curve", None), ("info", None), ("routes_source", "python")):
    ss.setdefault(key, default)


def decide_now(mode):
    """Make the graph match the current incident, read each route's status, ask Jev (or replay), remember it."""
    status = core.clean_status(ss.status)
    ss.status = status
    routes = ss.routes
    if len(routes) < 2:
        ss.result, ss.info = None, None
        ss.notice = "Fewer than two distinct routes, so there is nothing to choose between. Try more lines or a lower overlap."
        return
    notice, live = None, graph.up
    info = None
    if live:
        try:
            graph.apply_status(status)
            info = graph.route_status(routes)
        except Exception as exc:
            graph.lost(exc)
            notice, live = core.explain(exc, graph.uri, graph.database), False
    if info is None:
        info = core.python_route_status(routes, status)
    ss.info = info
    state = core.build_state(ORIGIN, DEST, LINES, routes, info)
    questions = core.build_questions(ORIGIN, DEST, routes)
    key = core.replay_key(ORIGIN, DEST, LINES, routes, info)
    result = None
    if mode != "replay" and live:
        try:
            result = graph.decide(state, questions, mode)
        except Exception as exc:
            graph.lost(exc)
            notice, live = core.explain(exc, graph.uri, graph.database), False
        if result and result.get("error"):
            notice = f"{mode.capitalize()}: {result['error']} Switch to replay in the sidebar if this persists."
            result = None
    if result is not None:
        if store and live:
            try:
                result["stored"] = graph.remember(ORIGIN, DEST, routes, result, state, info)
            except Exception as exc:
                graph.lost(exc)
                notice = (notice or "") + " Not stored: " + core.explain(exc, graph.uri, graph.database)
        if mode == "hosted" and CFG["replay"]["record"]:
            problem = core.save_replay(CFG["paths"]["replay"], key, result)
            if problem:
                notice = (notice or "") + " " + problem
    else:
        saved, problem = core.read_replay(CFG["paths"]["replay"])
        result = core.from_replay(saved, key)
        if result is None:
            notice = (notice or "") + (f" The replay file has a problem: {problem}." if problem else
                                       " No recording for this state. Run it once live in hosted mode and it is saved for replay.")
        elif mode != "replay" and not notice:
            notice = "Showing the recorded result."
    if result is not None:
        result["mode"] = "replay" if result.get("source") == "recorded" else mode   # what produced it, not what the radio says now
    ss.result = result
    ss.notice = notice.strip() if notice else None


def find_routes():
    """The graph proposes the options for the current journey. Falls back to Python when there is no Neo4j."""
    routes, notice, source, tried = None, None, "python", graph.up
    if tried:
        try:
            routes = graph.propose(ORIGIN, DEST, K, LINES)
            source = "gds"
        except Exception as exc:
            graph.lost(exc)
            notice = core.explain(exc, graph.uri, graph.database)
    if not routes:   # no Neo4j, an error, or no routes from the graph (are the stations loaded?): the CSV can still answer
        routes = core.python_propose(edges, ORIGIN, DEST, K, LINES, RC)
        source = "python"
        if tried and routes:
            notice = (notice or "Neo4j found no routes (are the stations loaded?).") + " Routes found in Python instead."
    ss.routes = routes
    ss.routes_source = source
    ss.status, ss.result, ss.info, ss.scenario, ss.sweep, ss.curve = {}, None, None, None, None, None
    ss.notice = notice.strip() if notice else None


if data_error:
    st.error(data_error)
    st.stop()

stations = sorted(coords)
line_names = sorted({e[2] for e in edges})

with st.sidebar:
    st.header("Journey")
    cfg_j = CFG["journey"]
    LINES = st.multiselect("Lines (none = whole network)", line_names,
                           default=[x for x in cfg_j["lines"] if x in line_names])
    on_lines = sorted({n for x, y, ln in edges if not LINES or ln in LINES for n in (x, y)})
    o_idx = on_lines.index(cfg_j["origin"]) if cfg_j["origin"] in on_lines else 0
    d_idx = on_lines.index(cfg_j["destination"]) if cfg_j["destination"] in on_lines else min(1, len(on_lines) - 1)
    ORIGIN = st.selectbox("From", on_lines, index=o_idx)
    DEST = st.selectbox("To", on_lines, index=d_idx)
    K = st.slider("Options for Jev", 2, RC["max_k"], RC["k"]) if RC["max_k"] > 2 else 2   # a slider needs min < max
    st.header("Settings")
    mode = st.radio("Model", core.MODES, index=0,
                    help="hosted: Jev in the cloud. local: tev1:4b through Ollama. replay: saved results, no model call.")
    store = st.checkbox("Remember decisions in the graph", value=True, disabled=not graph.up,
                        help="Each live decision is stored as a Decision node linked to the stations that were disrupted.")
    if graph.up:
        st.success("Neo4j connected")
    else:
        st.warning(f"No Neo4j: {graph.error} Map from CSV, routes from Python, decisions from replay.")
    c1, c2 = st.columns(2)
    if c1.button("Reconnect"):
        graph.close()   # let go of the old connection before making a new one
        get_graph.clear()
        st.rerun()
    if c2.button("Reset all"):
        ss.status, ss.result, ss.notice, ss.scenario, ss.info = {}, None, None, None, None
        ss.sweep, ss.curve = None, None
        if graph.up:
            try:
                graph.apply_status({})
            except Exception as exc:
                graph.lost(exc)
                st.warning(core.explain(exc, graph.uri, graph.database))
        st.rerun()
    if st.button("Pre-flight check"):
        with st.spinner("Checking..."):
            try:
                checks = graph.preflight({"origin": ORIGIN, "destination": DEST, "lines": LINES, "k": K})
            except Exception as exc:
                checks = [("Pre-flight", False, core.explain(exc, graph.uri, graph.database))]
            for label, ok, detail in checks:
                (st.success if ok else st.warning)(f"{label}: {detail}")
    st.caption("All incidents are simulated.")

st.warning("Simulated incidents. Nothing here comes from a live service.")
st.title(CFG["app"]["title"])

if ORIGIN == DEST:
    st.info("Pick two different stations in the sidebar.")
    st.stop()

jkey = (ORIGIN, DEST, tuple(LINES), K)
if ss.jkey != jkey:
    with st.spinner("Finding routes..."):
        find_routes()
    ss.jkey = jkey
    if graph.up:   # a new journey starts from a clean network, so old incidents don't leak in
        try:
            graph.apply_status({})
        except Exception as exc:
            graph.lost(exc)

routes = ss.routes
if not routes:
    st.error(f"No route from {ORIGIN} to {DEST}" + (f" on {', '.join(LINES)}." if LINES else ".")
             + " Try adding lines in the sidebar.")
    if ss.notice:
        st.warning(ss.notice)
    st.stop()

# the options the graph proposed
st.subheader("The graph proposes")
st.dataframe(core.routes_table(routes, coords), hide_index=True, width="stretch")
how = "GDS (Yen's k shortest paths) in Neo4j" if ss.routes_source == "gds" else ("Python (no Neo4j)" if not graph.up else "Python (not from Neo4j)")
st.caption(f"{len(routes)} distinct route{'' if len(routes) == 1 else 's'} from {how}, ranked by stops plus {RC['interchange_cost']} per interchange.")
if len(routes) < 2:
    st.info("Only one route on these lines, so there is nothing for Jev to choose between. Try more lines in the sidebar.")

# controls
scs = core.scenarios(routes)
cols = st.columns(len(scs))
for col, sc in zip(cols, scs):
    if col.button(sc["label"], width="stretch", key="sc_" + sc["label"]):
        ss.status = {k: dict(v) for k, v in sc["status"].items()}
        ss.scenario = sc["label"]
        with st.spinner("Asking Jev..."):
            decide_now(mode)

owner = {}
for r in routes:
    for n in r["stations"][1:-1]:
        owner.setdefault(n, []).append(r["label"])
choices = {f"{n} ({'all routes' if len(o) == len(routes) else ', '.join(o)})": n for n, o in owner.items()}
labels = list(choices)
if labels:
    a, b, c, d = st.columns([3, 1, 1, 1])
    station = choices[a.selectbox("Own incident: station", labels)]
    for col, label, val in ((b, "Suspend", {"active": False, "delay": 0}),
                            (c, "Delay 5 min", {"active": True, "delay": 5}),
                            (d, "Clear", None)):
        col.write("")
        if col.button(label, width="stretch", key="own_" + label):
            if val is None:
                ss.status.pop(station, None)
            else:
                ss.status[station] = dict(val)
            ss.scenario = "Own incident"
            with st.spinner("Asking Jev..."):
                decide_now(mode)
else:   # a direct hop has no station in between
    st.info("These routes have no station in between, so there is nowhere to add an incident.")

# map
down = {n for n, v in ss.status.items() if not v.get("active", True)}
late = {n for n, v in ss.status.items() if v.get("active", True) and v.get("delay", 0) > 0}
chosen = next((r for r in routes if ss.result and r["label"] == ss.result["choice"]), None)

grey = pd.DataFrame([{"s": coords[x][::-1], "t": coords[y][::-1]} for x, y, _ in edges])
layers = []
if not grey.empty:
    layers.append(pdk.Layer("LineLayer", grey, get_source_position="s", get_target_position="t",
                            get_color=GREY + [90], get_width=2, width_min_pixels=1))
if chosen:   # green halo under the chosen route
    pts = [list(coords[n][::-1]) for n in chosen["stations"] if n in coords]
    layers.append(pdk.Layer("PathLayer", [{"path": pts}], get_path="path", get_color=GREEN + [230],
                            get_width=12, width_min_pixels=11))
for i, r in enumerate(routes):
    pts = [list(coords[n][::-1]) for n in r["stations"] if n in coords]
    layers.append(pdk.Layer("PathLayer", [{"path": pts}], get_path="path", get_color=RGB[i % len(RGB)],
                            get_width=6, width_min_pixels=4))
on_route = {n for r in routes for n in r["stations"]}
rows = []
for n in on_route | down | late:
    if n not in coords:
        continue
    if n in (ORIGIN, DEST):
        colr, state, rad = [20, 20, 20], "origin" if n == ORIGIN else "destination", 8
    elif n in down:
        colr, state, rad = RED, "suspended", 9
    elif n in late:
        colr, state, rad = AMBER, f"delayed {ss.status[n]['delay']} min", 9
    else:
        colr, state, rad = [255, 255, 255], "running", 5
    rows.append({"pos": coords[n][::-1], "color": colr, "name": n, "state": state, "r": rad})
layers.append(pdk.Layer("ScatterplotLayer", pd.DataFrame(rows), get_position="pos", get_fill_color="color",
                        get_line_color=[40, 40, 40], stroked=True, line_width_min_pixels=1,
                        get_radius="r", radius_units="'pixels'", pickable=True))
lats = [coords[n][0] for n in on_route if n in coords]
lons = [coords[n][1] for n in on_route if n in coords]
deck = None
if lats:   # none of the stations on the routes has coordinates: skip the map, keep the decision
    span = max(max(lons) - min(lons), (max(lats) - min(lats)) * 2, 0.01)
    zoom = min(13, max(8.5, math.log2(360 / span) - 0.3))
    deck = pdk.Deck(layers=layers,
                    initial_view_state=pdk.ViewState(latitude=sum(lats) / len(lats), longitude=sum(lons) / len(lons),
                                                     zoom=zoom),
                    map_style="https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json" if CFG["app"]["basemap"] else None,
                    tooltip={"text": "{name}: {state}"})

left, right = st.columns([3, 1])
with left:
    if deck is not None:
        st.pydeck_chart(deck, height=500, key=f"map_{hash(jkey)}")
    else:
        st.warning("The stations on these routes have no coordinates, so the map can't be drawn.")
    legend = " ".join(f'<span style="color:{PALETTE[i % len(PALETTE)]}">&#9632;</span> {html.escape(r["label"])}&nbsp;&nbsp;'
                      for i, r in enumerate(routes))
    st.markdown(legend, unsafe_allow_html=True)
    st.caption("Red: suspended. Amber: delayed. Green halo: the route Jev chose. Black: the two ends of the journey. "
               "Where routes share track the later colour is drawn on top.")
with right:
    st.subheader("Decision")
    if ss.notice:
        st.warning(ss.notice)
    r = ss.result
    if r:
        st.markdown(f"### {r['choice']}")
        for k_, v in sorted(r["probs"].items(), key=lambda kv: -kv[1]):
            st.write(f"{k_}: {v:.2f}")
            st.progress(min(max(float(v), 0.0), 1.0))
        margin = r.get("margin")
        st.metric("Margin", "n/a" if margin is None else f"{margin:.2f}")
        if margin is not None and margin < CFG["app"]["low_margin"]:
            blocked = [x["label"] for x in routes if ss.info and ss.info[x["label"]]["suspended"]]
            why = (" Every route has a suspended station." if len(blocked) == len(routes) else "")
            st.warning(f"Close call: the margin is under {CFG['app']['low_margin']:.2f}.{why} "
                       "This is a case for a human check" + (", or add more lines in the sidebar so another route can appear." if LINES else "."))
        label = "recorded" if r["source"] == "recorded" else "live"
        ms = r.get("latency_ms")
        st.caption(f"{'n/a' if ms is None else ms} ms ({label}) | {r.get('model') or 'model not reported'} | {r.get('mode', mode)}")
        if r.get("stored"):
            st.caption(f"Stored in the graph as decision {r['stored'][:8]}.")
    else:
        st.info("Pick a scenario or add an incident.")
    if chosen:
        st.subheader("The chosen route")
        km = core.route_km(coords, chosen["stations"])
        st.write(f"{ORIGIN} to {DEST}: **{chosen['stops']} stops, {chosen['interchanges']} interchange"
                 f"{'' if chosen['interchanges'] == 1 else 's'}, {km:.1f} km**")
        st.caption(" > ".join(chosen["stations"]))
        st.caption("Lines: " + " > ".join(leg["line"] for leg in chosen["legs"]) +
                   ". Distance is the straight line between neighboring stations, added up.")
        if ss.info:
            st.caption("Status: " + ss.info[chosen["label"]]["status"])

# optional views
st.divider()
tab_log, tab_sweep, tab_curve = st.tabs(["Decision log", "Single outage", "Delay curve"])
route_colors = {r["label"]: PALETTE[i % len(PALETTE)] for i, r in enumerate(routes)}


def batch_blocked():
    if mode == "replay":
        return "Not available in replay mode: these states have no recording."
    if not graph.up:
        return "Not available without Neo4j."
    if len(routes) < 2 or not core.middle_stations(routes):
        return "Needs two or more routes with stations in between."
    return None


def run_batch(items):
    """Run jev.decideAll for the current mode. Returns (rows, ms) or None after showing one line why not."""
    t0 = time.perf_counter()
    try:
        rows = graph.decide_all(items, core.build_questions(ORIGIN, DEST, routes), mode)
    except Exception as exc:
        graph.lost(exc)
        st.warning(core.explain(exc, graph.uri, graph.database))
        return None
    ms = round((time.perf_counter() - t0) * 1000)
    if not rows or all(r["error"] for r in rows):
        st.warning(f"All {len(rows)} decisions failed: {rows[0]['error']}" if rows else "No decisions came back.")
        return None
    return rows, ms


with tab_log:
    st.caption("Every live decision is stored in the graph as a Decision node, linked to the stations that were "
               "disrupted at the time. These are Cypher queries over what Jev decided.")
    if not graph.up:
        st.info("Not available without Neo4j.")
    else:
        lc1, lc2 = st.columns([1, 1])
        this_only = lc1.checkbox("This journey only", value=True)
        if lc2.button("Clear the log", key="clear_log"):
            try:
                graph.clear_log()
            except Exception as exc:
                graph.lost(exc)
                st.warning(core.explain(exc, graph.uri, graph.database))
        try:
            log_rows, involved = graph.log()
        except Exception as exc:
            graph.lost(exc)
            log_rows, involved = [], []
            st.warning(core.explain(exc, graph.uri, graph.database))
        if this_only:
            log_rows = [x for x in log_rows if x["origin"] == ORIGIN and x["destination"] == DEST]
        if not log_rows:
            st.info("No decisions stored yet. Pick a scenario above.")
        else:
            df = pd.DataFrame(log_rows)
            for col in ("margin", "probability"):   # a decision stored without a margin has None here
                df[col] = pd.to_numeric(df[col], errors="coerce").round(2)
            df["concerns"] = df["concerns"].apply(lambda x: ", ".join(x) if x else "nothing disrupted")
            st.dataframe(df[["asked", "origin", "destination", "choice", "margin", "probability", "model", "concerns"]],
                         hide_index=True, width="stretch")
        if involved:
            fig = go.Figure(go.Bar(x=[x["station"] for x in involved], y=[x["decisions"] for x in involved],
                                   hovertemplate="%{x}<br>%{y} decisions<extra></extra>"))
            fig.update_layout(title="Stations most often involved in a decision (all journeys)",
                              yaxis_title="Decisions", height=300, margin=dict(t=60, b=40))
            st.plotly_chart(fig, width="stretch")

with tab_sweep:
    st.caption(f"Each station on the proposed routes (up to {core.MAX_SWEEP}) suspended on its own, nothing else. "
               "One jev.decideAll call.")
    blocked = batch_blocked()
    if blocked:
        st.info(blocked)
    if st.button("Run the sweep", disabled=bool(blocked), key="run_sweep"):
        items = core.outage_items(ORIGIN, DEST, LINES, routes)
        with st.spinner(f"Asking Jev about {len(items)} outages..."):
            out = run_batch(items)
        ss.sweep = {"rows": out[0], "ms": out[1], "mode": mode} if out else None
    sw = ss.sweep
    if sw:
        fig = go.Figure()
        for r in routes:
            fig.add_bar(name=r["label"], x=[x["id"] for x in sw["rows"]],
                        y=[x["probs"].get(r["label"]) if not x["error"] else None for x in sw["rows"]],
                        marker_color=route_colors[r["label"]],
                        hovertemplate="%{x} out<br>" + html.escape(r["label"]) + " %{y:.2f}<extra></extra>")
        fig.update_layout(barmode="group", title="Probability of each route when this station alone is suspended",
                          yaxis_title="Probability", yaxis_range=[0, 1], xaxis_title="Station suspended",
                          height=400, margin=dict(t=60, b=40))
        st.plotly_chart(fig, width="stretch")
        ok = [x for x in sw["rows"] if not x["error"]]
        counts = ", ".join(f"{r['label']}: {sum(x['choice'] == r['label'] for x in ok)}" for r in routes)
        st.caption(f"Routes chosen across {len(ok)} outages ({counts}). {len(sw['rows'])} decisions in {sw['ms']} ms "
                   f"({sw['mode']}). One live run, so the numbers shift a little each time.")

with tab_curve:
    st.caption(f"One station delayed by 0 to {core.DELAY_MAX} minutes, nothing else. One jev.decideAll call.")
    first_mild = next(iter(scs[1]["status"]), None) if len(scs) > 1 else None
    default_station = next((i for i, n in enumerate(choices.values()) if n == first_mild), 0)
    cstation = choices[st.selectbox("Station", labels, index=default_station, key="curve_station")] if labels else None
    blocked = batch_blocked()
    if blocked:
        st.info(blocked)
    if st.button("Run the curve", disabled=bool(blocked) or cstation is None, key="run_curve"):
        with st.spinner(f"Asking Jev about {core.DELAY_MAX + 1} delays..."):
            out = run_batch(core.delay_items(ORIGIN, DEST, LINES, routes, cstation))
        ss.curve = {"rows": out[0], "ms": out[1], "mode": mode, "station": cstation} if out else None
    cv = ss.curve
    if cv:
        ok = [x for x in cv["rows"] if not x["error"]]
        fig = go.Figure()
        for r in routes:
            fig.add_scatter(name=r["label"], x=[int(x["id"]) for x in ok], y=[x["probs"].get(r["label"]) for x in ok],
                            mode="lines+markers", line_color=route_colors[r["label"]],
                            hovertemplate="%{x} min<br>" + html.escape(r["label"]) + " %{y:.2f}<extra></extra>")
        fig.update_layout(title=f"Probability of each route as {cv['station']} is delayed longer",
                          xaxis_title="Delay (minutes)", yaxis_title="Probability", yaxis_range=[0, 1],
                          height=380, margin=dict(t=60, b=40))
        st.plotly_chart(fig, width="stretch")
        first = ok[0]["choice"] if ok else None
        flip = next((x for x in ok if x["choice"] != first), None)
        verdict = (f"The decision changes from {first} to {flip['choice']} at {flip['id']} min."
                   if flip else f"The decision stays {first} from 0 to {core.DELAY_MAX} minutes.")
        st.caption(f"{verdict} {len(cv['rows'])} decisions in {cv['ms']} ms ({cv['mode']}). "
                   "One live run, so the numbers shift a little each time.")
