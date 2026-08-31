package sentiment;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.neo4j.driver.Driver;
import org.neo4j.driver.GraphDatabase;
import org.neo4j.driver.Session;
import org.neo4j.harness.Neo4j;
import org.neo4j.harness.Neo4jBuilders;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
public class SentimentableTest {

    private Neo4j embeddedDatabaseServer;
    private Driver driver;

    @BeforeAll
    void initializeNeo4j() {
        this.embeddedDatabaseServer = Neo4jBuilders.newInProcessBuilder()
                .withDisabledServer()
                .withFunction(Sentimentable.class)
                .build();
        this.driver = GraphDatabase.driver(embeddedDatabaseServer.boltURI());
    }

    @AfterAll
    void closeNeo4j() {
        this.driver.close();
        this.embeddedDatabaseServer.close();
    }

    @Test
    void scorePositiveSentence() {
        try (Session session = driver.session()) {
            var scores = session.run(
                "RETURN sentiment.score('The movie was great') AS scores"
            ).single().get("scores").asMap();

            assertTrue((Double) scores.get("compound") > 0.5);
            assertTrue((Double) scores.get("positive") > 0.0);
            assertEquals(0.0, (Double) scores.get("negative"));
        }
    }

    @Test
    void capitalizationIncreasesScore() {
        try (Session session = driver.session()) {
            var normal = session.run(
                "RETURN sentiment.score('The movie was great') AS scores"
            ).single().get("scores").asMap();

            var caps = session.run(
                "RETURN sentiment.score('The movie was GREAT!') AS scores"
            ).single().get("scores").asMap();

            assertTrue((Double) caps.get("compound") > (Double) normal.get("compound"));
        }
    }

    @Test
    void emptyStringReturnsNeutral() {
        try (Session session = driver.session()) {
            var scores = session.run(
                "RETURN sentiment.score('') AS scores"
            ).single().get("scores").asMap();

            assertEquals(0.0, (Double) scores.get("compound"));
            assertEquals(1.0, (Double) scores.get("neutral"));
        }
    }

    @Test
    void nullStringReturnsNeutral() {
        try (Session session = driver.session()) {
            var scores = session.run(
                "RETURN sentiment.score(null) AS scores"
            ).single().get("scores").asMap();

            assertEquals(0.0, (Double) scores.get("compound"));
            assertEquals(1.0, (Double) scores.get("neutral"));
        }
    }
}
