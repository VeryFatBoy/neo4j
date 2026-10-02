package com.example.duckdb;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;

public class DuckDBConnection {

    private static volatile Connection instance;

    private DuckDBConnection() {}

    public static Connection getInstance() throws SQLException {
        if (instance == null || instance.isClosed()) {
            synchronized (DuckDBConnection.class) {
                if (instance == null || instance.isClosed()) {
                    instance = DriverManager.getConnection("jdbc:duckdb:");
                }
            }
        }
        return instance;
    }
}
