# Relate, Jev and MCP: A Judgment Ledger for Agent Decisions

An agent walks the London Underground from Kennington to Euston one hop at a time. At each station it sees only its legal moves, Jev scores them and a margin gate decides who picks: the agent or a person. Every decision is written to a ledger in Relate, so you can ask afterward where the agent was unsure.

This project has four parts:

```text
code/        Python scripts, the MCP server (server.py) and the LLM adapters (llm.py, llm_walk.py)
data/        the three Tube CSVs, shared by the notebook and the scripts (see data/README.md)
notebook/    walking_agent_ledger.ipynb, the Jupyter version of the same logic
report/      the write-up with results from the terminal code
```

Start with `code/README.md` for the terminal route (including letting an LLM drive: Claude, OpenAI or Ollama) or open the notebook. `notebook/technical-report.md` explains the design and how the two routes differ.

Relate is the `relate-db` package on PyPI and imports as `relate`. `pip install -r code/requirements.txt` installs everything.
