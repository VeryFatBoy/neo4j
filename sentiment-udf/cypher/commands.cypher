// Confirm the function loaded
SHOW FUNCTIONS
YIELD name
WHERE name STARTS WITH 'sentiment'
RETURN name;

// Run the tests
RETURN sentiment.score('The movie was great') AS scores;

RETURN sentiment.score('The movie was GREAT!') AS scores;

RETURN sentiment.score('') AS scores;

// Add constraints and indexes
CREATE CONSTRAINT tick_pk IF NOT EXISTS
    FOR (t:Tick) REQUIRE (t.symbol, t.ts) IS NODE KEY;

CREATE CONSTRAINT headline_id IF NOT EXISTS
    FOR (h:Headline) REQUIRE h.id IS UNIQUE;

CREATE CONSTRAINT stock_id IF NOT EXISTS
    FOR (s:Stock) REQUIRE s.symbol IS UNIQUE;

CREATE INDEX tick_symbol_ts IF NOT EXISTS
    FOR (t:Tick) ON (t.symbol, t.ts);

CREATE INDEX headline_symbol_ts IF NOT EXISTS
    FOR (h:Headline) ON (h.symbol, h.ts);

// Load the data
LOAD CSV WITH HEADERS FROM 'https://github.com/singlestore-cookbook/singlestore-cookbook.github.io/raw/refs/heads/main/code/part-ml/running-sentiment-analysis-inside-the-database-with-webassembly/datasets/fictitious_stocks.csv' AS row
CALL {
    WITH row
    MERGE (s:Stock {symbol: row.Name})
    CREATE (t:Tick {
        symbol: row.Name,
        ts:     date(row.date),
        open:   toFloat(row.open),
        high:   toFloat(row.high),
        low:    toFloat(row.low),
        close:  toFloat(row.close),
        volume: toInteger(row.volume)
    })
    CREATE (s)-[:HAS_TICK]->(t)
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'https://github.com/singlestore-cookbook/singlestore-cookbook.github.io/raw/refs/heads/main/code/part-ml/running-sentiment-analysis-inside-the-database-with-webassembly/datasets/raw_fictitious_headlines.csv' AS row
CALL {
    WITH row
    MATCH (s:Stock {symbol: row.symbol})
    WITH s, row, sentiment.score(row.headline) AS sc
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
} IN TRANSACTIONS OF 1000 ROWS;

// Headline-level sentiment
MATCH (h:Headline)
RETURN h.symbol             AS symbol,
       date(h.ts)           AS ts,
       left(h.headline, 30) AS headline,
       round(h.positive, 3) AS positive,
       round(h.negative, 3) AS negative,
       round(h.neutral,  3) AS neutral
ORDER BY h.symbol, h.ts
LIMIT 10;

// Aggregate sentiment by stock and day
MATCH (h:Headline)
WITH  h.symbol        AS symbol,
      date(h.ts)      AS ts,
      avg(h.positive) AS avg_positive,
      avg(h.negative) AS avg_negative,
      avg(h.neutral)  AS avg_neutral,
      count(h)        AS num_headlines
RETURN symbol,
       ts,
       round(avg_positive, 3) AS avg_positive,
       round(avg_negative, 3) AS avg_negative,
       round(avg_neutral,  3) AS avg_neutral,
       num_headlines
ORDER BY symbol, ts
LIMIT 10;

// Join sentiment with closing price
MATCH (t:Tick)<-[:HAS_TICK]-(s:Stock)-[:HAS_HEADLINE]->(h:Headline)
WHERE date(t.ts) = date(h.ts)
RETURN t.symbol             AS symbol,
       date(t.ts)           AS ts,
       round(t.close, 2)    AS close,
       round(h.positive, 3) AS positive,
       round(h.negative, 3) AS negative,
       round(h.neutral,  3) AS neutral
ORDER BY t.symbol, t.ts
LIMIT 10;

// Most positive headlines
MATCH (h:Headline)
RETURN h.symbol,
       date(h.ts)           AS ts,
       left(h.headline, 30) AS headline,
       round(h.positive, 3) AS positive
ORDER BY h.positive DESC
LIMIT 10;

// Most negative headlines
MATCH (h:Headline)
RETURN h.symbol,
       date(h.ts)           AS ts,
       left(h.headline, 30) AS headline,
       round(h.negative, 3) AS negative
ORDER BY h.negative DESC
LIMIT 10;

// Validate stored scores against live UDF calls
MATCH (h:Headline {symbol: 'BBRQ-FX'})
WITH  h, sentiment.score(h.headline) AS live
RETURN h.symbol             AS symbol,
       date(h.ts)           AS ts,
       left(h.headline, 30) AS headline,
       CASE
           WHEN round(h.positive, 3) = round(live.positive, 3)
            AND round(h.negative, 3) = round(live.negative, 3)
            AND round(h.neutral,  3) = round(live.neutral,  3)
           THEN 'match'
           ELSE 'not match'
       END AS comparison
LIMIT 10;

// Daily average sentiment vs closing price
MATCH (h:Headline)
WITH  h.symbol        AS symbol,
      date(h.ts)      AS ts,
      avg(h.positive) AS avg_positive,
      avg(h.negative) AS avg_negative,
      avg(h.neutral)  AS avg_neutral
MATCH (t:Tick {symbol: symbol})
WHERE date(t.ts) = ts
RETURN symbol,
       ts,
       round(t.close, 2)      AS daily_close,
       round(avg_positive, 3) AS avg_positive,
       round(avg_negative, 3) AS avg_negative,
       round(avg_neutral,  3) AS avg_neutral
ORDER BY symbol, ts
LIMIT 10;

// WARNING: Uncomment the lines below ONLY if you need to delete all data and reload from scratch.
// MATCH (n)
// CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 100 ROWS;