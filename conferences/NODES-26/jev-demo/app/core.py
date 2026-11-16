"""Logic for the transit routing demo: the graph proposes routes, Jev picks one, the graph remembers.
Neo4j + GDS, replay, errors, and a no-database fallback. No Streamlit imports here, so it can be tested on its own."""
import heapq
import json
import logging
import math
import os
import re
import shutil
import time
from collections import deque
from pathlib import Path

import pandas as pd
from neo4j import GraphDatabase, NotificationMinimumSeverity
from neo4j.exceptions import AuthError, ServiceUnavailable, SessionExpired

log = logging.getLogger(__name__)

MODES = ["hosted", "local", "replay"]
MAX_SWEEP = 30      # most stations the single-outage sweep will ask about in one call
DELAY_MAX = 10      # longest delay on the delay curve, in minutes


# ---------- errors ----------

class NotConnected(Exception):
    pass


class StationsMissing(Exception):
    """Neo4j is reachable but doesn't hold the stations of this journey."""


def explain(exc, uri="", database=""):
    text, code = str(exc), getattr(exc, "code", "") or ""
    missing_fn = "Unknown function" in text or "ProcedureNotFound" in code or "no procedure" in text.lower()
    if isinstance(exc, NotConnected):
        return "Not connected to Neo4j. Set NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD and NEO4J_DATABASE."
    if isinstance(exc, StationsMissing):
        return "Neo4j doesn't have these stations. Load them first (Demo 2, section 5)."
    if isinstance(exc, AuthError):
        return "Neo4j rejected the login. Check NEO4J_USERNAME and NEO4J_PASSWORD."
    if isinstance(exc, (ServiceUnavailable, SessionExpired, ConnectionError, OSError)):
        return f"Can't reach Neo4j at {uri}. Start the DBMS in Neo4j Desktop."
    if "DatabaseNotFound" in code:
        return f"Database '{database}' doesn't exist. Check NEO4J_DATABASE."
    if "DatabaseUnavailable" in code:
        return "The database isn't available yet. Wait for it to finish starting."
    if "jev." in text and missing_fn:
        return "The jev functions aren't available. Check the JAR, the allowlist line in neo4j.conf and restart the DBMS."
    if "gds." in text and missing_fn:
        return ("Graph Data Science isn't available. Install the GDS plugin in Neo4j Desktop and allowlist "
                "gds.* (dbms.security.procedures.allowlist and dbms.security.procedures.unrestricted).")
    return f"The query failed: {type(exc).__name__}: {text[:200]}"


def describe_error(message):
    lower = message.lower()
    if "connectexception" in lower or "unknownhost" in lower or "timeout" in lower or "timed out" in lower:
        if "11434" in message or "localhost" in lower:
            return "Can't reach the local model (is Ollama running?)."
        return "Can't reach the hosted Jev (check the network)."
    return message[:200]


# ---------- status ----------
# status: {station: {"active": bool, "delay": int}}; a station not listed is running normally.

def clean_status(status):
    return {k: v for k, v in status.items() if (not v.get("active", True)) or v.get("delay", 0) > 0}


def _summary(names, status):
    off = [n for n in names if not status.get(n, {}).get("active", True)]
    late = [f"{n} ({status[n]['delay']} min)" for n in names
            if status.get(n, {}).get("active", True) and status.get(n, {}).get("delay", 0) > 0]
    if not off and not late:
        return "All stations clear."
    parts = []
    if off:
        parts.append("Suspended: " + ", ".join(off) + ".")
    if late:
        parts.append("Delays: " + ", ".join(late) + ".")
    return " ".join(parts)


def python_route_status(routes, status):
    """Same words the STATUS_QUERY builds in Cypher, for the no-database tier and for the batch views.
    Returns {label: {status, suspended, late_names}}."""
    out = {}
    for r in routes:
        names = r["stations"]
        out[r["label"]] = {
            "status": _summary(names, status),
            "suspended": [n for n in names if not status.get(n, {}).get("active", True)],
            "late_names": [n for n in names if status.get(n, {}).get("active", True)
                           and status.get(n, {}).get("delay", 0) > 0]}
    return out


def margin_of(probs):
    try:
        p = sorted((float(v) for v in probs.values()), reverse=True)
    except (AttributeError, TypeError, ValueError):
        return None
    return p[0] - p[1] if len(p) > 1 else None


# ---------- data (CSV) ----------

def _read_csv(path, need):
    """Read one CSV and check its columns. Returns (DataFrame, None) or (None, message)."""
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")   # utf-8-sig: a file saved with a BOM (Excel) still reads
    except FileNotFoundError:
        return None, f"Can't find {path}. Put the three CSV files in the data folder."
    except pd.errors.EmptyDataError:
        return None, f"{path} is empty."
    except UnicodeDecodeError:
        return None, f"{path} isn't UTF-8 text. Save it as CSV UTF-8."
    except (OSError, ValueError) as exc:
        return None, f"Can't read {path}: {exc}"
    df.columns = df.columns.astype(str).str.strip()
    missing = [c for c in need if c not in df.columns]
    if missing:
        return None, f"{path} needs a column called {' and '.join(missing)}. It has: {', '.join(df.columns)}."
    return df, None


def load_data(paths):
    """Return (coords {name: (lat, lon)}, edges [(a, b, line)], error or None)."""
    st, err = _read_csv(paths["stations"], ["Station", "Latitude", "Longitude"])
    if err:
        return {}, [], err
    cn, err = _read_csv(paths["connections"], ["From Station", "To Station", "Tube Line"])
    if err:
        return {}, [], err
    ln, _ = _read_csv(paths["lines"], ["Tube Line"])
    if ln is not None:   # keep only the lines in the lines CSV, so the map and the fallback match the Neo4j graph
        cn = cn[cn["Tube Line"].astype(str).str.strip().isin(set(ln["Tube Line"].astype(str).str.strip()))]
    lat = pd.to_numeric(st["Latitude"], errors="coerce")
    lon = pd.to_numeric(st["Longitude"], errors="coerce")
    usable = st["Station"].notna() & lat.between(-90, 90) & lon.between(-180, 180)   # NaN fails between()
    if not usable.all():
        log.warning("%d station row(s) in %s have no usable name or coordinates and were skipped.",
                    int((~usable).sum()), paths["stations"])
    coords = {str(n).strip(): (float(a), float(b)) for n, a, b in zip(st["Station"][usable], lat[usable], lon[usable])}
    cn = cn.dropna(subset=["From Station", "To Station", "Tube Line"])
    edges = [(str(a).strip(), str(b).strip(), str(c).strip())
             for a, b, c in zip(cn["From Station"], cn["To Station"], cn["Tube Line"])]
    edges = [e for e in edges if e[0] in coords and e[1] in coords]
    if not coords:
        return {}, [], f"{paths['stations']} has no stations with usable coordinates."
    if not edges:
        return coords, [], f"No usable connections. Check {paths['connections']} and {paths['lines']}."
    return coords, edges, None


def route_km(coords, stations):
    """Straight-line distance in km along a route, summed station to station (haversine)."""
    total = 0.0
    for a, b in zip(stations, stations[1:]):
        if a not in coords or b not in coords:
            continue
        (la1, lo1), (la2, lo2) = (math.radians(x) for x in coords[a]), (math.radians(x) for x in coords[b])
        h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
        total += 2 * 6371.0088 * math.asin(math.sqrt(h))
    return total


# ---------- choosing routes (the same logic as the Demo 2 notebook) ----------

def line_plan(hop_lines):
    """Pick one line for each hop so the passenger changes as seldom as possible.
    Returns the legs of the journey as {line, first, last} hop numbers."""
    best = [{line: (0, None) for line in hop_lines[0]}]
    for lines_here in hop_lines[1:]:
        best.append({line: min((changes + (0 if prev == line else 1), prev)
                               for prev, (changes, _) in best[-1].items())
                     for line in lines_here})
    line = min(best[-1], key=lambda name: best[-1][name][0])
    chosen = [line]
    for step in range(len(best) - 1, 0, -1):
        line = best[step][line][1]
        chosen.append(line)
    chosen.reverse()
    legs = []
    for hop, line in enumerate(chosen):
        if legs and legs[-1]["line"] == line:
            legs[-1]["last"] = hop
        else:
            legs.append({"line": line, "first": hop, "last": hop})
    return legs


def describe(route):
    """The words Jev gets for one option: the stations, the stops and how the lines connect."""
    stations, legs = route["stations"], route["legs"]
    if len(legs) == 1:
        how = f"On the {legs[0]['line']} line."
    else:
        parts = [f"{leg['line']} line to {stations[leg['last'] + 1]}" for leg in legs]
        how = f"Changes: {', '.join(parts)} ({len(legs) - 1} interchange{'s' if len(legs) > 2 else ''})."
    middle = stations[1:-1]
    via = f"Via {', '.join(middle)}." if middle else "Direct, with no station in between."
    return f"{via} {route['stops']} stop{'' if route['stops'] == 1 else 's'}. {how}"


def choose_routes(pool, k, max_overlap):
    """Keep the k cheapest routes that are really different from each other."""
    chosen = []
    for route in sorted(pool, key=lambda r: (r["cost"], r["stops"])):
        middle = set(route["stations"][1:-1])
        distinct = True
        for other in chosen:
            theirs = set(other["stations"][1:-1])
            if len(middle & theirs) / (len(middle | theirs) or 1) > max_overlap:
                distinct = False
        if distinct:
            chosen.append(route)
        if len(chosen) == k:
            break
    return chosen


def label_routes(routes):
    """Name each route after the first station that only it uses, so the options read 'Via Waterloo'."""
    for route in routes:
        others = set()
        for other in routes:
            if other is not route:
                others |= set(other["stations"])
        route["unique"] = [s for s in route["stations"][1:-1] if s not in others]
        route["label"] = f"Via {route['unique'][0]}" if route["unique"] else f"Route {route['index'] + 1}"
        route["key"] = re.sub(r"[^a-z0-9]+", "_", route["label"].lower()).strip("_") or f"route_{route['index'] + 1}"


def build_routes(found, hops, k, interchange_cost, max_overlap):
    """found: list of station lists, shortest first. hops: {route index: {hop index: [lines]}}.
    Returns up to k distinct routes with legs, cost, description and label."""
    candidates = []
    for ri, stations in enumerate(found):
        by_hop = hops.get(ri, {})
        if len(stations) < 2 or len(by_hop) != len(stations) - 1 or any(not l for l in by_hop.values()):
            continue
        legs = line_plan([by_hop[i] for i in range(len(stations) - 1)])
        stops, interchanges = len(stations) - 1, len(legs) - 1
        candidates.append({"stations": stations, "stops": stops, "legs": legs, "interchanges": interchanges,
                           "cost": stops + interchange_cost * interchanges})
    routes = choose_routes(candidates, k, max_overlap)
    for index, route in enumerate(routes):
        route["index"] = index
        route["description"] = describe(route)
    label_routes(routes)
    return routes


def routes_table(routes, coords=None):
    rows = []
    for r in routes:
        row = {"route": r["label"], "stops": r["stops"], "interchanges": r["interchanges"],
               "lines": " > ".join(leg["line"] for leg in r["legs"])}
        if coords:
            row["km"] = round(route_km(coords, r["stations"]), 1)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------- no-database tier: k shortest routes in Python (Yen's algorithm, unweighted) ----------

def _bfs(adj, src, dst, banned_nodes, banned_edges):
    prev, q = {src: None}, deque([src])
    while q:
        cur = q.popleft()
        if cur == dst:
            path = []
            while cur is not None:
                path.append(cur)
                cur = prev[cur]
            return path[::-1]
        for nxt in sorted(adj.get(cur, ())):
            if nxt in prev or nxt in banned_nodes or (cur, nxt) in banned_edges:
                continue
            prev[nxt] = cur
            q.append(nxt)
    return None


def python_yens(adj, src, dst, k):
    first = _bfs(adj, src, dst, set(), set())
    if not first:
        return []
    found, heap = [first], []
    while len(found) < k:
        last = found[-1]
        for i in range(len(last) - 1):
            root = last[:i + 1]
            banned_edges = {(p[i], p[i + 1]) for p in found if len(p) > i + 1 and p[:i + 1] == root}
            spur = _bfs(adj, last[i], dst, set(root[:-1]), banned_edges)
            if spur:
                cand = root[:-1] + spur
                if cand not in found and (len(cand), cand) not in heap:
                    heapq.heappush(heap, (len(cand), cand))
        if not heap:
            break
        found.append(heapq.heappop(heap)[1])
    return found


def python_propose(edges, origin, dest, k, lines, cfg_routes):
    """The same proposal the graph makes, from the CSV edges. Returns a list of routes."""
    pair_lines, adj = {}, {}
    for a, b, ln in edges:
        if lines and ln not in lines:
            continue
        for x, y in ((a, b), (b, a)):
            pair_lines.setdefault((x, y), set()).add(ln)
            adj.setdefault(x, set()).add(y)
    found = python_yens(adj, origin, dest, cfg_routes["pool"])
    hops = {ri: {i: sorted(pair_lines[(s[i], s[i + 1])]) for i in range(len(s) - 1)} for ri, s in enumerate(found)}
    return build_routes(found, hops, k, cfg_routes["interchange_cost"], cfg_routes["max_overlap"])


# ---------- what Jev is asked ----------

def build_state(origin, dest, lines, routes, info):
    """The state Jev sees: where the passenger is, and the live status of each route, in words."""
    network = f"London Underground, {lines[0]} line" if lines and len(lines) == 1 else "London Underground"
    state = {"current_station": origin, "destination": dest, "network": network}
    for r in routes:
        state["status_" + r["key"]] = info[r["label"]]["status"]
    return state


def build_questions(origin, dest, routes):
    return {"route": {
        "type": "choice",
        "instructions": (f"Which route should the passenger take from {origin} to reach {dest} "
                         "as reliably as possible given the current state of the routes?"),
        "criteria": {r["label"]: r["description"] for r in routes},
    }}


def replay_key(origin, dest, lines, routes, info):
    """Readable key for the replay file: the journey, then each route and what is wrong on it."""
    head = f"v2 | {origin} > {dest} | {', '.join(lines) if lines else 'all lines'}"
    return " | ".join([head] + [f"{r['label']}: {info[r['label']]['status']}" for r in routes])


def scenarios(routes):
    """Four stories built from the routes the graph proposed."""
    out = [{"label": "No disruption", "status": {}}]
    if not routes:
        return out

    def pick(route):
        u = route.get("unique") or []
        return (u[-2] if len(u) > 1 else u[0]) if u else None
    a = pick(routes[0])
    b = pick(routes[1]) if len(routes) > 1 else None
    if a:
        out.append({"label": f"Mild: {a} delayed 5 min", "status": {a: {"active": True, "delay": 5}}})
        out.append({"label": f"Severe: {a} suspended", "status": {a: {"active": False, "delay": 0}}})
    if a and b:
        out.append({"label": f"Both hit: {a} and {b} suspended",
                    "status": {a: {"active": False, "delay": 0}, b: {"active": False, "delay": 0}}})
    return out


def middle_stations(routes):
    """Stations between the ends on any proposed route, in route order, each once."""
    seen, out = set(), []
    for r in routes:
        for s in r["stations"][1:-1]:
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out


def outage_items(origin, dest, lines, routes):
    """One item per station, with only that station suspended."""
    items = []
    for n in middle_stations(routes)[:MAX_SWEEP]:
        info = python_route_status(routes, {n: {"active": False, "delay": 0}})
        items.append({"id": n, "state": build_state(origin, dest, lines, routes, info)})
    return items


def delay_items(origin, dest, lines, routes, station):
    """One item per delay length from 0 to DELAY_MAX minutes for one station."""
    items = []
    for m in range(DELAY_MAX + 1):
        info = python_route_status(routes, {station: {"active": True, "delay": m}})
        items.append({"id": str(m), "state": build_state(origin, dest, lines, routes, info)})
    return items


# ---------- replay ----------

def read_replay(path):
    """Returns (saved decisions, problem). problem is None when the file is fine or just isn't there yet."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8-sig")   # utf-8-sig: an editor that adds a BOM doesn't hide the file
    except FileNotFoundError:
        return {}, None
    except (OSError, UnicodeDecodeError) as exc:
        return {}, f"{p.name} can't be read ({exc})"
    if not text.strip():
        return {}, None
    try:
        store = json.loads(text)
    except ValueError as exc:
        return {}, f"{p.name} isn't valid JSON ({exc})"
    if not isinstance(store, dict):
        return {}, f"{p.name} should hold a JSON object of saved decisions"
    return store, None


def load_replay(path):
    """Saved decisions, or {} if the file is missing or unreadable."""
    return read_replay(path)[0]


def from_replay(store, key):
    """Read one saved decision. A record that is damaged or incomplete counts as no recording."""
    rec = store.get(key) if isinstance(store, dict) else None
    if not isinstance(rec, dict) or not rec.get("choice") or not isinstance(rec.get("probabilities"), dict):
        return None
    try:
        probs = {str(k): float(v) for k, v in rec["probabilities"].items()}
    except (TypeError, ValueError):
        return None
    if not probs:
        return None
    return {"choice": rec["choice"], "probs": probs, "margin": margin_of(probs),
            "latency_ms": rec.get("ms"), "model": rec.get("recorded_with", "hosted"), "source": "recorded", "error": None}


def save_replay(path, key, result):
    """Record one live hosted result so replay mode can show it later. Returns an error message or None.
    A replay file that can't be read is left alone: writing over it would throw away every saved decision."""
    if result.get("source") != "live" or result.get("error"):
        return None
    if not result.get("choice") or not isinstance(result.get("probs"), dict) or not result["probs"]:
        return None
    store, problem = read_replay(path)
    if problem:
        return f"Not recorded: {problem}. The file was left as it is."
    store[key] = {"choice": result["choice"], "probabilities": result["probs"], "ms": result.get("latency_ms"),
                  "recorded_with": result.get("model") or "hosted"}
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():   # keep one step back, as the Demo 1 notebook does
            shutil.copy(p, p.with_name(p.name + ".bak"))
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(store, indent=2), encoding="utf-8")
        tmp.replace(p)
    except (OSError, TypeError, ValueError) as exc:
        return f"Couldn't save the replay file: {exc}"
    return None


# ---------- Cypher ----------

PROJECT_QUERY = """
MATCH (a:Station)-[r:CONNECTS_TO]->(b:Station)
WHERE $lines IS NULL OR r.line IN $lines
WITH DISTINCT a, b
WITH gds.graph.project($name, a, b) AS g
RETURN g.nodeCount AS stations, g.relationshipCount AS connections
"""

YENS_QUERY = """
MATCH (source:Station {name: $origin}), (target:Station {name: $destination})
CALL gds.shortestPath.yens.stream($name, {sourceNode: source, targetNode: target, k: $pool})
YIELD index, nodeIds
RETURN index, [id IN nodeIds | gds.util.asNode(id).name] AS stations
ORDER BY index
"""

HOP_LINES_QUERY = """
UNWIND range(0, size($routes) - 1) AS ri
UNWIND range(0, size($routes[ri]) - 2) AS i
MATCH (a:Station {name: $routes[ri][i]})-[r:CONNECTS_TO]->(b:Station {name: $routes[ri][i + 1]})
WHERE $lines IS NULL OR r.line IN $lines
RETURN ri, i, collect(DISTINCT r.line) AS lines
ORDER BY ri, i
"""

STATUS_QUERY = """
UNWIND $routes AS route
UNWIND range(0, size(route.stops) - 1) AS i
MATCH (s:Station {name: route.stops[i]})
WITH route.label AS label, i, s ORDER BY label, i
WITH label, collect(s) AS stops
WITH label,
     [s IN stops WHERE NOT coalesce(s.active, true) | s.name] AS suspended,
     [s IN stops WHERE coalesce(s.active, true) AND coalesce(s.delay_minutes, 0) > 0 | s] AS late
WITH label, suspended,
     [s IN late | s.name] AS late_names,
     [s IN late | s.name + ' (' + toString(s.delay_minutes) + ' min)'] AS delayed
WITH label, suspended, late_names,
     CASE WHEN size(suspended) = 0 THEN ''
          ELSE 'Suspended: ' + reduce(t = head(suspended), n IN tail(suspended) | t + ', ' + n) + '. '
     END AS off_text,
     CASE WHEN size(delayed) = 0 THEN ''
          ELSE 'Delays: ' + reduce(t = head(delayed), n IN tail(delayed) | t + ', ' + n) + '.'
     END AS late_text
RETURN label, suspended, late_names,
       CASE WHEN off_text + late_text = '' THEN 'All stations clear.'
            ELSE trim(off_text + late_text)
       END AS status
"""

DECIDE_QUERY = """
RETURN jev.decide($state, $questions, $options) AS result
"""

DECIDE_ALL_QUERY = """
CALL jev.decideAll($items, $questions, $options) YIELD id, answers, error_message
RETURN id, answers.route.choice AS choice, answers.route.probabilities AS probs, error_message
"""

STORE_QUERY = """
MATCH (a:Station {name: $origin}), (b:Station {name: $destination})
CREATE (d:Decision {id: randomUUID(), asked_at: datetime(), choice: $choice,
                    margin: $margin, probability: $probability, model: $model,
                    route: $route, state: $state_text})
CREATE (d)-[:FROM]->(a)
CREATE (d)-[:TO]->(b)
WITH d
OPTIONAL MATCH (s:Station) WHERE s.name IN $disrupted
FOREACH (x IN CASE WHEN s IS NULL THEN [] ELSE [s] END | CREATE (d)-[:CONCERNS]->(x))
RETURN DISTINCT d.id AS id
"""

LOG_QUERY = """
MATCH (d:Decision)-[:FROM]->(a:Station), (d)-[:TO]->(b:Station)
OPTIONAL MATCH (d)-[:CONCERNS]->(s:Station)
WITH d, a, b, collect(s.name) AS concerns
RETURN toString(datetime.truncate('second', d.asked_at)) AS asked,
       a.name AS origin, b.name AS destination, d.choice AS choice,
       d.margin AS margin, d.probability AS probability, d.model AS model, concerns
ORDER BY d.asked_at DESC
LIMIT $limit
"""

INVOLVED_QUERY = """
MATCH (d:Decision)-[:CONCERNS]->(s:Station)
RETURN s.name AS station, count(d) AS decisions
ORDER BY decisions DESC, station
LIMIT $limit
"""


# ---------- Neo4j ----------

class Graph:
    def __init__(self, cfg):
        self.cfg = cfg
        n = cfg["neo4j"]
        self.uri = os.environ.get(n["uri_env"])
        self.user = os.environ.get(n["username_env"])
        self.password = os.environ.get(n["password_env"])
        self.database = os.environ.get(n["database_env"])
        self.missing = [n[k] for k in ("uri_env", "username_env", "password_env", "database_env")
                        if not os.environ.get(n[k])]
        self.driver = None
        self.error = None
        if self.missing:
            self.error = f"Not set: {', '.join(self.missing)}."
            return
        wait = n.get("connect_timeout_s", 8)   # seconds. Without this a host that accepts the connection but never answers blocks for 60.
        try:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password),
                                               notifications_min_severity=NotificationMinimumSeverity.OFF,
                                               connection_timeout=wait, connection_acquisition_timeout=wait,
                                               liveness_check_timeout=30)   # test a connection that sat idle before using it
            self.driver.verify_connectivity()
        except Exception as exc:
            self.error = explain(exc, self.uri, self.database)
            self.close()

    @property
    def up(self):
        return self.driver is not None

    def close(self):
        if self.driver is not None:
            try:
                self.driver.close()
            except Exception:
                pass
        self.driver = None

    def lost(self, exc):
        """Call after a query failed. If Neo4j really can't be reached, stop using it: the app then carries on with
        Python and replay at once, instead of waiting for a timeout on every click. A stale connection is not an
        outage, and a fresh connection check tells the two apart. Returns True when the driver was dropped."""
        if self.driver is None or not isinstance(exc, (ServiceUnavailable, SessionExpired, ConnectionError, OSError)):
            return False
        try:
            self.driver.verify_connectivity()
            return False
        except Exception:
            self.error = explain(exc, self.uri, self.database)
            self.close()
            return True

    def _session(self):
        if self.driver is None:
            raise NotConnected()
        return self.driver.session(database=self.database)

    def options(self, mode):
        j = self.cfg["jev"]["hosted" if mode == "hosted" else "local"]
        return {k: v for k, v in j.items()}

    def apply_status(self, status):
        """Make the graph match status. Raises on a Neo4j error."""
        rows = [{"name": n, "active": v.get("active", True), "delay_minutes": v.get("delay", 0)}
                for n, v in clean_status(status).items()]
        with self._session() as s:
            s.run("MATCH (s:Station) WHERE s.active IS NOT NULL OR s.delay_minutes IS NOT NULL "
                  "REMOVE s.active, s.delay_minutes").consume()
            if rows:
                s.run("UNWIND $rows AS row MATCH (s:Station {name: row.name}) "
                      "SET s.active = row.active, s.delay_minutes = row.delay_minutes", rows=rows).consume()

    def propose(self, origin, dest, k, lines):
        """The graph proposes: project the network, ask GDS for a pool of short routes, keep the distinct ones.
        Raises on a Neo4j error."""
        r, name, lines = self.cfg["routes"], self.cfg["gds"]["graph_name"], (lines or None)
        with self._session() as s:
            ends = s.run("MATCH (n:Station) WHERE n.name IN [$o, $d] "
                         "AND EXISTS { (n)-[r:CONNECTS_TO]-() WHERE $lines IS NULL OR r.line IN $lines } "
                         "RETURN count(n) AS n", o=origin, d=dest, lines=lines).single()["n"]
            if ends < 2:   # an end isn't on these lines, so it isn't in the projection and GDS would fail
                return []
            s.run("CALL gds.graph.drop($name, false)", name=name).consume()
            s.run(PROJECT_QUERY, name=name, lines=lines).consume()
            found = [rec["stations"] for rec in s.run(
                YENS_QUERY, name=name, origin=origin, destination=dest, pool=r["pool"])]
            hops = {}
            if found:
                for rec in s.run(HOP_LINES_QUERY, routes=found, lines=lines):
                    hops.setdefault(rec["ri"], {})[rec["i"]] = rec["lines"]
        return build_routes(found, hops, k, r["interchange_cost"], r["max_overlap"])

    def route_status(self, routes):
        """Cypher reads the live status of every route from the graph. Returns {label: {status, suspended, late_names}}."""
        with self._session() as s:
            rows = [rec.data() for rec in s.run(
                STATUS_QUERY, routes=[{"label": r["label"], "stops": r["stations"]} for r in routes])]
        out = {row["label"]: row for row in rows}
        if any(r["label"] not in out for r in routes):   # the stations of a route aren't in the graph
            raise StationsMissing()
        return out

    def decide(self, state, questions, mode):
        """Ask Jev via jev.decide. Returns a normalized result dict. Raises on a Neo4j error."""
        t0 = time.perf_counter()
        with self._session() as s:
            rec = s.run(DECIDE_QUERY, state=state, questions=questions, options=self.options(mode)).single()
        result = rec["result"] if rec else None
        if not result:
            return {"error": "No result. Check that the stations are loaded."}
        if result.get("error_message"):
            return {"error": describe_error(result["error_message"])}
        ans = (result.get("answers") or {}).get("route") or {}
        try:
            probs = {str(k): float(v) for k, v in (ans.get("probabilities") or {}).items()}
        except (AttributeError, TypeError, ValueError):
            probs = {}
        if not ans.get("choice"):
            return {"error": "Jev didn't return an answer for the route question."}
        meta = result.get("metadata") or {}
        return {"choice": ans["choice"], "probs": probs, "margin": margin_of(probs),
                "latency_ms": meta.get("latency_ms", round((time.perf_counter() - t0) * 1000)),
                "model": meta.get("model"), "source": "live", "error": None}

    def decide_all(self, items, questions, mode):
        """Ask Jev about many states in one jev.decideAll call. Returns a list of dicts (id, choice, probs, error),
        in the order of items. Raises on a Neo4j error."""
        options = {**self.options(mode), "concurrency": 4 if mode == "hosted" else 1}
        with self._session() as s:
            records = [r.data() for r in s.run(DECIDE_ALL_QUERY, items=items, questions=questions, options=options)]
        by_id = {r["id"]: r for r in records}
        rows = []
        for it in items:
            r = by_id.get(it["id"], {})
            err = r.get("error_message") or (None if r else "No result")
            rows.append({"id": it["id"], "choice": r.get("choice"), "probs": r.get("probs") or {},
                         "error": describe_error(err) if err else None})
        return rows

    def remember(self, origin, dest, routes, result, state, info):
        """The graph remembers: store the decision as a node linked to the stations that were disrupted. Returns its id."""
        chosen = next((r for r in routes if r["label"] == result["choice"]), None)
        disrupted = sorted({n for v in info.values() for n in v["suspended"] + v["late_names"]})
        with self._session() as s:
            rec = s.run(STORE_QUERY, origin=origin, destination=dest, choice=result["choice"],
                        margin=result.get("margin"), probability=(result.get("probs") or {}).get(result["choice"]),
                        model=result.get("model"), route=chosen["stations"] if chosen else [],
                        state_text=json.dumps(state), disrupted=disrupted).single()
        if rec is None:   # the MATCH found no Station nodes for the two ends
            raise StationsMissing()
        return rec["id"]

    def log(self, limit=50):
        """Stored decisions, newest first, and how often each station was involved. Raises on a Neo4j error."""
        with self._session() as s:
            rows = [r.data() for r in s.run(LOG_QUERY, limit=limit)]
            involved = [r.data() for r in s.run(INVOLVED_QUERY, limit=15)]
        return rows, involved

    def clear_log(self):
        with self._session() as s:
            s.run("MATCH (d:Decision) DETACH DELETE d").consume()

    def preflight(self, journey, modes=("hosted", "local")):
        """List of (label, ok, detail). journey: dict with origin, destination, lines, k."""
        out = []
        if not self.up:
            return [("Neo4j", False, self.error or "Not connected.")]
        out.append(("Neo4j", True, f"connected to {self.uri}"))
        try:
            with self._session() as s:
                n = s.run("MATCH (s:Station) RETURN count(s) AS n").single()["n"]
            out.append(("Stations", n > 0, f"{n} loaded" if n else "none loaded (run Demo 2 section 5)"))
            if not n:
                return out
            routes = self.propose(journey["origin"], journey["destination"], journey["k"], journey["lines"])
            out.append(("GDS routes", len(routes) > 1,
                        f"{len(routes)} distinct routes proposed" if len(routes) > 1 else "fewer than two routes found"))
            info = self.route_status(routes)
        except Exception as exc:
            out.append(("Graph", False, explain(exc, self.uri, self.database)))
            return out
        if len(routes) < 2:
            return out
        state = build_state(journey["origin"], journey["destination"], journey["lines"], routes, info)
        questions = build_questions(journey["origin"], journey["destination"], routes)
        for m in modes:
            try:
                r = self.decide(state, questions, m)
                out.append((f"Jev {m}", not r.get("error"), r.get("error") or f"{r['model']}, {r['latency_ms']} ms"))
            except Exception as exc:
                out.append((f"Jev {m}", False, explain(exc, self.uri, self.database)))
        return out
