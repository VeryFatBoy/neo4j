"""The walking agent's tools, over a Relate graph of the London Underground.

Plain Python and Cypher, no MCP in here. server.py wraps these as MCP tools and the notebook uses the same logic.

The graph holds the Tube (Station nodes, CONNECTS_TO relationships) and the ledger (Step nodes). A Step records one
decision: where the agent was (AT), every option it considered with Jev's probability (CONSIDERED), what was chosen
(CHOSE), who chose (agent or human) and the margin between Jev's top two options.
"""

import math
import os
import uuid

from collections import defaultdict, deque
from datetime import datetime, timezone
from difflib import get_close_matches

LOW_MARGIN = float(os.environ.get("LOW_MARGIN", "0.2"))      # below this, a person must choose
MAX_STEPS = int(os.environ.get("MAX_STEPS", "30"))           # hops allowed in one walk
MAX_OPTIONS = int(os.environ.get("MAX_OPTIONS", "6"))        # most moves next_hop offers at one station
MAX_VISITS = int(os.environ.get("MAX_VISITS", "2"))          # an agent may not move onto a station already visited this many times
HOSTED_MODEL = os.environ.get("JEV_MODEL", "jev-1.13.0")

def records(result):
    """Rows as a list of dicts. Uses .data when the result has it, otherwise reads the cursor."""
    if hasattr(result, "data"):
        data = result.data
        return data() if callable(data) else data
    return [dict(row) for row in result]

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# operator functions: change the live status of a station (not given to the agent)

def set_status(db, station, state, minutes=5):
    """state is 'suspended', 'delayed' or 'clear'."""
    if state not in ("suspended", "delayed", "clear"):
        raise ValueError("state must be 'suspended', 'delayed' or 'clear'")
    active, delay = {"suspended": (False, 0), "delayed": (True, minutes), "clear": (True, 0)}[state]
    found = records(db.execute("MATCH (s:Station {name: $name}) RETURN s.name AS name", {"name": station}))
    if not found:
        raise ValueError(unknown_station(db, station))
    db.execute("MATCH (s:Station {name: $name}) SET s.active = $active, s.delay_minutes = $delay",
               {"name": station, "active": active, "delay": delay})

def clear_all(db):
    db.execute("MATCH (s:Station) SET s.active = true, s.delay_minutes = 0")

def station_status(db):
    """{station: (suspended, delay_minutes)} for every station that is not running normally."""
    rows = records(db.execute(
        "MATCH (s:Station) WHERE s.active = false OR s.delay_minutes > 0 "
        "RETURN s.name AS name, s.active AS active, s.delay_minutes AS delay"))
    return {r["name"]: (r["active"] is False, r["delay"] or 0) for r in rows}

def unknown_station(db, name):
    names = [r["name"] for r in records(db.execute("MATCH (s:Station) RETURN s.name AS name"))]
    close = get_close_matches(name, names, n=3)
    return f"Unknown station '{name}'." + (f" Did you mean: {', '.join(close)}?" if close else "")

# the ledger

REPORTS = {
    "shaky": (
        "MATCH (s:Step)-[:AT]->(st:Station) WHERE s.margin < $low AND s.chose IS NOT NULL "
        "RETURN s.walk AS walk, s.n AS step, st.name AS station, s.top AS top_pick, "
        "s.chose AS chose, s.margin AS margin, s.decided_by AS decided_by ORDER BY s.margin"),
    "trouble": (
        "MATCH (s:Step)-[:AT]->(st:Station) WHERE s.margin < $low AND s.chose IS NOT NULL "
        "RETURN st.name AS station, count(s) AS shaky_steps ORDER BY shaky_steps DESC, station"),
    "runner_ups": (
        "MATCH (s:Step)-[c:CONSIDERED]->(o:Station) "
        "WHERE c.probability > $runner_up AND o.name <> s.chose "
        "RETURN s.walk AS walk, s.n AS step, o.name AS runner_up, s.chose AS chose, "
        "c.probability AS probability ORDER BY c.probability DESC"),
    "humans": (
        "MATCH (s:Step)-[:AT]->(st:Station) WHERE s.decided_by = 'human' "
        "RETURN s.walk AS walk, s.n AS step, st.name AS station, s.top AS top_pick, "
        "s.chose AS chose, s.margin AS margin"),
    "overrides": (
        "MATCH (s:Step)-[:AT]->(st:Station) WHERE s.chose IS NOT NULL AND s.chose <> s.top "
        "RETURN s.walk AS walk, s.n AS step, st.name AS station, s.top AS top_pick, "
        "s.chose AS chose, s.lines AS lines, s.margin AS margin, s.decided_by AS decided_by, s.agent AS agent, "
        "s.reason AS reason ORDER BY s.walk, s.n"),
    "path": (
        "MATCH (s:Step)-[:AT]->(st:Station) WHERE s.chose IS NOT NULL "
        "RETURN s.walk AS walk, s.n AS step, st.name AS at, s.chose AS chose, s.lines AS lines, "
        "s.margin AS margin, s.decided_by AS decided_by, s.reason AS reason ORDER BY s.walk, s.n"),
    "walks": (
        "MATCH (s:Step) WHERE s.chose IS NOT NULL "
        "RETURN s.walk AS walk, s.agent AS agent, count(s) AS hops, min(s.margin) AS lowest_margin ORDER BY walk"),
    "agents": (
        "MATCH (s:Step) WHERE s.chose IS NOT NULL "
        "RETURN s.agent AS agent, count(s) AS hops, min(s.margin) AS lowest_margin ORDER BY agent"),
    "agent_overrides": (
        "MATCH (s:Step) WHERE s.chose IS NOT NULL AND s.chose <> s.top AND s.decided_by = 'agent' "
        "RETURN s.agent AS agent, count(s) AS overrides ORDER BY overrides DESC"),
}

def ledger_report(db, kind, low=None, runner_up=0.3):
    """Run one of the fixed read-only reports. Returns a list of dicts."""
    if kind not in REPORTS:
        raise ValueError(f"Unknown report '{kind}'. Choose from: {', '.join(REPORTS)}.")
    return records(db.execute(REPORTS[kind], {"low": LOW_MARGIN if low is None else low, "runner_up": runner_up}))

# pickers: state + question + options in, a probability per station out

def stub_picker(state, questions, options):
    """A plain scoring rule, so everything runs offline. Not Jev; its margins say nothing about Jev's."""
    scores = {}
    for o in options:
        s = -1.5 * o["d_goal"]
        if o["suspended"]:
            s -= 4.0
        s -= 0.3 * o["delay"]
        s -= 1.5 * len(o["ahead_suspended"]) + 0.2 * sum(m for _, m in o["ahead_delayed"])
        if o["visited"]:
            s -= 2.0
        scores[o["station"]] = s
    top = max(scores.values())
    exp = {k: math.exp(v - top) for k, v in scores.items()}
    total = sum(exp.values())
    return {k: v / total for k, v in exp.items()}

_client = None

def jev_picker(state, questions, options):
    """Ask the hosted Jev which station to go to next. Returns {station: probability}."""
    global _client
    from typesafe_sdk import Choice, TypeSafeClient
    if _client is None:
        if not os.environ.get("TYPESAFE_API_KEY"):
            raise RuntimeError("TYPESAFE_API_KEY isn't set. Set it in the server's environment, or set PICKER=stub.")
        _client = TypeSafeClient(api_key=os.environ["TYPESAFE_API_KEY"], model=HOSTED_MODEL, timeout=5.0)
    q = questions["hop"]
    response = _client.system_one(
        state=state,
        questions={"hop": Choice(instructions=q["instructions"], criteria=q["criteria"])},
    )
    probabilities = getattr(response.choices["hop"], "probabilities", None)
    if not probabilities:
        raise RuntimeError("Jev returned no probabilities, so there is no margin to judge the hop by.")
    return {k: float(v) for k, v in dict(probabilities).items()}

def get_picker(kind=None):
    kind = kind or os.environ.get("PICKER", "jev")
    if kind not in ("jev", "stub"):
        raise ValueError("PICKER must be 'jev' or 'stub'")
    return jev_picker if kind == "jev" else stub_picker

# the walk

class Walker:
    """One walk from origin to goal. Each method is one tool and runs fixed statements; there is no free Cypher."""

    def __init__(self, db, origin, goal, picker, agent="", low_margin=None, max_steps=None, max_options=None):
        self.db, self.goal, self.picker, self.agent = db, goal, picker, agent
        self.low_margin = LOW_MARGIN if low_margin is None else low_margin
        self.max_steps = MAX_STEPS if max_steps is None else max_steps
        self.max_options = MAX_OPTIONS if max_options is None else max_options
        self.walk_id = uuid.uuid4().hex[:8]
        self.here, self.visited, self.n, self.pending = origin, [origin], 0, None
        edges = records(db.execute("MATCH (a:Station)-[r:CONNECTS_TO]->(b:Station) RETURN a.name AS a, b.name AS b"))
        self.adj = defaultdict(set)
        for e in edges:
            self.adj[e["a"]].add(e["b"])
            self.adj[e["b"]].add(e["a"])
        for name in (origin, goal):
            if name not in self.adj:
                raise ValueError(unknown_station(db, name))
        self.dist = self._distances(goal)   # worked out once per walk, not on every hop
        if origin not in self.dist:
            raise ValueError(f"There is no route from {origin} to {goal}.")

    def _distances(self, goal):
        dist, queue = {goal: 0}, deque([goal])
        while queue:
            x = queue.popleft()
            for y in self.adj[x]:
                if y not in dist:
                    dist[y] = dist[x] + 1
                    queue.append(y)
        return dist

    @property
    def finished(self):
        return self.here == self.goal

    # tool: where_am_i
    def where_am_i(self):
        return {"walk": self.walk_id, "station": self.here, "goal": self.goal, "steps_taken": self.n,
                "steps_left": self.max_steps - self.n, "visited": list(self.visited),
                "reached_goal": self.finished, "awaiting_move": self.pending is not None}

    # tool: next_hop
    def next_hop(self):
        """The legal moves from here, with live status. The only list of legal moves there is."""
        if self.finished:
            return []
        rows = records(self.db.execute(
            "MATCH (a:Station {name: $here})-[r:CONNECTS_TO]-(b:Station) "
            "RETURN b.name AS station, collect(DISTINCT r.line) AS lines",
            {"here": self.here}))
        status = station_status(self.db)
        options = []
        for r in rows:
            st = r["station"]
            if st not in self.dist:
                continue
            off, late = status.get(st, (False, 0))
            ahead = [c for c in self.adj[st] if self.dist.get(c, 99) < self.dist[st] and c in status]
            options.append({
                "station": st, "lines": sorted(r["lines"]), "d_goal": self.dist[st],
                "suspended": off, "delay": late,
                "ahead_suspended": sorted(c for c in ahead if status[c][0]),
                "ahead_delayed": sorted((c, status[c][1]) for c in ahead if not status[c][0] and status[c][1] > 0),
                "visited": st in self.visited,
            })
        options.sort(key=lambda o: (o["d_goal"], o["station"]))
        options = options[:self.max_options]
        for o in options:
            o["description"] = self._describe(o)
        return options

    def _describe(self, o):
        lines = ", ".join(o["lines"])
        status = "suspended" if o["suspended"] else (f"delayed {o['delay']} min" if o["delay"] else "clear")
        text = f"{o['station']} ({lines} line). {o['d_goal']} stops from {self.goal}. Status: {status}."
        if o["ahead_suspended"]:
            text += f" Further on: {', '.join(o['ahead_suspended'])} suspended."
        if o["ahead_delayed"]:
            text += " Further on: " + ", ".join(f"{c} delayed {m} min" for c, m in o["ahead_delayed"]) + "."
        if o["visited"]:
            text += " Already visited."
        return text

    # tool: judge_hop
    def judge_hop(self):
        """Ask the picker (Jev) to score the legal moves and write the Step to the ledger.
        Calling it again before moving returns the same judgment instead of asking again."""
        if self.finished:
            return {"error": f"Already at {self.goal}."}
        if self.n >= self.max_steps:
            return {"error": f"Step limit reached ({self.max_steps} hops)."}
        if self.pending:
            return self.pending["result"]
        options = self.next_hop()
        if not options:
            return {"error": f"No legal moves from {self.here}."}
        state = {"current_station": self.here, "destination": self.goal,
                 "steps_taken": self.n, "stations_visited": ", ".join(self.visited)}
        questions = {"hop": {
            "type": "choice",
            "instructions": (f"The passenger is at {self.here} and wants to reach {self.goal} as reliably as possible. "
                             "Which station should they go to next?"),
            "criteria": {o["station"]: o["description"] for o in options}}}
        probs = self.picker(state, questions, options)
        ranked = sorted(probs.items(), key=lambda kv: -kv[1])
        top, p1 = ranked[0]
        margin = p1 - (ranked[1][1] if len(ranked) > 1 else 0.0)
        step_id = f"{self.walk_id}-{self.n}"
        self.db.execute(
            "MATCH (st:Station {name: $here}) "
            "CREATE (s:Step {id: $id, walk: $walk, agent: $agent, n: $n, top: $top, probability: $p, margin: $margin, "
            "decided_by: 'pending', created_at: $ts})-[:AT]->(st)",
            {"here": self.here, "id": step_id, "walk": self.walk_id, "agent": self.agent, "n": self.n, "top": top,
             "p": round(p1, 4), "margin": round(margin, 4), "ts": now()})
        self.db.execute(
            "UNWIND $rows AS r "
            "MATCH (s:Step {id: $id}), (b:Station {name: r.name}) "
            "CREATE (s)-[:CONSIDERED {probability: r.p}]->(b)",
            {"id": step_id, "rows": [{"name": k, "p": round(v, 4)} for k, v in probs.items()]})
        needs_human = margin < self.low_margin
        result = {"top": top, "probability": round(p1, 3), "margin": round(margin, 3), "needs_human": needs_human,
                  "probabilities": {k: round(v, 3) for k, v in ranked},
                  "next": "Call ask_human: the margin is below the threshold." if needs_human
                          else "Call move with a legal station (normally the top pick)."}
        self.pending = {"id": step_id, "top": top, "probs": probs, "margin": margin,
                        "options": options, "result": result}
        return result

    # tool: move
    def move(self, station, decided_by="agent", reason=""):
        """Move to a legal station, and record why. Refuses anything next_hop did not offer, and refuses an agent's move when the
        margin was too low (a person must choose then)."""
        served = {o["station"]: o["lines"] for o in self.next_hop()}
        legal = list(served)
        if station not in legal:
            return {"ok": False,
                    "error": f"'{station}' is not a legal move from {self.here}. Legal moves: {', '.join(legal) or 'none'}."}
        if self.pending is None:
            return {"ok": False, "error": "Call judge_hop before move."}
        if decided_by == "agent" and self.pending["margin"] < self.low_margin:
            return {"ok": False,
                    "error": (f"The margin is {self.pending['margin']:.2f}, below {self.low_margin}. "
                              "Call ask_human so a person chooses.")}
        if decided_by == "agent" and self.visited.count(station) >= MAX_VISITS:
            return {"ok": False,
                    "error": (f"{station} has already been visited {self.visited.count(station)} times, which looks like a loop. "
                              "Call ask_human so a person chooses, and say in the reason that the walk is going in circles.")}
        self.db.execute(
            "MATCH (s:Step {id: $id}), (b:Station {name: $to}) "
            "SET s.decided_by = $by, s.chose = $to, s.reason = $reason, s.lines = $lines "
            "CREATE (s)-[:CHOSE]->(b)",
            {"id": self.pending["id"], "to": station, "by": decided_by, "reason": reason,
             "lines": ", ".join(served[station])})
        self.here, self.pending = station, None
        self.visited.append(station)
        self.n += 1
        result = {"ok": True, "at": station, "reached_goal": self.finished, "steps_taken": self.n}
        if decided_by == "human":
            result["note"] = (f"A person chose {station} and the walk has moved there. Do not call move. "
                              "Continue with next_hop.")
        return result

    # the person's side of ask_human
    def human_options(self):
        """What a person is shown when the margin is low: the stations and Jev's probability for each."""
        if self.pending is None:
            return None
        return [(o["station"], self.pending["probs"].get(o["station"], 0.0)) for o in self.pending["options"]]

    def record_human_choice(self, station, asked_because=""):
        """Record the person's choice as the move. asked_because is the agent's reason for asking, kept in the ledger."""
        if self.pending is None:
            return {"ok": False, "error": "Call judge_hop before ask_human."}
        reason = f"chosen by a person (asked: {asked_because.strip()})" if asked_because.strip() else "chosen by a person"
        return self.move(station, decided_by="human", reason=reason)
