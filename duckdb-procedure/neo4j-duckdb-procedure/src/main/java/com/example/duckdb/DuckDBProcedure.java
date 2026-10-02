package com.example.duckdb;

import org.neo4j.procedure.Description;
import org.neo4j.procedure.Name;
import org.neo4j.procedure.Procedure;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.Statement;
import java.sql.Types;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

public class DuckDBProcedure {

    public static class QueryResult {
        public Map<String, Object> row;

        public QueryResult(Map<String, Object> row) {
            this.row = row;
        }
    }

    @Procedure(name = "duckdb.query")
    @Description("Run a DuckDB SQL query and return results as rows")
    public Stream<QueryResult> query(@Name("sql") String sql) throws Exception {
        List<QueryResult> results = new ArrayList<>();

        synchronized (DuckDBConnection.class) {
            Connection conn = DuckDBConnection.getInstance();
            try (Statement stmt = conn.createStatement()) {
                boolean hasResultSet = stmt.execute(sql);

                if (hasResultSet) {
                    try (ResultSet rs = stmt.getResultSet()) {
                        ResultSetMetaData meta = rs.getMetaData();
                        int columnCount = meta.getColumnCount();

                        while (rs.next()) {
                            Map<String, Object> row = new HashMap<>();
                            for (int i = 1; i <= columnCount; i++) {
                                String colName = meta.getColumnName(i);
                                int colType = meta.getColumnType(i);
                                String typeName = meta.getColumnTypeName(i);
                                Object value = mapValue(rs, i, colType, typeName);
                                row.put(colName, value);
                            }
                            results.add(new QueryResult(row));
                        }
                    }
                } else {
                    Map<String, Object> status = new HashMap<>();
                    status.put("status", "OK");
                    status.put("updateCount", (long) stmt.getUpdateCount());
                    results.add(new QueryResult(status));
                }
            }
        }

        return results.stream();
    }

    private Object mapValue(ResultSet rs, int index, int sqlType, String typeName) throws Exception {
        if (typeName != null && typeName.equalsIgnoreCase("HUGEINT")) {
            long val = rs.getLong(index);
            return rs.wasNull() ? null : val;
        }
        switch (sqlType) {
            case Types.TINYINT:
            case Types.SMALLINT:
            case Types.INTEGER: {
                long val = (long) rs.getInt(index);
                return rs.wasNull() ? null : val;
            }
            case Types.BIGINT: {
                long val = rs.getLong(index);
                return rs.wasNull() ? null : val;
            }
            case Types.FLOAT:
            case Types.REAL:
            case Types.DOUBLE:
            case Types.NUMERIC:
            case Types.DECIMAL: {
                double val = rs.getDouble(index);
                return rs.wasNull() ? null : val;
            }
            case Types.BOOLEAN:
            case Types.BIT: {
                boolean val = rs.getBoolean(index);
                return rs.wasNull() ? null : val;
            }
            case Types.NULL:
                return null;
            default:
                return rs.getString(index);
        }
    }
}
