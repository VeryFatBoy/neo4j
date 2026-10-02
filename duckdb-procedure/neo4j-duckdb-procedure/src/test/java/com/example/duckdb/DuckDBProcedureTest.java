package com.example.duckdb;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
public class DuckDBProcedureTest {

    private Connection conn;

    @TempDir
    Path tempDir;

    @BeforeAll
    void setUp() throws Exception {
        conn = DuckDBConnection.getInstance();
    }

    @AfterAll
    void tearDown() throws Exception {
        if (conn != null && !conn.isClosed()) {
            conn.close();
        }
    }

    private String resourcePath(String filename) {
        return getClass().getClassLoader().getResource(filename).getPath();
    }

    private List<Map<String, Object>> runQuery(String sql) throws Exception {
        List<Map<String, Object>> results = new ArrayList<>();
        try (Statement stmt = conn.createStatement()) {
            boolean hasResultSet = stmt.execute(sql);
            if (hasResultSet) {
                try (ResultSet rs = stmt.getResultSet()) {
                    var meta = rs.getMetaData();
                    int cols = meta.getColumnCount();
                    while (rs.next()) {
                        Map<String, Object> row = new HashMap<>();
                        for (int i = 1; i <= cols; i++) {
                            String typeName = meta.getColumnTypeName(i);
                            int sqlType = meta.getColumnType(i);
                            row.put(meta.getColumnName(i),
                                    mappedValue(rs, i, sqlType, typeName));
                        }
                        results.add(row);
                    }
                }
            } else {
                Map<String, Object> status = new HashMap<>();
                status.put("status", "OK");
                status.put("updateCount", (long) stmt.getUpdateCount());
                results.add(status);
            }
        }
        return results;
    }

    private Object mappedValue(ResultSet rs, int i, int sqlType, String typeName) throws Exception {
        if (typeName != null && typeName.equalsIgnoreCase("HUGEINT")) {
            long val = rs.getLong(i);
            return rs.wasNull() ? null : val;
        }
        switch (sqlType) {
            case java.sql.Types.TINYINT:
            case java.sql.Types.SMALLINT:
            case java.sql.Types.INTEGER: {
                long val = (long) rs.getInt(i);
                return rs.wasNull() ? null : val;
            }
            case java.sql.Types.BIGINT: {
                long val = rs.getLong(i);
                return rs.wasNull() ? null : val;
            }
            case java.sql.Types.FLOAT:
            case java.sql.Types.REAL:
            case java.sql.Types.DOUBLE:
            case java.sql.Types.NUMERIC:
            case java.sql.Types.DECIMAL: {
                double val = rs.getDouble(i);
                return rs.wasNull() ? null : val;
            }
            case java.sql.Types.BOOLEAN:
            case java.sql.Types.BIT: {
                boolean val = rs.getBoolean(i);
                return rs.wasNull() ? null : val;
            }
            case java.sql.Types.NULL:
                return null;
            default:
                return rs.getString(i);
        }
    }

    // 1. Basic range query
    @Test
    void rangeQueryReturnsCorrectRows() throws Exception {
        var results = runQuery("SELECT range AS num FROM range(1, 6)");
        assertEquals(5, results.size());
        assertEquals(1L, results.get(0).get("num"));
        assertEquals(5L, results.get(4).get("num"));
    }

    // 2. CSV file query
    @Test
    void csvQueryReturnsCorrectRows() throws Exception {
        String path = resourcePath("test.csv");
        var results = runQuery("SELECT city, country, population FROM read_csv_auto('" + path + "') ORDER BY population DESC");
        assertEquals(5, results.size());
        assertEquals("Tokyo", results.get(0).get("city"));
        assertEquals(13960000L, results.get(0).get("population"));
    }

    // 3. Parquet write and read round-trip
    @Test
    void parquetWriteAndReadRoundTrip() throws Exception {
        String csvPath = resourcePath("test.csv");
        String parquetPath = tempDir.resolve("test.parquet").toString();
        runQuery("COPY (SELECT * FROM read_csv_auto('" + csvPath + "')) TO '" + parquetPath + "' (FORMAT PARQUET)");
        var results = runQuery("SELECT city, population FROM read_parquet('" + parquetPath + "') ORDER BY population DESC");
        assertEquals(5, results.size());
        assertEquals("Tokyo", results.get(0).get("city"));
    }

    // 4. JSON file query
    @Test
    void jsonQueryReturnsCorrectRows() throws Exception {
        String path = resourcePath("test.json");
        var results = runQuery("SELECT city, country, population FROM read_json_auto('" + path + "') ORDER BY population DESC");
        assertEquals(5, results.size());
        assertEquals("Tokyo", results.get(0).get("city"));
    }

    // 5. Aggregation returns numeric values, not strings
    @Test
    void aggregationReturnsNumericValues() throws Exception {
        String path = resourcePath("test.csv");
        var results = runQuery("SELECT country, SUM(population) AS total FROM read_csv_auto('" + path + "') GROUP BY country ORDER BY total DESC");
        assertEquals(5, results.size());
        assertInstanceOf(Long.class, results.get(0).get("total"));
    }

    // 6. NULL values return as null, not zero
    @Test
    void nullNumericValueReturnsNull() throws Exception {
        String path = resourcePath("test_nulls.csv");
        var results = runQuery("SELECT city, country, population FROM read_csv_auto('" + path + "')");
        Map<String, Object> berlin = results.stream()
            .filter(r -> "Berlin".equals(r.get("city")))
            .findFirst()
            .orElseThrow();
        assertNull(berlin.get("population"));
    }

    // 7. Float values return as doubles
    @Test
    void floatValuesReturnAsDoubles() throws Exception {
        String path = resourcePath("test_floats.csv");
        var results = runQuery("SELECT city, latitude, longitude FROM read_csv_auto('" + path + "') WHERE city = 'London'");
        assertEquals(1, results.size());
        assertEquals(51.5074, (Double) results.get(0).get("latitude"), 0.0001);
        assertEquals(-0.1278, (Double) results.get(0).get("longitude"), 0.0001);
    }

    // 8. Boolean values return correctly
    @Test
    void booleanValuesReturnCorrectly() throws Exception {
        String path = resourcePath("test_booleans.csv");
        var results = runQuery("SELECT city, is_capital, is_coastal FROM read_csv_auto('" + path + "') WHERE city = 'Tokyo'");
        assertEquals(1, results.size());
        assertTrue((Boolean) results.get(0).get("is_capital"));
        assertTrue((Boolean) results.get(0).get("is_coastal"));
    }

    // 9. Empty result set returns zero rows cleanly
    @Test
    void emptyResultSetReturnsZeroRows() throws Exception {
        String path = resourcePath("test.csv");
        var results = runQuery("SELECT city FROM read_csv_auto('" + path + "') WHERE population > 999999999");
        assertEquals(0, results.size());
    }

    // 10. Bad SQL surfaces an error
    @Test
    void badSqlSurfacesError() {
        assertThrows(Exception.class, () ->
            runQuery("SELECT * FROM table_that_does_not_exist")
        );
    }

    // 11. In-memory table persists across calls
    @Test
    void inMemoryTablePersistsAcrossCalls() throws Exception {
        runQuery("CREATE TABLE IF NOT EXISTS landmarks (city VARCHAR, name VARCHAR, year_built INTEGER)");
        runQuery("INSERT INTO landmarks VALUES ('London', 'Tower Bridge', 1894), ('Paris', 'Eiffel Tower', 1889)");
        var results = runQuery("SELECT * FROM landmarks ORDER BY year_built");
        assertEquals(2, results.size());
        assertEquals("Paris", results.get(0).get("city"));
        assertEquals(1889L, results.get(0).get("year_built"));
    }

    // 12. DuckDB-side JOIN between two files
    @Test
    void duckdbSideJoinBetweenTwoFiles() throws Exception {
        String csvPath = resourcePath("test.csv");
        String tzPath = resourcePath("test_timezones.csv");
        var results = runQuery(
            "SELECT p.city, p.population, t.timezone FROM read_csv_auto('" + csvPath + "') p " +
            "JOIN read_csv_auto('" + tzPath + "') t ON p.city = t.city ORDER BY p.population DESC"
        );
        assertEquals(5, results.size());
        assertEquals("Tokyo", results.get(0).get("city"));
        assertEquals("JST", results.get(0).get("timezone"));
    }
}
