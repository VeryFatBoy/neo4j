# data_loader.py
import pandas as pd

from neo4j import GraphDatabase
from tqdm import tqdm

URI      = "bolt://localhost:7687"
AUTH     = ("neo4j", "your_password_here")
TICK_CSV = "fictitious_stocks.csv"
RAW_CSV  = "raw_fictitious_headlines.csv"

driver = GraphDatabase.driver(URI, auth = AUTH)

def chunks(df, size):
    for i in range(0, len(df), size):
        yield df.iloc[i:i+size].to_dict("records")

# load tick data
tick_df = (pd.read_csv(TICK_CSV)
             .dropna()
             .query("volume <= 2_147_483_647")
             .rename(columns = {"date": "ts", "Name": "symbol"})
             .sort_values(["ts", "symbol"]))

tick_batches = list(chunks(tick_df, 1000))
print(f"Loading {len(tick_df):,} tick rows in {len(tick_batches)} batches...")

with driver.session() as session:
    for batch in tqdm(tick_batches, desc = "Ticks", unit = "batch"):
        session.run("""
            UNWIND $rows AS row
            MERGE  (s:Stock {symbol: row.symbol})
            CREATE (t:Tick  {symbol: row.symbol, ts: date(row.ts),
                             open: row.open, high: row.high,
                             low:  row.low,  close: row.close,
                             volume: toInteger(row.volume)})
            CREATE (s)-[:HAS_TICK]->(t)
        """, rows = batch)

# load headlines and score at ingestion time
raw_df = pd.read_csv(RAW_CSV)
raw_batches = list(chunks(raw_df, 1000))
print(f"Loading {len(raw_df):,} headline rows in {len(raw_batches)} batches...")

with driver.session() as session:
    for batch in tqdm(raw_batches, desc = "Headlines", unit = "batch"):
        session.run("""
            UNWIND $rows AS row
            MATCH  (s:Stock {symbol: row.symbol})
            WITH   s, row, sentiment.score(row.headline) AS sc
            CREATE (h:Headline {
                id:        randomUUID(),
                symbol:    row.symbol,
                ts:        datetime(row.ts),
                headline:  row.headline,
                url:       row.url,
                publisher: row.publisher,
                compound:  sc.compound,
                positive:  sc.positive,
                negative:  sc.negative,
                neutral:   sc.neutral
            })
            CREATE (s)-[:HAS_HEADLINE]->(h)
        """, rows = batch)

print("Done.")
driver.close()