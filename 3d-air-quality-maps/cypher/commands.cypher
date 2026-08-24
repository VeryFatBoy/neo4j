// AQI Leaderboard
MATCH (c:City)-[:HAS_READING]->(r:Reading)
WITH c, r ORDER BY r.timestamp DESC
WITH c, collect(r)[0] AS latest
RETURN c.name AS city, c.country AS country,
       latest.aqi_us AS aqi_us, latest.aqi_category AS category
ORDER BY latest.aqi_us DESC;

// Cross-Border Neighbors
MATCH (a:City)-[n:NEIGHBORS]-(b:City)
WHERE a.country = 'France'
  AND b.country = 'Spain'
MATCH (a)-[:HAS_READING]->(ra:Reading)
MATCH (b)-[:HAS_READING]->(rb:Reading)
WITH a, b, n, ra, rb ORDER BY ra.timestamp DESC, rb.timestamp DESC
WITH a, b, n, collect(ra)[0] AS latest_a, collect(rb)[0] AS latest_b
RETURN a.name AS city_france, latest_a.aqi_us AS aqi_france,
       b.name AS city_spain,   latest_b.aqi_us AS aqi_spain,
       n.distance_km AS distance_km
ORDER BY n.distance_km;

// Most Connected City
MATCH (c:City)-[:NEIGHBORS]-()
RETURN c.name AS city, c.country AS country, count(*) AS neighbors
ORDER BY neighbors DESC
LIMIT 10;

// AQI Summary by Country
MATCH (c:City)-[:HAS_READING]->(r:Reading)
WITH c, r ORDER BY r.timestamp DESC
WITH c, collect(r)[0] AS latest
RETURN c.country AS country,
       round(avg(latest.aqi_us)) AS avg_aqi,
       min(latest.aqi_us) AS min_aqi,
       max(latest.aqi_us) AS max_aqi,
       count(c) AS cities
ORDER BY avg_aqi DESC;

// Pollution Corridors
MATCH (a:City)-[n:NEIGHBORS]-(b:City)
WHERE id(a) < id(b)
MATCH (a)-[:HAS_READING]->(ra:Reading)
MATCH (b)-[:HAS_READING]->(rb:Reading)
WITH a, b, n, ra, rb ORDER BY ra.timestamp DESC, rb.timestamp DESC
WITH a, b, n, collect(ra)[0] AS latest_a, collect(rb)[0] AS latest_b
WHERE latest_a.aqi_us > 50 AND latest_b.aqi_us > 50
RETURN a.name AS city_a, latest_a.aqi_us AS aqi_a,
       b.name AS city_b, latest_b.aqi_us AS aqi_b,
       n.distance_km AS distance_km
ORDER BY (latest_a.aqi_us + latest_b.aqi_us) DESC;

// Sharp Gradients
MATCH (a:City)-[n:NEIGHBORS]-(b:City)
MATCH (a)-[:HAS_READING]->(ra:Reading)
MATCH (b)-[:HAS_READING]->(rb:Reading)
WITH a, b, n, ra, rb ORDER BY ra.timestamp DESC, rb.timestamp DESC
WITH a, b, n, collect(ra)[0] AS latest_a, collect(rb)[0] AS latest_b
WHERE latest_a.aqi_us > 80 AND latest_b.aqi_us < 40
RETURN a.name AS higher_aqi_city, latest_a.aqi_us AS aqi,
       b.name AS lower_aqi_neighbor, latest_b.aqi_us AS neighbor_aqi,
       n.distance_km AS distance_km
ORDER BY n.distance_km;

// AQI Change Over Time
MATCH (c:City)-[:HAS_READING]->(r:Reading)
WITH c, r ORDER BY r.timestamp
WITH c, collect(r) AS readings
WHERE size(readings) > 1
RETURN c.name AS city, c.country AS country,
       readings[0].aqi_us AS first_aqi,
       readings[-1].aqi_us AS latest_aqi,
       readings[-1].aqi_us - readings[0].aqi_us AS change,
       size(readings) AS total_readings
ORDER BY abs(readings[-1].aqi_us - readings[0].aqi_us) DESC;

