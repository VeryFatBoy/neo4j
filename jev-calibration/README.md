# Jev calibration study

When Jev says it's 95% sure, is it right 95% of the time? Three standalone notebooks test that on synthetic fraud data, with Neo4j holding every decision.

| Notebook | What it does |
|---|---|
| `01_data_and_probe.ipynb` | Generates 500 synthetic transactions with hidden fraud labels. Optional live probe of the Jev API. |
| `02_decisions_in_neo4j.ipynb` | Resets the database, loads the transactions and stores a `Decision` node for each one. |
| `03_calibration_analysis.ipynb` | Reads everything from Neo4j. Converts Jev's margin to the probability of the chosen option, then computes reliability, ECE, Brier score, AUROC, a threshold sweep (including a split by clear and ambiguous scenarios) and a per-scenario breakdown. |

```
(:Transaction)-[:HAS_DECISION]->(:Decision)
```

The `data/` folder holds the files the notebooks share:

```
data/
  transactions.csv   500 synthetic transactions with hidden labels (written by notebook 1)
  decisions.csv      the stored Jev run that notebook 2 imports by default
```

## Run it

Set these before launching Jupyter:

```shell
export NEO4J_URI="neo4j+s://xxxx.databases.neo4j.io"
export NEO4J_USERNAME="your-username"
export NEO4J_PASSWORD="your-password"
export NEO4J_DATABASE="your-database"
export TYPESAFE_API_KEY="your-api-key"   # only for live calls
```

Run the notebooks in order. Each one installs its own pinned libraries.

## With or without a Jev key

Notebook 2 has two switches in its Configuration cell:

- `USE_SHIPPED_DECISIONS = True` (default) imports `data/decisions.csv` into Neo4j. No key needed and everyone analyzes the same stored run.
- `USE_SHIPPED_DECISIONS = False` calls Jev for every transaction, then exports the results to `data/decisions.csv`.
- `RESET_DATABASE = True` (default) removes all data, relationships, indexes and constraints first, so a free Aura database starts fresh.

Both CSV files in `data/` are included. Notebook 1 uses a fixed random seed, so running it again regenerates the same `transactions.csv`. Live calls give slightly different decisions each time, so every number in the article comes from the stored `decisions.csv`. A live run never overwrites that file: if `data/decisions.csv` exists, notebook 2 exports to `data/decisions_live_<timestamp>.csv` instead.

**Warning:** notebook 2 wipes the database it connects to by default. Point it at a database you don't mind clearing, or set `RESET_DATABASE = False`.

## Caveats

- The labels are synthetic, so "correct" means agreeing with the generator.
- The stored run used `jev-1.13.0`. Newer models, such as `jev-1.14.0`, may calibrate differently.
- About 38% of the transactions are fraud, far above real-world rates, so accuracy and error rates won't carry over.
- `config_hash` identifies a configuration (model, question, options, fields), not a single execution. Running the same configuration again stores nothing new, so keep a CSV export of any run you want to compare.
- Jev doesn't know the generator's base rates, so some of the measured overconfidence is a mismatch between Jev's assumptions and this generator's.
- Decisions at the very top of the range (0.99 and above) are mostly easy cases. In the ambiguous scenarios, decisions at 0.90 or above are right far less often.
- Jev's `confidence` is the margin between the two options' probabilities, not the probability of being right. Notebook 3 converts it to the probability of the chosen option, `(1 + confidence) / 2`.
- The self-rating is Jev's own report, not a measured probability.
- Jev rounds its probabilities to two decimals, so the signals are coarse.
- Results vary between runs, so the shipped file is one stored run.
