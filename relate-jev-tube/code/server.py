"""MCP server: the graph-walking agent's tools over a Relate database.

Run it from an MCP client (see README.md). It speaks MCP over stdio.

Environment:
  WALK_DB           path to the .cl file made by load_data.py         (default walk.cl)
  PICKER            "jev" (hosted, needs TYPESAFE_API_KEY) or "stub"  (default jev)
  LOW_MARGIN        margin below which a person must choose           (default 0.2)
  MAX_STEPS         hops allowed in one walk                          (default 30)
  MAX_VISITS        visits to one station after which an agent's move is refused (default 2)
  OPERATOR_TOOLS    set to 1 to also expose the operator tools that suspend and delay stations
"""

import asyncio
import logging
import functools
import os
import sys

from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel, Field, create_model
from typing import Annotated, Literal

try:
    from mcp.server.mcpserver import Context, Elicit, MCPServer, Resolve
    from mcp.server.elicitation import ElicitationResult
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:
    raise SystemExit("This server needs mcp 2.x (ask_human uses its input-required mechanism). Run: pip install -U 'mcp>=2.0'")

import walker

os.environ.setdefault("TYPESAFE_LOG_LEVEL", "warn")        # keep the hosted-Jev request lines out of the console
for _name in ("httpx", "httpx2", "httpcore", "httpcore2", "typesafe_sdk"):   # the Jev client uses httpx2
    logging.getLogger(_name).setLevel(logging.WARNING)
    # a filter holds even if a library raises the level again later
    logging.getLogger(_name).addFilter(lambda record: record.levelno >= logging.WARNING)

DB_PATH = os.environ.get("WALK_DB", "walk.cl")
OPERATOR_TOOLS = os.environ.get("OPERATOR_TOOLS") == "1"

INSTRUCTIONS = """\
You are walking a passenger across the London Underground one hop at a time, from a start station to a goal station.
You cannot see the map. You only ever know your legal moves, and only next_hop lists them.

Loop until the goal is reached:
1. next_hop: read the legal moves and their live status.
2. judge_hop: Jev scores the moves and the decision is written to the ledger.
3. If needs_human is true, call ask_human. The person's choice is made as the move, so you are moved there:
   do not call move afterward, continue with next_hop. Otherwise call move with a legal station, normally the top pick.
   Call ask_human for the passenger's constraint only when every legal move breaks it: give the reason, and it is
   stored in the ledger. If any legal move meets the constraint, choose it yourself with move (when the margin allows)
   and say why.
   A move meets a constraint to avoid a line if at least one of the lines listed for it is not that line, because the passenger can ride that other line. Judge every move this way, every time.
Never invent a station. move refuses anything next_hop did not offer.
Base every reason (including one you give to ask_human) only on what next_hop and judge_hop returned (lines, status, probabilities). Say "probability", not "reliability score", and do not call a move the only option unless next_hop lists just one. A probability is Jev's chance that a station is the best next move, not a chance of reaching the goal.
Use ledger_report afterward to see where Jev was unsure."""

mcp = MCPServer("walking-agent", instructions=INSTRUCTIONS)

_db = None
_walk = None

# Relate connections belong to the thread that opened them. Every tool runs its database work on this one thread,
# so the connection is always used where it was made, whichever thread the MCP server calls a tool from.
_db_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="relate")

def db():
    global _db
    if _db is None:
        import relate
        if DB_PATH != ":memory:" and not os.path.exists(DB_PATH):
            raise RuntimeError(f"{DB_PATH} not found. Run load_data.py first to build it from the Tube CSVs.")
        _db = relate.connect(DB_PATH)
        n = walker.records(_db.execute("MATCH (s:Station) RETURN count(s) AS n"))[0]["n"]
        if not n:
            raise RuntimeError(f"{DB_PATH} has no stations. Run load_data.py first.")
    return _db

def close_db():
    global _db
    if _db is not None:
        try:
            _db.close()
        except Exception:
            pass
        _db = None

def need_walk():
    if _walk is None:
        raise RuntimeError("No walk in progress. Call start_walk first.")
    return _walk

def as_error(exc):
    return {"error": str(exc) if isinstance(exc, (ValueError, RuntimeError)) else f"{type(exc).__name__}: {exc}"}

async def on_db_thread(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(_db_thread, functools.partial(fn, *args))

def dbtool(fn):
    """Run a tool's database work on the Relate thread, and turn an exception into an {'error': ...} result."""
    @functools.wraps(fn)
    async def run(*args, **kwargs):
        try:
            return await on_db_thread(functools.partial(fn, *args, **kwargs))
        except Exception as exc:
            return as_error(exc)
    return run

@mcp.tool()
@dbtool
def start_walk(origin: str = "Kennington", goal: str = "Euston", agent: str = "") -> dict:
    """Start a new walk from a station to a goal station. Replaces any walk in progress.
    agent is a label for whoever is driving (for example the model name); it is stored on every step.
    The ledger keeps every earlier walk."""
    global _walk
    _walk = walker.Walker(db(), origin, goal, walker.get_picker(), agent=agent)
    return {"started": True, **_walk.where_am_i()}

@mcp.tool()
@dbtool
def where_am_i() -> dict:
    """Where the walk is: the current station, the goal, hops taken and left, stations visited, and whether the goal
    has been reached."""
    return need_walk().where_am_i()

@mcp.tool()
@dbtool
def next_hop() -> dict:
    """The legal moves from the current station. This is the only list of legal moves. For each: the connecting
    lines, stops left to the goal, the station's live status (suspended or delayed), any trouble one hop further on,
    and whether it was already visited."""
    w = need_walk()
    return {"at": w.here, "goal": w.goal, "moves": w.next_hop()}

@mcp.tool()
@dbtool
def judge_hop() -> dict:
    """Ask Jev to score the legal moves from here and write the decision to the ledger. Returns Jev's top pick, a
    probability for every move, the margin (top probability minus the second), and needs_human, which is true when
    the margin is below the threshold. Calling it twice before moving returns the same judgment."""
    return need_walk().judge_hop()

@mcp.tool()
@dbtool
def move(station: str, reason: str = "") -> dict:
    """Move to one of the legal stations from next_hop, after judge_hop. Refuses any other station, and refuses when
    needs_human was true (call ask_human then). Give a one-sentence reason: it is stored in the ledger, which is
    how a choice that differs from Jev's top pick can be explained later."""
    return need_walk().move(station, decided_by="agent", reason=reason)

def prepare_question(reason=""):
    """What the person is asked: the message and the answer schema, built from the pending judgment."""
    w = need_walk()
    shown = w.human_options()
    if shown is None:
        raise RuntimeError("Call judge_hop before ask_human.")
    names = [name for name, _ in shown]
    answer = create_model("NextStation", station=(Literal[tuple(names)], Field(description="The station to go to next")))
    lines = "\n".join(f"- {name}: {p:.2f}" for name, p in shown)
    if w.pending["margin"] < w.low_margin:
        why = f"Jev is unsure (margin {w.pending['margin']:.2f})."
    else:
        why = f"The agent is asking for your choice (Jev's margin is {w.pending['margin']:.2f})."
    if reason.strip():
        why += f" The agent says: {reason.strip()}"
    message = f"The agent is at {w.here}, heading for {w.goal}. {why} Where should it go next?\n{lines}"
    return message, answer

ASK_HUMAN_DOC = (
    """Ask the person which station to go to next. Use it when judge_hop says needs_human, or when every legal move breaks
    the passenger's constraint. If some legal move meets the constraint, use move instead. Give a one-sentence reason: the
    person sees it and it is stored in the ledger. The person sees Jev's probabilities and chooses; the move is made and
    recorded as a human decision, so you are moved there and must not call move afterward. Needs a client that can ask the
    person a question (MCP elicitation)."""
)

async def the_question(reason: str = ""):
    """Resolver: what the person is asked. The framework sends it the way the negotiated protocol allows (a question
    mid-call for older clients, an input-required result for newer ones), and may run this again on each round."""
    try:
        message, answer_model = await on_db_thread(prepare_question, reason)
    except Exception as exc:
        raise ToolError(str(exc)) from exc   # a ToolError reaches the model as this text; anything else becomes a bare "Error executing tool"
    return Elicit(message, answer_model)

@mcp.tool(description=ASK_HUMAN_DOC)
async def ask_human(answer: Annotated[ElicitationResult[BaseModel], Resolve(the_question)], reason: str = "") -> dict:
    if answer.action != "accept":
        return {"ok": False, "error": f"The person chose not to answer ({answer.action}). Nothing was moved."}
    try:
        return await on_db_thread(need_walk().record_human_choice, answer.data.station, reason)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

@mcp.tool()
@dbtool
def ledger_report(
    kind: Literal["shaky", "trouble", "runner_ups", "humans", "overrides", "path", "walks", "agents", "agent_overrides"],
    low_margin: float | None = None,
) -> dict:
    """Run one fixed, read-only report over the ledger of every walk. There is no free-form query.
    shaky: decisions with a low margin. trouble: stations ranked by how many shaky decisions happened there.
    runner_ups: options Jev gave real weight to but that were not chosen. humans: steps a person decided.
    overrides: steps where the choice differed from Jev's top pick, with the agent and its reason. path: every hop taken.
    walks: one row per walk. agents: hops and lowest margin per agent. agent_overrides: how often each agent overrode Jev.
    low_margin overrides the threshold used by shaky and trouble."""
    return {"kind": kind, "rows": walker.ledger_report(db(), kind, low=low_margin)}

if OPERATOR_TOOLS:
    @mcp.tool()
    @dbtool
    def set_station_status(station: str, state: Literal["suspended", "delayed", "clear"], minutes: int = 5) -> dict:
        """OPERATOR ONLY. Suspend a station, delay it by some minutes, or clear it. Simulated incidents."""
        walker.set_status(db(), station, state, minutes)
        return {"ok": True, "station": station, "state": state, "minutes": minutes if state == "delayed" else 0}

    @mcp.tool()
    @dbtool
    def clear_all_status() -> dict:
        """OPERATOR ONLY. Return every station to normal running."""
        walker.clear_all(db())
        return {"ok": True}

    @mcp.tool()
    @dbtool
    def list_disruptions() -> dict:
        """OPERATOR ONLY. Stations that are suspended or delayed right now."""
        return {"disruptions": [{"station": s, "suspended": off, "delay_minutes": late}
                                for s, (off, late) in sorted(walker.station_status(db()).items())]}

if __name__ == "__main__":
    try:
        mcp.run()
    finally:
        try:
            _db_thread.submit(close_db).result(timeout=5)
        except Exception:
            pass
        # Leave without Python's normal shutdown: Relate's connection panics if it is cleaned up during interpreter exit.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
