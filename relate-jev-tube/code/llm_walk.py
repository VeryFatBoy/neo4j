"""Let an LLM drive the walk. The LLM is the agent; the MCP server holds the tools, Jev scores each hop, Relate keeps the ledger.

  python llm_walk.py --llm anthropic:claude-sonnet-5-5
  python llm_walk.py --llm ollama:bonsai-2:9b
  python llm_walk.py --llm openai:gpt-4o --base-url https://api.openai.com/v1
  python llm_walk.py --llm ollama:bonsai-2:9b --constraint "avoid the Northern line" --suspend "Green Park"

--llm is provider:model (see llm.py; adding a provider takes one small class). Keys come from the environment:
ANTHROPIC_API_KEY for anthropic, OPENAI_API_KEY for openai, none for ollama. TYPESAFE_API_KEY is for Jev.
When a hop's margin is low, the server asks you at the keyboard.
"""

import argparse
import asyncio
import json
import mcp.types as types
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pathlib import Path
from llm import make_llm

HERE = Path(__file__).parent
AGENT_TOOLS = {"where_am_i", "next_hop", "judge_hop", "move", "ask_human", "ledger_report"}

EXTRA_RULES = """\
The walk has already started. Give a one-sentence reason with every move; it is stored in the ledger.
Jev scores each hop for reliability only. It does not know the passenger's constraints. If a constraint means a
different legal station is better than Jev's top pick, you may choose it, as long as the margin lets you move
yourself, and say why in the reason. When the margin is low, call ask_human; the person's choice is made as the
move, so do not call move afterward. Call ask_human for a constraint only when every legal move breaks it, and give the
reason. If any legal move meets the constraint, choose it yourself with move and say why.
A move meets a constraint to avoid a line if at least one of the lines listed for it is not that line, because the passenger can ride that other line. Judge every move this way, every time.
Base every reason (including one you give to ask_human) only on what next_hop and judge_hop returned (lines, status, probabilities). Say "probability", not "reliability score", and do not call a move the only option unless next_hop lists just one. A probability is Jev's chance that a station is the best next move, not a chance of reaching the goal.
Stop as soon as the goal is reached."""

async def answer_the_person(context, params):
    print("\n" + params.message)
    options = params.requested_schema["properties"]["station"]["enum"]
    while True:
        choice = input("Type the station to go to: ").strip()
        if choice in options:
            return types.ElicitResult(action="accept", content={"station": choice})
        print(f"Choose one of: {', '.join(options)}")

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", default=os.environ.get("LLM"), help="provider:model, for example ollama:bonsai-2:9b")
    ap.add_argument("--base-url", help="API address, for a server other than the provider's default")
    ap.add_argument("--origin", default="Kennington")
    ap.add_argument("--goal", default="Euston")
    ap.add_argument("--constraint", help='what the passenger wants, in words, for example "avoid the Northern line"')
    ap.add_argument("--suspend", help="suspend this station after the first hop")
    ap.add_argument("--stub", action="store_true", help="score hops with the offline stub instead of hosted Jev")
    ap.add_argument("--max-turns", type=int, default=80)
    args = ap.parse_args()
    if not args.llm:
        sys.exit("Choose a model with --llm provider:model, for example --llm anthropic:claude-sonnet-5-5")
    llm = make_llm(args.llm, base_url=args.base_url)

    env = {**os.environ, "WALK_DB": os.environ.get("WALK_DB", str(HERE / "walk.cl"))}
    if args.stub:
        env["PICKER"] = "stub"
    if args.suspend:
        env["OPERATOR_TOOLS"] = "1"
    server = StdioServerParameters(command=sys.executable, args=[str(HERE / "server.py")], env=env)

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write, elicitation_callback=answer_the_person) as session:
            init = await session.initialize()

            async def call(tool, arguments=None):
                result = await session.call_tool(tool, arguments or {})
                text = result.content[0].text if result.content else "{}"
                return text

            started = json.loads(await call("start_walk", {"origin": args.origin, "goal": args.goal, "agent": llm.label}))
            walk_id = started["walk"]
            print(f"Walk {walk_id}: {args.origin} to {args.goal}, driven by {llm.label}\n")

            tools = [{"name": t.name, "description": t.description or "", "parameters": getattr(t, "inputSchema", None) or t.input_schema}   # 1.x and 2.x spell it differently
                     for t in (await session.list_tools()).tools if t.name in AGENT_TOOLS]
            system = (init.instructions or "") + "\n\n" + EXTRA_RULES
            task = f"Walk the passenger from {args.origin} to {args.goal}."
            if args.constraint:
                task += f" The passenger's constraint: {args.constraint}."
            messages = [{"role": "user", "content": task}]

            reached = False
            try:
                for _ in range(args.max_turns):
                    reply = await asyncio.to_thread(llm.chat, system, messages, tools)
                    messages.append({"role": "assistant", "content": reply.text, "tool_calls": reply.tool_calls})
                    if reply.text:
                        print(f"[{llm.label}] {reply.text.strip()}")
                    if not reply.tool_calls:
                        break
                    for c in reply.tool_calls:
                        text = await call(c.name, c.arguments)
                        messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name, "content": text})
                        try:
                            out = json.loads(text) if text.startswith("{") else {}
                        except ValueError:
                            out = {}
                        if c.name == "judge_hop" and "margin" in out:
                            print(f"  judge_hop: Jev favors {out['top']} (margin {out['margin']:.2f})"
                                  f"{'  -> needs a person' if out['needs_human'] else ''}")
                        elif c.name in ("move", "ask_human"):
                            if out.get("ok"):
                                why = f"  ({c.arguments.get('reason')})" if c.arguments.get("reason") else ""
                                print(f"  {c.name}: -> {out['at']}{why}")
                            else:
                                print(f"  {c.name}: refused. {out.get('error') or text[:300]}")
                            if out.get("ok") and out.get("steps_taken") == 1 and args.suspend:
                                await call("set_station_status", {"station": args.suspend, "state": "suspended"})
                                print(f"  (Operator: {args.suspend} suspended)")
                            reached = reached or bool(out.get("reached_goal"))
                        elif out.get("error") or not out:   # a tool error: the model sees it, so show it here too
                            print(f"  {c.name}: {out.get('error') or text[:300]}")
                        else:
                            print(f"  {c.name}")
                    if reached:
                        break
            finally:   # leave the stations running normally even if the run failed part way
                if args.suspend:
                    try:
                        await call("clear_all_status")
                    except Exception:
                        pass

            print("\nReached the goal." if reached else "\nStopped before the goal.")
            path = json.loads(await call("ledger_report", {"kind": "path"}))["rows"]
            print(f"\nLedger, this walk ({sum(1 for r in path if r['walk'] == walk_id)} hops):")
            for r in path:
                if r["walk"] == walk_id:
                    why = f'  "{r["reason"]}"' if r.get("reason") else ""
                    lines = f" [{r['lines']}]" if r.get("lines") else ""
                    print(f"  {r['step']}: {r['at']} -> {r['chose']}{lines}  margin {r['margin']:.2f}  {r['decided_by']}{why}")
            for kind, title in (("overrides", "Overrides of Jev's top pick (all walks)"), ("agents", "By agent (all walks)")):
                rows = json.loads(await call("ledger_report", {"kind": kind}))["rows"]
                print(f"\n{title}: {len(rows)} row(s)")
                for r in rows:
                    print("  ", r)

asyncio.run(main())
