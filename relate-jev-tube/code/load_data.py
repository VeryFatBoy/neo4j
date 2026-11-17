"""Build walk.cl from the three Tube CSVs: python load_data.py [--data ../data] [--db walk.cl]

Deletes the database file first, so it also resets the ledger. Every station starts running normally."""

import argparse
import os
import pandas as pd
import relate

from pathlib import Path
from walker import records

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(Path(__file__).resolve().parent.parent / "data"), help="folder with london_stations.csv, london_connections.csv, london_lines.csv")
    ap.add_argument("--db", default=os.environ.get("WALK_DB", "walk.cl"))
    args = ap.parse_args()
    data = Path(args.data)

    stations = pd.read_csv(data / "london_stations.csv")
    links = pd.read_csv(data / "london_connections.csv")
    lines = pd.read_csv(data / "london_lines.csv")
    for frame in (stations, links, lines):
        frame.columns = frame.columns.str.strip()

    # The connections file also holds other London rail lines. Keep the lines in the lines file, and the stations they touch.
    links = links[links["Tube Line"].str.strip().isin(set(lines["Tube Line"].str.strip()))]
    used = set(links["From Station"].str.strip()) | set(links["To Station"].str.strip())
    stations = stations[stations["Station"].str.strip().isin(used)]

    if args.db != ":memory:" and os.path.exists(args.db):
        os.remove(args.db)
    db = relate.connect(args.db)
    db.execute(
        "UNWIND $rows AS r MERGE (s:Station {name: r.name}) "
        "SET s.lat = r.lat, s.lon = r.lon, s.active = true, s.delay_minutes = 0",
        {"rows": [{"name": str(r["Station"]).strip(), "lat": float(r["Latitude"]), "lon": float(r["Longitude"])}
                  for r in stations.to_dict("records")]})
    db.execute(
        "UNWIND $rows AS r MATCH (a:Station {name: r.a}), (b:Station {name: r.b}) "
        "CREATE (a)-[:CONNECTS_TO {line: r.line}]->(b)",
        {"rows": [{"a": str(r["From Station"]).strip(), "b": str(r["To Station"]).strip(), "line": str(r["Tube Line"]).strip()}
                  for r in links.to_dict("records")]})
    n = records(db.execute("MATCH (s:Station) RETURN count(s) AS n"))[0]["n"]
    m = records(db.execute("MATCH ()-[r:CONNECTS_TO]->() RETURN count(r) AS n"))[0]["n"]
    db.close()
    print(f"Built {args.db}: {n} stations, {m} connections.")

if __name__ == "__main__":
    main()
