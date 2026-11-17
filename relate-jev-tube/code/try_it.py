"""Try the walking agent's MCP server with no MCP client installed: this script is the client.

  python try_it.py                         walk Kennington to Euston, using hosted Jev
  python try_it.py --stub                  same, offline (no API key)
  python try_it.py --suspend "Green Park"  suspend a station after the first hop, to provoke a close call

A scripted loop plays the agent. When the margin is low you are the person: type the station you want.
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

HERE = Path(__file__).parent

async def answer_the_person(context, params):
    """The server calls this when ask_human needs a person."""
    print("\n" + params.message)
    options = params.requested_schema["properties"]["station"]["enum"]
    while True:
        choice = input("Type the station to go to: ").strip()
        if choice in options:
            return types.ElicitResult(action="accept", content={"station": choice})
        print(f"Choose one of: {', '.join(options)}")

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", default="Kennington")
    ap.add_argument("--goal", default="Euston")
    ap.add_argument("--stub", action="store_true", help="use the offline scoring rule instead of hosted Jev")
    ap.add_argument("--suspend", help="suspend this station after the first hop")
    args = ap.parse_args()

    env = {**os.environ, "WALK_DB": os.environ.get("WALK_DB", str(HERE / "walk.cl"))}
    if args.stub:
        env["PICKER"] = "stub"
    if args.suspend:
        env["OPERATOR_TOOLS"] = "1"
    server = StdioServerParameters(command=sys.executable, args=[str(HERE / "server.py")], env=env)

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write, elicitation_callback=answer_the_person) as session:
            await session.initialize()

            async def call(tool, arguments=None):
                result = await session.call_tool(tool, arguments or {})
                text = result.content[0].text if result.content else "{}"
                try:
                    return json.loads(text)
                except ValueError:
                    return {"error": text}

            try:
                r = await call("start_walk", {"origin": args.origin, "goal": args.goal, "agent": "scripted loop"})
                if "error" in r:
                    print(r["error"])
                    return
                print(f"Walk {r['walk']}: {args.origin} to {args.goal}\n")

                while True:
                    here = (await call("where_am_i"))
                    if here.get("reached_goal") or here["steps_left"] <= 0:
                        break
                    if args.suspend and here["steps_taken"] == 1:
                        await call("set_station_status", {"station": args.suspend, "state": "suspended"})
                        print(f"(Operator: {args.suspend} suspended)")
                    j = await call("judge_hop")
                    if "error" in j:
                        print(j["error"])
                        return
                    shown = ", ".join(f"{k} {v:.2f}" for k, v in j["probabilities"].items())
                    print(f"At {here['station']}: {shown} | margin {j['margin']:.2f}")
                    step = await call("ask_human") if j["needs_human"] else await call("move", {"station": j["top"]})
                    if not step.get("ok"):
                        print(step.get("error"))
                        return
                    print(f"   -> {step['at']}{'  (a person chose)' if j['needs_human'] else ''}")
            finally:
                if args.suspend:
                    await call("clear_all_status")   # leave the stations running normally
                    print(f"(Operator: {args.suspend} cleared)")

            print("\nReached the goal." if here.get("reached_goal") else "\nStopped before the goal.")
            for kind in ("shaky", "path"):
                rows = (await call("ledger_report", {"kind": kind})).get("rows", [])
                if kind == "path":   # only this walk; the ledger holds every earlier one too
                    rows = [row for row in rows if row["walk"] == r["walk"]]
                print(f"\nLedger, {kind}{' (this walk)' if kind == 'path' else ' (all walks)'}: {len(rows)} row(s)")
                for row in rows:
                    print("  ", row)

asyncio.run(main())
