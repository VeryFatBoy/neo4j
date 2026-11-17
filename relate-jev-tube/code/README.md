# Graph-walking agent: MCP server

An MCP server that lets an agent walk the London Underground one hop at a time. The agent only sees its legal moves. Hosted Jev scores them, and every decision is written to a ledger in a Relate database (one `.cl` file).

The full write-up, with the queries and what the first runs showed, is in `report/technical-report.md`. The notebook version of the same logic is in `notebook/`.

```text
code/
  disrupt.py         suspend or delay a station from the command line
  llm_walk.py        runs the server and lets an LLM be the agent
  llm.py             adapters for the LLM that drives the walk (Claude, OpenAI-compatible, Ollama)
  load_data.py       builds walk.cl from the three Tube CSVs
  outside_client.py  walks the server with FastMCP's client, a second MCP implementation (pip install fastmcp)
  server.py          the MCP server (stdio)
  try_it.py          runs the server and plays the agent with a scripted loop, with you as the person
  walker.py          the tools, the pickers and the ledger reports (plain Python and Cypher, no MCP)
  requirements.txt
```

## Quick start

You need Python 3.10 or later (the MCP SDK and the hosted Jev client require it; it ran on 3.13 in testing). `pip install -r requirements.txt` installs Relate, which is the `relate-db` package on PyPI and imports as `relate`, and the MCP SDK, which must be version 2 or later (check with `pip show mcp`).

```shell
pip install -r requirements.txt
# put london_stations.csv, london_connections.csv and london_lines.csv in ../data (shared with the notebook)
python load_data.py
python try_it.py --stub                   # offline, no key
export TYPESAFE_API_KEY="your-key"
python try_it.py                          # hosted Jev
python try_it.py --suspend "Green Park"   # provoke a close call; type a station when asked
```

`load_data.py` deletes and rebuilds `walk.cl`, so it also resets the ledger.

## Let an LLM drive

`llm_walk.py` makes an LLM the agent. It starts the server, gives the model the agent tools and lets it walk. Jev still scores each hop and the model gives a one-sentence reason with every move, which goes into the ledger.

```shell
pip install anthropic                        # or: pip install openai (also covers Ollama)
export ANTHROPIC_API_KEY="your-key"
python llm_walk.py --llm anthropic:claude-sonnet-5-5
python llm_walk.py --llm openai:gpt-6.1-terra  # needs OPENAI_API_KEY; runs with reasoning off, which tools need on Chat Completions
python llm_walk.py --llm openai:gpt-6-luna     # cheaper; fine without a constraint, weaker on "avoid the Northern line"
python llm_walk.py --llm ollama:bonsai-2:9b    # a local model that supports tool calling
python llm_walk.py --llm ollama:bonsai-2:9b --constraint "avoid the Northern line" --suspend "Green Park"
```

`--llm` is `provider:model`. Built in: `anthropic`, `openai` (any OpenAI-compatible API; add `--base-url` for LM Studio, vLLM and others) and `ollama`.

For OpenAI's gpt-6 models the adapter sets reasoning effort to `none`, because Chat Completions only takes tools that way; set `OPENAI_REASONING_EFFORT` to change it. The model matters most when the passenger has a constraint: in the notebook runs, `gpt-6-luna` read the line lists inconsistently on "avoid the Northern line" and took 16 to 18 hops, while `gpt-6.1-terra` took the direct 8-hop route on every condition. Compare models afterward with the `agent` column and the `overrides` report. Use a model id your account can call and for Ollama a model that supports tools (`ollama show <model>` lists its capabilities).

**Swapping in another LLM** takes one small class in `llm.py`. An adapter has one method, `chat(system, messages, tools)`, which takes the conversation in a neutral format and returns the model's text and any tool calls. Subclass `LLM`, implement `chat`, add `@register("name")`, and run with `--llm name:model`. The loop and the tools never change.

Jev scores each hop for reliability only and does not see the passenger's constraint. The LLM can choose a different legal station when a constraint needs it and says why in the reason. Each step stores the model that drove it, so walks by different models can be compared with the `agents` and `agent_overrides` reports. Each step also stores the lines that serve the link it took (`lines`), so the `path` and `overrides` reports can show whether a walk kept to a constraint such as "avoid the Northern line". When a person answers `ask_human`, the move is made for the model and the tool result says so.

## The tools

| Tool | What it does | Writes to the ledger |
| --- | --- | --- |
| `start_walk(origin, goal, agent)` | Starts a walk. `agent` labels who is driving, for example the model name. Earlier walks stay in the ledger. | No |
| `where_am_i` | Current station, goal, hops taken and left, stations visited. | No |
| `next_hop` | The legal moves, each with lines, stops to the goal, live status, trouble one hop further on and a visited flag. The only list of legal moves. | No |
| `judge_hop` | Jev scores the legal moves. Returns the top pick, a probability for each, the margin and `needs_human`. Calling it twice returns the same judgment. | Yes: a `Step` with a `CONSIDERED` edge per option |
| `move(station, reason)` | Moves to a legal station. Refuses any other station and refuses when the margin is too low. The reason is stored. | Yes: `CHOSE`, who chose and why |
| `ask_human(reason)` | Asks the person which station to go to, showing Jev's probabilities and the agent's reason, then makes and records the move. | Yes, as a human decision, with the reason it was asked |
| `ledger_report(kind)` | Fixed read-only reports: `shaky`, `trouble`, `runner_ups`, `humans`, `overrides`, `path`, `walks`, `agents`, `agent_overrides`. No free Cypher. | No |

The server enforces three rules:

1. A move must be one that `next_hop` offered and an agent cannot move itself when the margin is below `LOW_MARGIN` (0.2): a person has to choose.
2. An agent also cannot move onto a station it has already visited twice (`MAX_VISITS`): that looks like a loop, so a person has to choose.
3. An agent may also ask a person at a higher margin, for example when its constraint cannot be met (every hop out of Kennington is Northern, so "avoid the Northern line" has to be broken once). Those steps are recorded as human decisions with the agent's reason, so the `humans` report lists them even though Jev was not unsure.

## Connect an MCP client

`try_it.py` is a small MCP client. To use a client such as Claude Desktop instead, add the server with absolute paths:

```json
{
  "mcpServers": {
    "walking-agent": {
      "command": "/path/to/venv/bin/python",
      "args": ["/path/to/code/server.py"],
      "env": {
        "WALK_DB": "/path/to/code/walk.cl",
        "TYPESAFE_API_KEY": "your-key"
      }
    }
  }
}
```

Use the virtual environment's own `python`, so the server can import Relate. Then ask the agent: "Walk me from Kennington to Euston." The server sends the agent its rules when it connects.

## Simulated incidents

The agent is never given the tools that change station status. To disrupt a station:

- **Before a walk, from the command line,** with the server stopped: `python disrupt.py suspend "Green Park"`, `python disrupt.py delay "Goodge Street" 5`, `python disrupt.py clear-all`, `python disrupt.py show`.
- **During a walk,** by starting the server with `OPERATOR_TOOLS=1`. That adds `set_station_status`, `clear_all_status` and `list_disruptions`. The agent can see these too, so use this for a live demo where you play the operator, not for an unattended run. `try_it.py --suspend` does this and clears the station afterward.

## Settings

Environment variables, all optional:
- `WALK_DB` (default `walk.cl`)
- `PICKER` (`jev` or `stub`, default `jev`)
- `LOW_MARGIN` (0.2)
- `MAX_STEPS` (30)
- `MAX_OPTIONS` (6)
- `MAX_VISITS` (2)
- `JEV_MODEL` (`jev-1.13.0`)

## Limits

- **`ask_human` needs a client that supports MCP elicitation.** If the client doesn't, the tool returns an error and nothing moves. A client that is itself an agent may answer without asking a person.
- **The server needs `mcp` 2.x.** `ask_human` asks the person through the SDK's input-required mechanism, which sends the question the way the client's protocol version allows: mid-call for older clients, as an input-required result for newer ones. `outside_client.py` drives the server with FastMCP's client in both modes and the person is asked in each. `mcp` 1.x is not supported: the server stops at start-up with a message saying so.
- **One writer.** The server holds the `.cl` file open. Stop it before running `disrupt.py` or `load_data.py` or opening the file in a notebook.
- **Hosted Jev is not deterministic.** Margins move between runs, including at the same hop.
- **State is in memory.** One walk at a time. The ledger is kept in the file, but restarting the server forgets the walk in progress.
- **The ledger records every line that serves a link, not the one the passenger rides.** Where two lines serve a link, `lines` lists both.

## Status

Built and run on Relate with hosted `jev-1.13.0`, through `try_it.py`: a clear walk, an offline walk, and a walk with Green Park suspended that handed one hop to a person.

The OpenAI adapter in `llm.py` has run against real models (`gpt-6-luna` and `gpt-6.1-terra`) from the notebook, which calls the tools in-process.

`llm_walk.py`, the route through the MCP server, has run with `gpt-6.1-terra` and hosted Jev on Relate 1.0.0 and `mcp` 2.3.0, with a person answering `ask_human` at the keyboard.

With Green Park suspended, the Northern and Jubilee runs and a run with no constraint all reached Euston in 7 or 8 hops. One no-constraint run looped between Victoria and Pimlico for 26 hops after a person chose St. James's Park at a tied hop. That led to the `MAX_VISITS` guard; with it, the same path asked a person four times, was steered back to Embankment and reached Euston in 16 hops.

The Anthropic adapter has run through the server with `claude-sonnet-5-5` on the same three conditions, taking 8, 8 and 7 hops. On the St. James's Park path it avoided the loop by itself but took 22 hops. The Ollama adapter has only had its message conversion tested. It has also been driven by FastMCP's client (`outside_client.py`) in both protocol modes, with the person asked in each. Not yet driven by an application such as Claude Desktop.
