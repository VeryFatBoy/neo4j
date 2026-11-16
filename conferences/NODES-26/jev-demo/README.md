# Jev demos: transit routing

The graph proposes the route options, Jev picks one and says how sure it is and the graph remembers the decision. The example is Kennington to Euston on the London Underground.

This README is both a guide and a reference. If you are giving the conference talk, read [Conference run sheet](#conference-run-sheet). If you are running everything after the conference, start at [Running everything, in order](#running-everything-in-order) and then read [Reading the results](#reading-the-results).

## Contents

1. [What this is](#what-this-is)
2. [What to run and when](#what-to-run-and-when)
3. [Layout](#layout)
4. [Setup](#setup)
5. [How it works](#how-it-works)
6. [Demo 1: one decision, then a flip](#demo-1-one-decision-then-a-flip)
7. [Demo 2: the graph proposes, Jev decides, the graph remembers](#demo-2-the-graph-proposes-jev-decides-the-graph-remembers)
8. [The Streamlit app](#the-streamlit-app)
9. [Replay](#replay)
10. [Running everything, in order](#running-everything-in-order)
11. [Reading the results](#reading-the-results)
12. [Conference run sheet](#conference-run-sheet)
13. [Querying the graph](#querying-the-graph)
14. [Troubleshooting](#troubleshooting)
15. [Known limits and validation status](#known-limits-and-validation-status)
16. [Data notes](#data-notes)
17. [Changing the journey or the network](#changing-the-journey-or-the-network)
18. [Glossary](#glossary)

## What this is

Jev is a model from TypeSafe AI that answers with a typed choice instead of a paragraph. "Typed" means the answer is always one of the options you supplied, never free text, so your code can branch on it directly. You give it a situation (the state), a question and a list of options. It returns the option it picks, a probability for every option and a margin: the top probability minus the second. A margin of 0.9 means a clear favorite. A margin of 0.05 means a near tie.

The demos use that margin to answer one question: which of two Northern line branches should a passenger take from Kennington to Euston, given what is wrong on each branch right now?

- The **Charing Cross branch** has 8 stops: Waterloo, Embankment, Charing Cross, Leicester Square, Tottenham Court Road, Goodge Street, Warren Street, Euston.
- The **Bank branch** has 9 stops: Elephant and Castle, Borough, London Bridge, Bank, Moorgate, Old Street, Angel, Kings Cross St. Pancras, Euston.

In Demo 2 and the app the same two routes are named after their first distinctive station: **Via Waterloo** (the Charing Cross branch) and **Via Elephant and Castle** (the Bank branch).

There are three things to run and they build on each other:

| Piece | What it shows | Needs Neo4j |
| --- | --- | --- |
| Demo 1 notebook | Jev on its own, from Python. One decision, then a flip when a station is suspended. | No |
| Demo 2 notebook | The same decision inside Neo4j: GDS finds the routes, Cypher writes their status, `jev.decide` picks and the graph stores the decision. | Yes |
| Streamlit app | Demo 2 as an interactive tool, with a map, scenario buttons, a decision log and two batch views. It keeps working, in a reduced form, without Neo4j. | Optional |

All incidents are simulated. Nothing here reads a live service.

## What to run and when

| Situation | Run |
| --- | --- |
| Giving the conference talk | Demo 1 notebook, then the Streamlit app. See the [run sheet](#conference-run-sheet). |
| Learning how the Neo4j side works, one step at a time | Demo 2 notebook |
| Trying your own journeys and incidents | The Streamlit app |
| Reproducing every result in this README | All three, in the order in [Running everything](#running-everything-in-order) |
| No network, no Neo4j | Demo 1 in replay mode, or the app in replay mode |

## Layout

```text
jev-demo/
  app/         the Streamlit app: app.py, core.py, config.yaml, config_validator.py, requirements.txt
  data/        the three London CSVs, shared by the notebooks and the app
  notebooks/   demo1_branch_decision.ipynb, demo2_graph_proposes.ipynb
  jar/         neo4j-jev-decide-1.0.0.jar built on an Apple Mac M4
  replay/      replay.json (and replay.json.bak), written by Demo 1 and the app, read by both
```

Every path is relative. Open the notebooks from the `notebooks` folder and start the app from the `app` folder.

| File | Role |
| --- | --- |
| `app/app.py` | The Streamlit user interface. |
| `app/core.py` | All the logic: routes, status text, Jev calls, replay, Cypher and a pure-Python fallback. It has no Streamlit imports, so you can import and test it on its own. |
| `app/config.yaml` | Every setting except the credentials. |
| `app/config_validator.py` | Checks `config.yaml` at startup and stops with a clear message if something is wrong. |
| `replay/replay.json` | Saved hosted decisions. See [Replay](#replay). |

## Setup

### What each piece needs

| | Python packages | Neo4j and plugins | Jev hosted key | Ollama |
| --- | --- | --- | --- | --- |
| Demo 1, hosted | `typesafe-sdk`, `pandas`, `plotly` | No | `TYPESAFE_API_KEY` environment variable | No |
| Demo 1, local | same | No | No | Yes, with `tev1:4b` |
| Demo 1, replay | same | No | No | No |
| Demo 2 | `neo4j`, `pandas` | Yes: Jev JAR (you build it, see [Setup](#2-neo4j-desktop-demo-2-and-the-app)) and GDS | In `neo4j.conf` | Only for section 12 |
| App | see `app/requirements.txt` | Optional (see tiers below) | In `neo4j.conf` | Only for local mode |

The hosted Jev key lives in two different places, which is a common stumbling block. Demo 1 calls the hosted API from Python, so the key is an environment variable. In Demo 2 and the app the call happens inside Neo4j (`jev.decide`), so the key goes into `neo4j.conf` and the notebook or app never sees it.

### 1. Python

Use Python 3.12. Create a virtual environment and install what you need:

```
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install notebook
pip install -r app/requirements.txt
```

The notebooks install their own pinned packages in section 1 (`%pip install ...`).

### 2. Neo4j Desktop (Demo 2 and the app)

Use Neo4j Desktop. Aura cannot run custom plugins, so it does not work here.

1. Create a DBMS and a database. **Use a scratch database.** Demo 2 section 5 deletes everything in the target database.
2. Add two plugins to the DBMS `plugins` folder: Graph Data Science (GDS) and the Jev plugin JAR. **The Jev JAR has to be built before you can use it.** It is not covered in this README. The book [Lessons Learned Putting Jev Inside Neo4j](https://jev-inside-neo4j.github.io/) is a complete walkthrough, with examples, of how to build it and get Jev working inside Neo4j. The demos were run with a JAR named `neo4j-jev-decide-1.0.0.jar`. Build it first, copy it into the `plugins` folder, then continue with the settings below.
3. In the DBMS settings (`neo4j.conf`), add these lines, then restart the DBMS:

   ```text
   dbms.security.procedures.allowlist=jev.*,gds.*
   dbms.security.procedures.unrestricted=gds.*
   server.jvm.additional=-DTYPESAFE_API_KEY=<your-api-key>
   ```

   The first two allow the procedures. The third gives the Jev plugin your key for the hosted API.

### 3. Environment variables

Set these before launching Jupyter or the app. The notebooks and the app read them at startup, so a change needs a restart.

```
export NEO4J_URI="bolt://localhost:7687"
export NEO4J_USERNAME="neo4j"
export NEO4J_PASSWORD="your-password"
export NEO4J_DATABASE="your-database"
export TYPESAFE_API_KEY="your-api-key"  # Demo 1 hosted mode only
```

Optional overrides for Demo 1:
- `JEV_MODEL` (default `jev-1.13.0`)
- `LOCAL_JEV_URL` (default `http://localhost:11434`)
- `LOCAL_JEV_MODEL` (default `tev1:4b`)

### 4. The data

Put the three London Underground CSV files in `data/`:
- `london_stations.csv`
- `london_connections.csv`
- `london_lines.csv`

See [Data notes](#data-notes) for their columns and what the loaders do to them.

### 5. Ollama (optional)

For the local model, start Ollama and pull the model once: `ollama pull tev1:4b`. The first call after the model loads is slow. Warm it up before you rely on it.

### 6. Pre-flight

Every piece has a pre-flight check. Run it before anything else, on the network you will use.

- Demo 1: run the cell that calls `preflight()`. It checks hosted, local and replay.
- Demo 2: run `preflight()`. It checks Neo4j, the `jev.decide` function, GDS, the stations, leftover disruptions, route proposal and a decision on each service.
- App: click **Pre-flight check** in the sidebar.

## How it works

1. **The graph proposes.** GDS projects the network and finds a pool of short routes with Yen's k shortest paths (`pool: 40`). A filter keeps the ones that really differ: two routes may share at most half their stations (`max_overlap: 0.5`). Routes are ranked by stops plus an interchange penalty (`interchange_cost: 3`). Each option carries its lines and interchanges and is named after the first station only it uses ("Via Waterloo").
2. **Cypher adds the live status.** Each route's suspended and delayed stations are read from the `active` and `delay_minutes` properties on the station nodes and written as a sentence, such as `Suspended: Warren Street.` or `Delays: Goodge Street (5 min).` A clean route reads `All stations clear.`
3. **Jev decides.** `RETURN jev.decide($state, $questions, $options)` returns the pick, a probability for every route and metadata (model, latency). The margin is computed from the probabilities.
4. **The graph remembers.** Each live decision is stored as a `Decision` node, linked to the origin and destination (`FROM`, `TO`) and to every disrupted station (`CONCERNS`).

### What Jev sees

Jev gets plain words and nothing else. It never sees the map or the graph.

- **State:** the current station, the destination, the network name and one status sentence per route. In Demo 2 and the app the keys look like `status_via_waterloo`. In Demo 1 they are `charing_cross_branch` and `bank_branch`.
- **Question:** one `choice` question: which route should the passenger take to reach the destination as reliably as possible given the current state of the routes.
- **Options:** one per route, each described in a sentence, for example `Via Waterloo, Embankment, Charing Cross, Leicester Square, Tottenham Court Road, Goodge Street, Warren Street. 8 stops. On the Northern line.`

Because Jev sees only that text, anything not in the text cannot influence the answer. The margin reflects how clearly the words separate the options.

### The graph model

| Element | Properties |
| --- | --- |
| `(:Station)` | `name`, `lat`, `lon`, `zone`, `postcode` and, only while an incident is set, `active` (false when suspended) and `delay_minutes` |
| `[:CONNECTS_TO]` | `line`, `color`. Stored once in each direction, so 458 connections are 916 relationships. |
| `(:Decision)` | `id`, `asked_at`, `choice`, `margin`, `probability` (of the choice), `model`, `route` (the chosen route's stations), `state` (the JSON Jev saw) |
| `[:FROM]`, `[:TO]` | From a `Decision` to the origin and destination stations |
| `[:CONCERNS]` | From a `Decision` to each station that was suspended or delayed at the time |

## Demo 1: one decision, then a flip

`notebooks/demo1_branch_decision.ipynb`. Python only, no database. It asks Jev one question at Kennington and shows how the answer and the margin move when a station goes offline.

### Sections

| Section | What happens |
| --- | --- |
| 1 to 2 | Install and imports. |
| 3 Configuration | `MODE` is `"hosted"`, `"local"` or `"replay"`. `DEBUG = True` shows full tracebacks instead of one-line messages. |
| 4 The network | The two branches in route order, the state builder and four scripted scenarios. Station names must match the CSV spelling exactly (for example `Kings Cross St. Pancras`). A name not on either branch raises an error that lists the valid ones. |
| 5 The question | One `Choice` question with two options, each with a short description. |
| 6 Ask Jev | `ask()` sends a state and returns the choice, the probabilities, the margin and the call time. Hosted calls time out after 5 s, local after 60 s. Any failure prints one line saying what happened and which mode to try, then returns nothing. Also holds the warm-up and `preflight()`. |
| 7 One decision | Both branches clear. |
| 8 Your turn | Edit `OFFLINE` and `DELAYED`, for example `OFFLINE = ["Warren Street"]` or `DELAYED = {"Goodge Street": 5}`. |
| 9 Four scenarios | A table and bar chart of the four scripted scenarios. |
| 10 Save for replay | Writes the hosted decisions to `../replay/replay.json` for replay mode and the app. |

### The four scripted scenarios

| Scenario | State |
| --- | --- |
| No disruption | Both branches clear |
| Mild | Goodge Street delayed 5 minutes |
| Severe | Warren Street suspended |
| Critical | Warren Street and Goodge Street suspended |

### Expected output

A real hosted run, then with Warren Street suspended:

```
Charing Cross | Charing Cross 0.86, Bank 0.14 | margin 0.72 | reported confidence 0.72 | 156 ms | hosted
Bank | Bank 0.95, Charing Cross 0.05 | margin 0.90 | reported confidence 0.90 | 342 ms | hosted
```

The next section should show the choice column as `Charing Cross`, `Bank`, `Bank`, `Bank`. If the first row is not Charing Cross, or any of the other three is not Bank, see [Reading the results](#reading-the-results).

### Things to know

- **Reported confidence.** The hosted Jev reports the margin as its confidence. The local model reports one minus the normalized entropy, a different number. When you switch modes, compare the `margin` column, which the notebook computes from the probabilities.
- **Warm up.** `warm_up()` makes one throwaway call with a state that is not part of the demo. A state the model has just seen comes back almost instantly, so warm up with something you will not demo.
- **Replay needs saving first.** Replay mode reads `replay.json`. On a fresh checkout the file does not exist and the pre-flight says `0 state(s) saved`. Run sections 7 to 9 in hosted mode, then section 10 and replay is ready.
- **Saving is safe.** Section 10 merges by state, copies the previous file to `replay.json.bak` and writes through a temporary file, so an interrupted save cannot leave a half-written file. A later run replaces an earlier one for the same state.

## Demo 2: the graph proposes, Jev decides, the graph remembers

`notebooks/demo2_graph_proposes.ipynb`. The same decision, with Neo4j doing more of the work. Run the cells one at a time, from the top.

### Warning

The notebook deletes every node, relationship, constraint and index in the target database. **Point `NEO4J_DATABASE` at a scratch database first.** Set `CLEAR_DATABASE = False` or `RELOAD_DATA = False` in the config cell to skip the wipe or the reload.

### Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `ORIGIN`, `DESTINATION` | Kennington, Euston | The journey. |
| `LINES` | `["Northern"]` | Lines the graph may use. `None` means the whole network. |
| `K` | 2 | How many routes the graph proposes. |
| `POOL` | 40 | Short routes Yen's finds before the distinct ones are picked. |
| `MAX_OVERLAP` | 0.5 | Two routes may share at most this fraction of their stations. |
| `INTERCHANGE_COST` | 3 | An interchange counts as this many extra stops when ranking. |
| `GRAPH_NAME` | `routes` | Name of the GDS projection. |
| `CLEAR_DATABASE`, `RELOAD_DATA` | `True`, `False` | See the warning above. |
| `HOSTED_OPTIONS` | `jev-1.13.0`, 1 retry, 8 s timeout | The third argument of `jev.decide`. Pinned to the model Demo 1 uses. |
| `LOCAL_OPTIONS` | `tev1:4b`, no retries, 60 s timeout | Same, for the local model. |

### Sections

| Section | What happens |
| --- | --- |
| 1 to 4 | Install, imports, configuration, error handling and the Neo4j connection. Any failure prints one line that says what to do and the notebook carries on. Fix the problem and rerun the failing cell. |
| 5 Clear and load | Wipes the database, reads the CSVs and loads the network. |
| 6 Helpers and pre-flight | The route, status, decision and memory helpers, plus `preflight()`. |
| 7 The graph proposes | Projects the network into GDS and proposes the routes. |
| 8 The live state | Reads each route's status from the station properties. |
| 9 Jev decides | One `jev.decide` call, stored in the graph. |
| 10 Take a station offline | Suspends Warren Street and asks again. "Your turn" resets and delays Goodge Street by 5 minutes. |
| 11 The graph remembers | Three queries over the stored decisions, then a reset. |
| 12 Local model (optional) | The same call on `tev1:4b`. Not stored. |
| 13 All lines (optional) | Opens the network to every line and proposes three routes. Not stored. |
| 14 Teardown | Drops the GDS projection and closes the connection. |

### Expected output, in order

| Section | What you should see |
| --- | --- |
| 5 | `Stations: 349`, `Connections: 458`, then `Loaded 349 stations.` and `Loaded 458 connections (bidirectional).`, then `Stations: 349, connections: 916` |
| 6 pre-flight | `Neo4j: connected`, `jev.decide: registered`, `GDS (Yen's): registered`, `Stations: 349`, `Disruptions: none`, `Routes: 2 proposed (Via Waterloo, Via Elephant and Castle)`, then a hosted and a local decision |
| 7 | Projection `{'stations': 52, 'connections': 106}`. A table with Via Waterloo (8 stops, 0 interchanges, Northern) and Via Elephant and Castle (9 stops, 0 interchanges, Northern). |
| 8 | Both routes `All stations clear.` |
| 9 | Via Waterloo, probabilities about 0.54 to 0.57 against 0.43 to 0.46, **margin about 0.08 to 0.14**, then `Stored decision <id>.` |
| 10 | Status `Suspended: Warren Street.` on Via Waterloo. Decision **Via Elephant and Castle, margin 0.98 to 1.00.** |
| 10 "Your turn" | Goodge Street delayed 5 minutes. Decision **Via Elephant and Castle, margin about 0.98.** |
| 11 | Three queries (see below). After section 10 there are three stored decisions. The reset cells delete them (`nodes_deleted 3`, `relationships_deleted 8`). |
| 12 | Via Waterloo, probabilities about 0.51 and 0.49, margin 0.02 |
| 13 | Three routes with interchanges and a decision with a margin around 0.2. See the note on route sets in [Variability](#variability-between-runs). |

## The Streamlit app

Demo 2 as an interactive tool.

```
cd app
pip install -r requirements.txt
# set NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE
streamlit run app.py
```

Streamlit opens in the browser (by default at http://localhost:8501). Load the stations first by running Demo 2 section 5; the app does not load data into Neo4j itself. Close any notebook that holds the GDS projection, because only one projection (`gds.graph_name`) exists at a time. Use one browser session at once.

### The sidebar

| Control | What it does |
| --- | --- |
| **Lines** | Which lines the graph may use. None selected means the whole network. |
| **From**, **To** | Stations on the selected lines only. |
| **Options for Jev** | How many routes to propose, from 2 to `routes.max_k` (4). |
| **Model** | `hosted`, `local` or `replay`. |
| **Remember decisions in the graph** | Stores each live decision as a `Decision` node. Disabled without Neo4j. |
| **Reconnect** | Rebuilds the Neo4j connection. |
| **Reset all** | Clears the incident, the result and the batch views and clears the incident in the graph. |
| **Pre-flight check** | Checks Neo4j, the stations, GDS routes, Jev hosted and Jev local, one line each. |

A banner at the top of the page repeats that incidents are simulated.

### The main page

1. **The graph proposes.** A table of the options with stops, interchanges, lines and straight-line distance in km.
2. **Scenario buttons.** Built from the routes the graph proposed, so they follow your journey:
   - **No disruption**
   - **Mild:** a station near the end of the first route delayed 5 minutes
   - **Severe:** the same station suspended
   - **Both hit:** one station suspended on each of the first two routes

   On the default journey the stations are Goodge Street (first route) and Angel (second route). Each button sets the incident in Neo4j, calls `jev.decide` and redraws the map.
3. **Own incident.** Pick any station on the proposed routes, then **Suspend**, **Delay 5 min** or **Clear**.
4. **Map.** Each option has its own color. The chosen route gets a green halo. Suspended stations are red, delayed ones amber, the two ends of the journey black. Where routes share track the later color is drawn on top. `basemap: false` in `config.yaml` removes the online tiles for a room without a network.
5. **Decision panel.** The pick, a probability bar for every route, the margin, the latency, the model and the mode. A margin under `app.low_margin` (0.2) shows a **Close call** warning. If every route has a suspended station, the warning says so. A stored decision shows its id.
6. **The chosen route.** Stops, interchanges, distance, the station list, the lines used and the route's status.
7. **Tabs.**
   - **Decision log:** the stored decisions, newest first and the stations most often involved. "This journey only" filters. "Clear the log" deletes every `Decision` node.
   - **Single outage:** each station on the proposed routes (up to 30) suspended on its own, in one `jev.decideAll` call. A grouped bar chart shows each route's probability. The caption counts the routes chosen and the time taken.
   - **Delay curve:** one station delayed from 0 to 10 minutes, one `jev.decideAll` call. The line chart shows each route's probability and the caption says where, if anywhere, the decision flips.

   The two batch tabs use the sidebar model, need Neo4j and are disabled in replay mode.

### Modes and fallback tiers

| Situation | What the app does |
| --- | --- |
| Neo4j up, `hosted` | Routes from GDS, status from Cypher, decision from `jev.decide` on the hosted model, stored and recorded for replay (`replay.record: true`). |
| Neo4j up, `local` | Same, with the local model. Not recorded for replay. |
| Neo4j up, `replay` | Routes and status still come from Neo4j, but the decision is read from `replay.json` instead of calling Jev. A state that was never recorded shows a notice and no decision. |
| Neo4j down | Routes from a Python version of the same search, status computed in Python and the decision read from `replay.json`. The map is drawn from the CSV and the log and the batch tabs are unavailable. |

When something fails mid-demo the app says what happened and falls back one tier instead of showing a traceback. The decision panel labels recorded results as `recorded` and live ones as `live`.

### `config.yaml` reference

| Key | Default | Meaning |
| --- | --- | --- |
| `app.title` | `Transit routing: which route?` | Page title. |
| `app.low_margin` | 0.2 | A margin below this is flagged as a close call. |
| `app.basemap` | `true` | `false` for no online map tiles. |
| `neo4j.*_env` | `NEO4J_*` | Names of the environment variables that hold the credentials. |
| `jev.hosted` | `jev-1.13.0`, 1 retry, 8000 ms | Options passed to `jev.decide` in hosted mode. |
| `jev.local` | `http://localhost:11434/v1/systemone`, `tev1:4b`, 0 retries, 60000 ms | Options for local mode. |
| `journey.origin`, `journey.destination` | Kennington, Euston | Where the app starts. Both can be changed in the sidebar. |
| `journey.lines` | `[Northern]` | Starting lines. `[]` means the whole network. |
| `routes.k`, `routes.max_k` | 2, 4 | Default and maximum number of options. |
| `routes.pool` | 40 | Short routes GDS finds before filtering. Must be at least `max_k`. |
| `routes.max_overlap` | 0.5 | Overlap above which two routes count as the same. |
| `routes.interchange_cost` | 3 | Stops that one interchange counts as when ranking. |
| `gds.graph_name` | `routes` | The in-memory GDS projection, dropped and rebuilt for each journey. |
| `paths.*` | `../data/...`, `../replay/replay.json` | Where the data and the replay file live. |
| `replay.record` | `true` | Save each live hosted result so replay mode can show it later. |

The validator checks types and ranges at startup and stops the app with a list of what to fix.

## Replay

`replay/replay.json` stores decisions so the demos can run with no network. It is a dictionary keyed by the state.

- **Demo 1 keys** are the two branch statuses joined by a bar, for example `Suspended: Warren Street. | All stations clear.` The app ignores these.
- **App and Demo 2 keys** start with `v2`, then the journey, the lines and each route with what is wrong on it, for example `v2 | Kennington > Euston | Northern | Via Waterloo: All stations clear. | Via Elephant and Castle: All stations clear.`
- **Each entry** holds the choice, the probabilities, the latency in milliseconds and `recorded_with`, the model that produced it.
- **The file accumulates different states, not repeated runs.** A new hosted run of a state that is already saved overwrites the old entry. It is a store of one result per state, not a history, so it cannot give you the spread across runs.
- **The app writes an entry every time** a live hosted decision succeeds (`replay.record: true`). Run each scenario you plan to show once in hosted mode and replay can show it later.
- **A key includes** the journey, the lines, the route labels and the status text. It does not include the route descriptions or the model, so changing the journey or the route names makes old recordings stop matching, while changing the model does not (check `recorded_with`).
- **`replay.json.bak`** is the copy Demo 1 makes just before each save. It holds one older version, not a history.

The file shipped with this folder has 34 entries: 4 from Demo 1 and 30 `v2` entries covering the default journey and several others.

## Running everything, in order

Use this when you want to reproduce every result in this README.

1. **Set up.** Follow [Setup](#setup) and run each pre-flight check.
2. **Demo 1, hosted.** Run sections 1 to 9. Compare section 9 with the [expected ranges](#expected-results). Run section 10 to save the replay file.
3. **Demo 1, other modes.** Set `MODE = "local"` and rerun sections 7 to 9. Compare the `margin` column, not `confidence`. Set `MODE = "replay"` and rerun to confirm that it reads the saved decisions.
4. **Demo 2.** Point `NEO4J_DATABASE` at a scratch database. Run sections 1 to 11, then optionally 12 and 13, then run the two reset cells in section 11 before the teardown.
5. **The app.** Start it. Run the pre-flight check. Click the four scenario buttons in hosted mode. Open **Single outage** and click "Run the sweep". Open **Delay curve** and run it. Open **Decision log**.
6. **Replay.** Switch the app's model to `replay` and click the four scenarios again. They should all show a recorded result. Switch off Neo4j (stop the DBMS) and reload to see the fallback tier.

## Reading the results

### What the numbers mean

- **Probability.** How much of Jev's belief sits on each option. They sum to 1.
- **Margin.** The top probability minus the second. It measures how clearly Jev prefers one option. With two options it is twice the top probability minus one, so a 0.54 / 0.46 split is a margin of 0.08 and a 0.99 / 0.01 split is 0.98.
- **Confidence.** In the hosted Jev this equals the margin. In the local model it is one minus the normalized entropy, a different quantity. Always compare margins.
- **The close-call threshold.** 0.2, set by `app.low_margin`. A decision below it is flagged as worth a human check.
- **Margin is certainty, not correctness.** A confident pick can still be wrong. A low margin does not mean the answer is bad. It means the options are hard to tell apart from the text Jev sees.

### Expected results

Observed with hosted `jev-1.13.0`. Every figure is a margin unless stated. The "Runs" column is how many runs these figures come from, which is small, so treat the ranges as indicative. The conference deck quotes slightly wider ranges drawn from additional runs.

**Demo 1** (Python SDK, `Charing Cross` against `Bank`):

| Scenario | Choice | Margin range | Runs |
| --- | --- | --- | --- |
| No disruption | Charing Cross | 0.72 to 0.78 | 4 |
| Mild: Goodge Street delayed 5 min | Bank | 0.84 to 0.86 | 2 |
| Severe: Warren Street suspended | Bank | 0.90 to 0.92 | 4 |
| Critical: Warren Street and Goodge Street suspended | Bank | 0.92 to 0.94 | 2 |

**Demo 2 and the app** (Neo4j function, `Via Waterloo` against `Via Elephant and Castle`):

| Scenario | Choice | Margin range | Runs |
| --- | --- | --- | --- |
| No disruption | Via Waterloo | 0.08 to 0.14 | 3 |
| Mild: Goodge Street delayed 5 min | Via Elephant and Castle | 0.98 | 3 |
| Severe: Warren Street suspended | Via Elephant and Castle | 0.98 to 1.00 | 2 |
| Both hit: Goodge Street and Angel suspended (app) | Via Waterloo | 0.14 to 0.18 | 2 |
| Critical: Warren Street and Goodge Street suspended | not run in Demo 2 | | |

**The app's single-outage sweep** (one run, 15 stations, one at a time):

- All 15 outages sent the trip to the other branch.
- The 7 outages on the Via Waterloo route (Waterloo to Warren Street) chose Via Elephant and Castle. The 8 outages on the Via Elephant and Castle route (Elephant and Castle to Kings Cross St. Pancras) chose Via Waterloo.
- The winning route's probability was about 0.99 to 1.00 at every outage. Goodge Street was the lowest, at about 0.99.
- 15 decisions took 749 ms in one call.

**Speed** (hosted): a single decision takes roughly 130 to 340 ms. A local model takes a few milliseconds once warm and one to several seconds on the first call.

### What to look for

Use these as checks that everything is working.

1. **The choice flips when the chosen route breaks.** Clear day picks the Charing Cross branch. Suspend a station on it and the pick moves to the Bank branch. Suspend one on the Bank branch and the pick moves back. The sweep makes this a 15 of 15 check.
2. **The margin rises with the severity of the incident.** Clear day is lowest, a 5-minute delay is higher, a suspension is highest.
3. **The margin collapses when neither route is clean.** "Both hit" lands back under 0.2 and the app shows the Close call warning with the note that every route has a suspended station.
4. **Demo 2 reads the routes correctly.** 8 stops and 9 stops, 0 interchanges, 15 stations in all, 5.3 km and 8.1 km.
5. **The graph remembers.** After section 10 in Demo 2 there are three `Decision` nodes. The Warren Street query returns one row and its choice is Via Elephant and Castle.
6. **The status sentence reaches Jev.** `Suspended: Warren Street.` appears against Via Waterloo in section 10 and the decision follows it.

### How to interpret them

- **A clear day is a near tie and that is correct.** The two branches are almost equivalent on a clear day (8 stops against 9), so a low margin is the honest answer. The app flags it as a close call. In a talk this is a feature: no disruption means no strong reason to prefer either branch.
- **A suspension is decisive.** A margin around 0.98 means Jev treats a suspended station on one branch as close to disqualifying that branch.
- **A 5-minute delay is already decisive in Demo 2.** A delay of 5 minutes at Goodge Street produces the same margin as a suspension. The Delay curve tab shows where the decision flips for any station. We have not recorded a curve run, so we cannot say yet where on the curve it happens for Goodge Street.
- **"Both hit" is the case for a human.** When both routes have a problem, Jev still returns an answer, but with a margin under the threshold. The right response is to look at the options, not to trust the pick.
- **The local model is noticeably less decisive.** In Demo 2 on a clear day it returned 0.51 / 0.49 (margin 0.02) both times we ran it. It runs in milliseconds and is useful as a fallback, but compare its margins with its own history, not with the hosted model.
- **The decision log is the audit trail.** Each stored decision records what Jev saw, what it chose, how sure it was and which stations were disrupted. Use it to ask which stations drive close calls and how often.

### Variability between runs

Hosted Jev is not deterministic. Expect the following.

- **Margins move.** The same state gives a spread of margins from run to run. The tables above show the spread we saw. Quote ranges and name the route, not single numbers.
- **Choices on near ties can flip.** On a clear day the margin is small, so the choice is the one thing that could change between runs. We have seen Via Waterloo every time so far, but a margin of 0.08 does not rule out a flip.
- **Choices on decisive states hold.** Suspensions and delays gave the same choice every time.
- **Demo 1 and Demo 2 margins are not comparable.** The same clear-day question gave 0.72 to 0.78 in Demo 1 and 0.08 to 0.14 in Demo 2. The two demos use different option names and state wording and we have not isolated which difference causes the gap. Compare results within a demo, not across demos.
- **The set of routes can vary on the whole network.** With `LINES = None`, Demo 2 section 13 proposed Via Embankment, Via Elephant and Castle and Via Westminster on one run and Via Embankment, Via Westminster and Via Oval on another. The saved replay file also holds several different three-route sets for the same journey. We have not isolated the cause. Many routes tie on length, so one possibility is that the pool GDS returns differs between runs. On the Northern line alone the two branches were the same every time.
- **Latency is variable.** Single calls ranged from about 130 to 340 ms.

## Conference run sheet

At the conference the plan is Demo 1, then the Streamlit app. The app shows the Demo 2 flow, so the "Demo 2" slide is delivered through the app.

### Before the talk

1. Start Neo4j and make sure the stations are loaded (run Demo 2 section 5 once).
2. Run the Demo 1 pre-flight: hosted, local and replay should all be ready.
3. Run the app's **Pre-flight check**: Neo4j, Stations, GDS routes, Jev hosted and Jev local should all be green.
4. Run each scenario you plan to show once in hosted mode, so replay has them. The app records them as it goes.
5. Run Demo 1 section 10 to save its decisions for replay.
6. Do this on the network you will use on the day.
7. Reset any leftover incident: **Reset all** in the app, or the reset cells in Demo 2 section 11.

### Demo 1 (suggested: about 5 minutes)

1. Section 7: both branches clear. Charing Cross, margin around 0.75. Point out that the answer is a choice with probabilities, not a paragraph.
2. Section 8: set `OFFLINE = ["Warren Street"]`. The choice flips to Bank, margin around 0.9.
3. Section 9: the four scenarios. Say that the margin rises as the incident gets worse.
4. Say that runs vary, so you quote ranges.

### The app (suggested: about 10 minutes)

1. Show **The graph proposes**: two routes, 8 and 9 stops, 5.3 and 8.1 km, found by GDS rather than listed by hand.
2. Click **No disruption**. A near tie (margin about 0.1) and the Close call warning. Explain why: nothing separates the routes.
3. Click **Severe**. The choice moves to the other route with a margin of about 0.98. Point at the green halo and the red station on the map.
4. Click **Both hit**. The margin falls back under 0.2 and the warning says every route has a suspended station. This is the case for a human.
5. Open **Decision log**: every decision is a node in the graph, linked to the stations involved.
6. Open **Single outage** and click "Run the sweep": 15 outages in under a second and 15 of 15 flip to the other route.

### If something fails

| Failure | Fall back to |
| --- | --- |
| Hosted Jev is unreachable | Switch the app's model to `local`, or to `replay`. |
| Local model is cold or missing | Use `replay`. |
| Neo4j is down | The app falls back to Python routes and replay on its own. |
| The app will not start | Demo 1 in replay mode. |
| Nothing works | The slides. The backup slides hold the margin ranges, the sweep and the both-hit result. |

The ladder, from the deck: hosted Jev, then a local model, then saved results, then the notebook.

## Querying the graph

Every decision is data. Run these in Neo4j Browser, or through the Demo 2 notebook's `run_cypher`.

Which decisions involved Warren Street:

```cypher
MATCH (d:Decision)-[:CONCERNS]->(:Station {name: 'Warren Street'})
RETURN toString(datetime.truncate('second', d.asked_at)) AS asked, d.choice AS choice, round(d.margin, 2) AS margin
ORDER BY asked
```

Close calls and what was disrupted when they were made:

```cypher
MATCH (d:Decision)
WHERE d.margin < 0.2
OPTIONAL MATCH (d)-[:CONCERNS]->(s:Station)
RETURN toString(datetime.truncate('second', d.asked_at)) AS asked, d.choice AS choice, round(d.margin, 2) AS margin,
       collect(s.name) AS disrupted
ORDER BY margin
```

Everything stored, newest first:

```cypher
MATCH (d:Decision)-[:FROM]->(a:Station), (d)-[:TO]->(b:Station)
OPTIONAL MATCH (d)-[:CONCERNS]->(s:Station)
RETURN toString(datetime.truncate('second', d.asked_at)) AS asked, a.name + ' to ' + b.name AS trip, d.choice AS choice,
       round(d.margin, 2) AS margin, collect(s.name) AS disrupted
ORDER BY asked DESC
```

Stations most often involved in a decision:

```cypher
MATCH (d:Decision)-[:CONCERNS]->(s:Station)
RETURN s.name AS station, count(d) AS decisions
ORDER BY decisions DESC, station
LIMIT 15
```

Margins by choice (a standard aggregation; we did not run this one in our sessions):

```cypher
MATCH (d:Decision)
RETURN d.choice AS choice, count(d) AS decisions,
       round(min(d.margin), 2) AS lowest, round(avg(d.margin), 2) AS average, round(max(d.margin), 2) AS highest
ORDER BY decisions DESC
```

Remove every incident, so every station counts as running normally again:

```cypher
MATCH (s:Station)
WHERE s.active IS NOT NULL OR s.delay_minutes IS NOT NULL
REMOVE s.active, s.delay_minutes
```

Delete the stored decisions (the stations and connections stay):

```cypher
MATCH (d:Decision)
DETACH DELETE d
```

## Troubleshooting

The notebooks and the app print one line for most failures and say what to do. The common ones:

| Message or symptom | Cause and fix |
| --- | --- |
| `Not set: NEO4J_URI, ...` | The environment variables were not set before launching Jupyter or Streamlit. Set them and restart the kernel or the app. |
| `Neo4j rejected the login.` | Wrong `NEO4J_USERNAME` or `NEO4J_PASSWORD`. |
| `Can't reach Neo4j at <uri>.` | The DBMS is not running. Start it in Neo4j Desktop. |
| `Database '<name>' doesn't exist.` | Wrong `NEO4J_DATABASE`. |
| `The database isn't available yet.` | The database is still starting. Wait and rerun. |
| `The jev functions aren't available.` | The Jev JAR has not been built or is not in the DBMS `plugins` folder (see [Lessons Learned Putting Jev Inside Neo4j](https://jev-inside-neo4j.github.io/) for how to build it), the `allowlist` line is missing from `neo4j.conf`, or the DBMS was not restarted. |
| `Graph Data Science isn't available.` | GDS is not installed, or `gds.*` is missing from the `allowlist` and `unrestricted` lines. Restart afterwards. |
| `Can't reach the hosted Jev (check the network).` | Network problem, or a timeout. Try replay. |
| `Can't reach the local model (is Ollama running?).` | Start Ollama and pull `tev1:4b`. |
| `TYPESAFE_API_KEY isn't set.` (Demo 1) | Set the environment variable before launching Jupyter, or use `local` or `replay`. |
| In Demo 2 or the app, hosted decisions fail but local ones work | Check the key line in `neo4j.conf`. The notebook and the app never see the key. Add the `server.jvm.additional` line and restart the DBMS. |
| `That state wasn't saved for replay.` (Demo 1) | Replay only knows the four scripted scenarios. |
| `No recording for this state.` (app) | Run that state once in hosted mode and the app saves it. |
| `Fewer than two distinct routes` | Add more lines in the sidebar, or lower `routes.max_overlap`. |
| `No route from X to Y` | The two stations are not connected on the selected lines. |
| `Stations: none loaded (run Demo 2 section 5)` | Load the data first by running Demo 2 section 5. |
| GDS error about an existing projection, or odd routes when two sessions are open | Only one projection (`gds.graph_name`) exists at a time. Close the other session, then reconnect. |
| A station still shows as suspended after a demo | Run **Reset all** in the app, or the reset cell in Demo 2 section 11. |
| `Can't find ...csv` | Put the three CSV files in the shared `data` folder, one level above the notebook or app folder. |
| `config.yaml: ...` errors | The validator found a bad setting. The message names the key. |
| Replay mode says the file was not found | The pre-flight in Demo 1 shows `0 state(s) saved` until you run section 10 once in hosted mode. |

## Known limits and validation status

### What has been run

- **Demo 1:** run end to end in hosted mode several times, with local and replay pre-flight. Results above.
- **Demo 2:** run end to end in hosted mode twice, including the local model and the all-lines section. Results above.
- **App:** run in hosted mode with Neo4j connected: the "Both hit" scenario and the single-outage sweep. The route logic and the replay keys were also checked offline against the CSVs and `replay.json`.

### What has not been run

In the sessions this README is based on:

- The **Delay curve** tab.
- The **Decision log** tab's "Clear the log" button.
- **Local** mode and **replay** mode inside the app.
- The app's Python fallback with Neo4j actually stopped.
- Anything on Aura (unsupported) or a Neo4j version other than the one in use. The Neo4j Desktop version was not recorded.

### Limits to know about

- **Hosted Jev is not deterministic.** See [Variability](#variability-between-runs).
- **Demo 2 wipes the target database** unless you change the settings.
- **One GDS projection at a time,** so one browser session at once.
- **The app's "Severe" scenario suspends Goodge Street, but the notebooks and the deck suspend Warren Street.** Both are on the Via Waterloo route and both flipped the decision to Via Elephant and Castle in our runs.
- **The route names differ between demos.** Demo 1 says Charing Cross and Bank. Demo 2 and the app say Via Waterloo and Via Elephant and Castle. They are the same two routes.
- **A margin display edge case.** If Jev were ever to return a single probability, the app's Margin display would fail before its None check. With two or more routes this does not happen.
- **Scenario button labels can truncate** when the browser window is narrow.
- **Straight-line distances** are summed between neighboring stations, so they are neither bird's-fly distances nor track distances. See [Data notes](#data-notes).
- **All incidents are simulated.** Nothing reads a live service.

## Data notes

### Files

| File | Columns |
| --- | --- |
| `london_stations.csv` | `Station`, `OS X`, `OS Y`, `Latitude`, `Longitude`, `Zone`, `Postcode` |
| `london_connections.csv` | `Tube Line`, `From Station`, `To Station` |
| `london_lines.csv` | `Tube Line`, `Color` |

The lines file lists 13 lines: Bakerloo, Central, Circle, District, Hammersmith and City, Jubilee, Metropolitan, Northern, Piccadilly, Victoria, Waterloo and City, DLR and Tramlink. So "the whole network" includes the DLR and Tramlink.

### What the loaders do

- Column names and string values are stripped of whitespace.
- Connections are merged with the lines file (inner join), so a line missing from the lines file is dropped.
- Only stations that appear in a connection are kept. The stations CSV has 659 rows and 349 stations are used.
- Each of the 458 connections is stored in both directions, giving 916 relationships.
- On the Northern line alone, the GDS projection holds 52 stations and 106 relationships.

### Distances

Distances in the app are straight-line (great-circle, haversine) distances between neighboring stations, added up along the route (`route_km` in `app/core.py`, using each station's latitude and longitude from `london_stations.csv`). This is not one straight line from origin to destination: the route still passes through every station, so the total is longer than a bird's-fly figure. It is also not the length of the track, which curves between stations. The app uses the number to compare candidate routes, not as a published distance, and the talk quotes the TfL figures below instead. The app's figures were checked against TfL's inter-station running data (released under a Freedom of Information request, ref. FOI-0175-1314, 2013, at [Station to Station Journey Times](https://www.whatdotheyknow.com/request/station_to_station_journey_times). For the two Northern line branches the totals agree within 2.5%: 5.30 km against 5.43 km for the Charing Cross branch and 8.10 km against 8.07 km for the Bank branch. Single legs differ by up to about 0.3 km. The app's total is not always the smaller one (the Bank branch is 0.03 km longer in the app), so do not describe it as a strict lower bound. Coordinate precision in the station file is a likely cause of small differences, but this has not been checked. The spreadsheet is not included here and the running times quoted in the talk (about 14 minutes against about 18) come from it. The data are old, so cite the numbers only.

## Changing the journey or the network

- **A different journey on the same network.** In the app, use the sidebar. In Demo 2, change `ORIGIN`, `DESTINATION`, `LINES` and `K` in section 3. The scenarios and the sweep follow the routes the graph proposes. Check the pre-flight, then run each new scenario once in hosted mode so replay knows it.
- **Every line.** Clear the Lines selection in the app, or set `LINES = None` in Demo 2. Interchanges become part of the options and part of what Jev reads, which is where the graph earns its keep over a hardcoded list. Expect the proposed set of routes to vary (see [Variability](#variability-between-runs)).
- **More options.** Raise **Options for Jev** (maximum `routes.max_k`). `routes.pool` must stay at or above `max_k`.
- **A different network.** Replace the three CSVs with the same columns. Station names in the connections file must match the stations file exactly. The Demo 1 notebook is hardcoded to the two Northern line branches, so a new network needs Demo 2 or the app.
- **Different wording for Jev.** Edit the instructions and the option descriptions (`build_questions` and `describe` in `app/core.py`, or the matching cells in Demo 2). Wording changes the margins and the replay keys depend on the route labels, so re-record after changing them.
- **A different model.** Change `jev.hosted.model` in `config.yaml` (and `HOSTED_OPTIONS` or `HOSTED_MODEL` in the notebooks). Re-record the replay file and rerun the pre-flight. Replay entries record the model that produced them in `recorded_with`.

## Glossary

| Term | Meaning |
| --- | --- |
| Jev | TypeSafe AI's model that returns a typed choice with a probability for every option. "Typed" means the pick is always one of the options you listed, never free text. |
| `jev.decide` | The Neo4j function that asks Jev one question and returns the pick, the probabilities and metadata. |
| `jev.decideAll` | The batch version. The app's Single outage and Delay curve tabs use it. |
| GDS | Neo4j Graph Data Science, the plugin that provides Yen's k shortest paths. |
| Yen's algorithm | Finds the k shortest simple paths between two nodes. |
| Margin | Top probability minus the second. |
| Close call | A decision whose margin is below `app.low_margin` (0.2). |
| Pool | The short routes GDS finds before the distinct ones are kept. |
| Overlap | The fraction of stations two routes share. Above `max_overlap`, they count as the same route. |
| Interchange cost | Extra stops each interchange counts as when ranking routes. |
| Replay | Saved hosted decisions, shown with no network or model call. |
| Hosted, local | The hosted Jev API, or a small model (`tev1:4b`) running in Ollama. |
