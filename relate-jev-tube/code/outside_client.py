"""A second, independent MCP client: FastMCP's client, not the official SDK's ClientSession that llm_walk.py and try_it.py use.
It starts the server, walks Kennington to Euston taking Jev's top pick, and answers ask_human with a prompt.

  pip install fastmcp          (needs mcp 2.x; fastmcp 4 brings it)
  python outside_client.py --stub --suspend "Green Park"                 # offline scoring; add your TYPESAFE_API_KEY and drop --stub for hosted Jev
  MCP_MODE=legacy python outside_client.py --stub --suspend "Green Park"   # the older protocol mode, for comparison

FastMCP negotiates the newest protocol by default ("auto"); the server's ask_human works in both modes.
"""

import argparse
import asyncio
import json
import os
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from pathlib import Path

HERE = Path(__file__).resolve().parent

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--suspend")
    args = ap.parse_args()
    env = {**os.environ, "WALK_DB": os.environ.get("WALK_DB", str(HERE / "walk.cl"))}
    if args.stub:
        env["PICKER"] = "stub"
    if args.suspend:
        env["OPERATOR_TOOLS"] = "1"

    async def answer(message, response_type, params, context):
        print("\n" + message)
        schema = getattr(params, "requested_schema", None) or getattr(params, "requestedSchema", None) or {}
        options = list(schema.get("properties", {}).get("station", {}).get("enum", []))
        while True:
            choice = input("Type the station to go to: ").strip()
            if not options or choice in options:
                return {"station": choice}
            print("Choose one of:", ", ".join(options))

    transport = StdioTransport(command=sys.executable, args=[str(HERE / "server.py")], env=env)
    async with Client(transport, elicitation_handler=answer, mode=os.environ.get("MCP_MODE", "auto")) as client:
        print("Connected through FastMCP's client. Tools:", ", ".join(t.name for t in await client.list_tools()))

        async def call(name, arguments=None):
            result = await client.call_tool(name, arguments or {}, raise_on_error=False)
            return json.loads(result.content[0].text)

        await call("start_walk", {"origin": "Kennington", "goal": "Euston", "agent": "fastmcp client"})
        try:
            while True:
                here = await call("where_am_i")
                if here.get("reached_goal") or here["steps_left"] <= 0:
                    break
                if args.suspend and here["steps_taken"] == 1:
                    await call("set_station_status", {"station": args.suspend, "state": "suspended"})
                    print(f"(Operator: {args.suspend} suspended)")
                j = await call("judge_hop")
                step = await call("ask_human", {"reason": "margin too low"}) if j["needs_human"] else await call("move", {"station": j["top"], "reason": "Jev's top pick"})
                print(f"{here['station']} -> {step.get('at')}{'  (a person chose)' if j['needs_human'] else ''}")
                if not step.get("ok"):
                    print("Stopped:", step.get("error"))
                    break
        finally:
            if args.suspend:
                await call("clear_all_status")
        print("\nReached the goal." if here.get("reached_goal") or step.get("reached_goal") else "\nStopped before the goal.")

asyncio.run(main())
