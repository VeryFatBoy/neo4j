"""Pre-talk check for the transit routing app.

    cd app
    python check_app.py            # offline, about 30 seconds. Never touches Neo4j, Jev or the replay file.
    python check_app.py --live     # the same, then the pre-flight against Neo4j and Jev (needs NEO4J_* to be set)

It checks the config and the data files, whether the replay file still covers the journeys you recorded, and then
drives the real app headless through a set of journeys (including the ones that used to crash it), pressing every
scenario button and every incident button. Exit code 0 means everything passed.
"""
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.chdir(HERE)
LIVE = "--live" in sys.argv
if not LIVE:   # offline: the app falls back to Python routes and replay, so nothing can reach the database or the model
    for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE"):
        os.environ.pop(name, None)

import streamlit as st                       # noqa: E402
from streamlit.testing.v1 import AppTest    # noqa: E402

import core                                  # noqa: E402
from config_validator import load_config     # noqa: E402

failures = 0


def report(status, name, detail=""):
    global failures
    failures += status == "FAIL"
    print(f"{status:5s} {name}" + (f": {detail}" if detail else ""), flush=True)


# ---------------------------------------------------------------- 1. config and data
cfg, errors = load_config(HERE / "config.yaml")
report("FAIL" if errors else "PASS", "config.yaml", "; ".join(errors) or "valid")
if errors:
    sys.exit(1)
coords, edges, err = core.load_data(cfg["paths"])
report("FAIL" if err else "PASS", "data files", err or f"{len(coords)} stations, {len(edges)} connections, "
                                                  f"{len({e[2] for e in edges})} lines")
if err:
    sys.exit(1)

# ---------------------------------------------------------------- 2. the replay file
store, problem = core.read_replay(cfg["paths"]["replay"])
report("FAIL" if problem else "PASS", "replay file", problem or f"{len(store)} saved decisions")
groups = {}
for key in store:
    parts = key.split(" | ")
    if parts[0] == "v2" and len(parts) > 3:
        origin, dest = parts[1].split(" > ")
        lines = [] if parts[2] == "all lines" else parts[2].split(", ")
        groups.setdefault((origin, dest, tuple(lines), len(parts) - 3), 0)
        groups[(origin, dest, tuple(lines), len(parts) - 3)] += 1
J = cfg["journey"]
default = (J["origin"], J["destination"], tuple(J["lines"]), cfg["routes"]["k"])
groups.setdefault(default, 0)
for (origin, dest, lines, k), n in sorted(groups.items()):
    routes = core.python_propose(edges, origin, dest, k, list(lines), cfg["routes"])
    scs = core.scenarios(routes) if len(routes) > 1 else []
    hit = 0
    for sc in scs:
        info = core.python_route_status(routes, core.clean_status(sc["status"]))
        hit += core.from_replay(store, core.replay_key(origin, dest, list(lines), routes, info)) is not None
    name = f"replay covers {origin} > {dest} ({', '.join(lines) or 'all lines'}, {k} routes)"
    if not scs:
        report("WARN", name, "fewer than two routes now, nothing to replay")
    elif hit == len(scs):
        report("PASS", name, f"all {hit} scenario buttons have a recording")
    else:   # the default journey is what the talk opens with, so a gap there is a failure
        report("FAIL" if (origin, dest, lines, k) == default else "WARN", name,
               f"{hit} of {len(scs)} scenario buttons have a recording. Run the others once in hosted mode to save them.")

# ---------------------------------------------------------------- 3. the app, headless
JOURNEYS = [
    ("opening journey", J["lines"], J["origin"], J["destination"], None),
    ("opening journey, 3 options", J["lines"], J["origin"], J["destination"], 3),
    ("whole network, Kennington > Euston", [], "Kennington", "Euston", None),
    ("whole network, Kennington > Wimbledon", [], "Kennington", "Wimbledon", None),
    ("Waterloo and City only (Bank > Waterloo, no station in between)", ["Waterloo and City"], "Bank", "Waterloo", None),
    ("direct hop on one line", ["Northern"], "Kennington", "Oval", None),
    ("direct hop, whole network", [], "Kennington", "Oval", None),
    ("one route only", ["Victoria"], "Brixton", "Walthamstow Central", None),
    ("two lines", ["Jubilee", "Northern"], "Kennington", "Stratford", None),
    ("no route between the two ends", ["Waterloo and City", "Tramlink"], "Bank", "Wimbledon", None),
]


def errors_of(at):
    out = []
    for e in at.exception:
        frames = getattr(e, "stack_trace", None) or []
        found = re.search(r'(\w+\.py)", line (\d+)', str(frames[-1])) if frames else None
        out.append(f"{str(getattr(e, 'value', e))[:90]}" + (f" ({found.group(1)} line {found.group(2)})" if found else ""))
    return out


for label, lines, origin, dest, k in JOURNEYS:
    if origin not in coords or dest not in coords:
        report("WARN", label, f"skipped: {origin} or {dest} isn't in the data")
        continue
    st.cache_data.clear()
    st.cache_resource.clear()
    t0, problems, presses = time.perf_counter(), [], 0
    try:
        at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)
        at.run()
        side = lambda: (at.sidebar.multiselect[0], at.sidebar.selectbox[0], at.sidebar.selectbox[1], at.sidebar.slider)  # noqa: E731
        side()[0].set_value(lines)
        at.run()
        s = side()
        if origin in s[1].options and dest in s[2].options:
            s[1].select(origin)
            s[2].select(dest)
        if k and s[3]:
            s[3][0].set_value(k)
        at.run()
        problems += errors_of(at)
        if not problems:
            at.sidebar.radio[0].set_value("replay")
            at.run()
            keys = [b.key for b in at.main.button if b.key and (b.key.startswith("sc_") or b.key.startswith("own_"))]
            for key in keys:
                at.main.button(key=key).click().run()
                presses += 1
                problems += errors_of(at)
                if problems:
                    problems.insert(0, f"after pressing {key}")
                    break
    except Exception as exc:   # a problem in this script rather than in the app
        problems.append("check script: " + repr(exc)[:100])
    shown = f"{at.sidebar.selectbox[0].value} > {at.sidebar.selectbox[1].value}" if not problems else ""
    report("FAIL" if problems else "PASS", label, "; ".join(problems) or f"{shown}, {presses} buttons pressed, "
                                                                        f"{time.perf_counter() - t0:.1f}s")

# ---------------------------------------------------------------- 4. Neo4j and Jev, only with --live
if LIVE:
    graph = core.Graph(cfg)
    if not graph.up:
        report("FAIL", "Neo4j", graph.error)
    else:
        for label, ok, detail in graph.preflight({"origin": J["origin"], "destination": J["destination"],
                                                  "lines": J["lines"], "k": cfg["routes"]["k"]}):
            report("PASS" if ok else ("WARN" if label == "Jev local" else "FAIL"), label, detail)   # the local model is optional
        graph.close()
else:
    print("      (run with --live to check Neo4j and Jev as well)")

print(f"\n{'ALL CHECKS PASSED' if not failures else str(failures) + ' CHECK(S) FAILED'}")
sys.exit(1 if failures else 0)
