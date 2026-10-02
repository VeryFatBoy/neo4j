# Running TypeSafe AI's Jev Inside Neo4j for Calibrated Sentiment Decisions

This is Part 3 of a series on running sentiment analysis inside Neo4j.

- Part 1, [Running Sentiment Analysis Inside Neo4j With a Java Plugin](https://dzone.com/articles/neo4j-sentiment-analysis-java), used the VADER lexicon via a pure Java UDF – rule-based, fully local, high throughput.
- Part 2, [Wasm Inside Neo4j: Building the Example That Didn't Exist](https://dzone.com/articles/wasm-inside-neo4j), embedded a Wasm runtime inside a Java UDF using `wasmtime-java` — sandboxed, still local, lower throughput.

Part 3 takes a different approach. Instead of bundling a scoring model inside the plugin JAR, we call TypeSafe AI's Jev from inside a Java UDF. Jev is a System One model – it doesn't generate text. It takes a state (the text to evaluate) and a set of typed questions and returns structured decisions with explicit probability and confidence information. No prompt engineering, no JSON schema coercion, no parsing.

The result is callable directly from Cypher:

```cypher
RETURN com.example.jev.sentiment('The movie was great!') AS result;
```

The full source code is available on [GitHub](https://github.com/VeryFatBoy/neo4j/tree/main/jev-udf).

## What Jev Gives You That VADER Doesn't

VADER gives you a compound score. For "The movie had some good moments but ultimately disappointed," it returns something around -0.3. That's a number, but it doesn't tell you how much to trust it.

Jev separates the decision from the uncertainty. For the same sentence, it returns:

```json
{
  "choice": "negative",
  "confidence": 1.0,
  "score": 1.55,
  "positive": 0.0,
  "negative": 1.0,
  "neutral": 0.0
}
```

The winning sentiment option has a confidence value of `1.0`. But the `score` — which is the result of the separate confidence question we asked Jev, rated on a scale from 0 to 2 — is `1.55`, landing between "Somewhat confident" and "Very confident." Jev is flagging uncertainty about a genuinely ambiguous sentence, and that information is actionable in a way that a raw score isn't.

For unambiguous text, Jev is equally unambiguous:

```json
{
  "choice": "positive",
  "confidence": 1.0,
  "score": 2.0,
  ...
}
```

This structured uncertainty output is the key differentiator of Part 3. If you need to act differently on low-confidence classifications — routing them to human review, flagging them for follow-up, or simply surfacing the uncertainty in a UI — VADER's scores don't provide a model-derived estimate of uncertainty about the classification. Jev returns an explicit confidence assessment alongside the decision. Confidence information should not be confused with accuracy: a model can be highly confident and still be wrong.

## What We're Building

A Neo4j Java UDF that calls the Jev API over HTTPS, passing the input text as the evaluation state and asking two typed questions: a sentiment classification (positive, negative, or neutral) and a confidence score. For non-blank input, the UDF returns a `Map<String, Object>` with six keys: `choice`, `confidence`, `score`, `positive`, `negative` and `neutral`. For blank input, it returns only `choice` and `confidence` as a guard response without calling the API.

No Wasm runtime, no bundled lexicon, no native libraries. The plugin has no Wasm runtime, native library, or bundled sentiment lexicon. Its only runtime dependency beyond Neo4j itself is Jackson, which is relocated into a private package namespace to avoid classpath conflicts with Neo4j's own Jackson dependencies.

## Prerequisites

- **Java 21**. OpenJDK 21 or later. Confirm with `java -version`.
- **Maven 3.9.6**. Install manually rather than via Homebrew on Apple Silicon — see [Part 2](https://dzone.com/articles/wasm-inside-neo4j) of this article series for the reason. Confirm with `mvn -version`.
- **Neo4j Desktop**. Download and install from [neo4j.com/download](https://neo4j.com/download/). We're using version 2026.07.0. Update the `neo4j.version` property in `pom.xml` to match your installation.
- **A Jev API key.** Sign up for access at [console.typesafe.ai](https://console.typesafe.ai). The key is passed to Neo4j via a JVM property in `neo4j.conf` — more on this in the deployment section.

## Getting the Code

The full source is on GitHub. Clone the repo and navigate to the `neo4j-jev-udf` directory:

```bash
cd ~
git clone --filter=blob:none --sparse https://github.com/VeryFatBoy/neo4j.git
cd neo4j
git sparse-checkout set jev-udf
mv jev-udf ../jev-udf
cd ../jev-udf/neo4j-jev-udf
```

## The Maven Project

The `pom.xml` has two things worth noting. First, `org.neo4j:neo4j` is declared as `provided` scope — Neo4j is already present in the database JVM at runtime. Second, we use `maven-shade-plugin` to relocate Jackson into a private package namespace. Neo4j ships its own version of Jackson internally, and without relocation, the two can conflict at runtime in ways that produce silent failures.

```xml
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.example</groupId>
    <artifactId>jev-neo4j-poc</artifactId>
    <version>1.0-SNAPSHOT</version>
    <packaging>jar</packaging>

    <properties>
        <maven.compiler.source>21</maven.compiler.source>
        <maven.compiler.target>21</maven.compiler.target>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
        <neo4j.version>2026.07.0</neo4j.version>
    </properties>

    <dependencies>
        <dependency>
            <groupId>org.neo4j</groupId>
            <artifactId>neo4j</artifactId>
            <version>${neo4j.version}</version>
            <scope>provided</scope>
        </dependency>
        <dependency>
            <groupId>com.fasterxml.jackson.core</groupId>
            <artifactId>jackson-databind</artifactId>
            <version>2.17.2</version>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <artifactId>maven-compiler-plugin</artifactId>
                <configuration>
                    <source>21</source>
                    <target>21</target>
                </configuration>
            </plugin>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-shade-plugin</artifactId>
                <version>3.5.1</version>
                <executions>
                    <execution>
                        <phase>package</phase>
                        <goals><goal>shade</goal></goals>
                        <configuration>
                            <artifactSet>
                                <excludes>
                                    <exclude>org.neo4j:*</exclude>
                                </excludes>
                            </artifactSet>
                            <relocations>
                                <relocation>
                                    <pattern>com.fasterxml.jackson</pattern>
                                    <shadedPattern>com.example.jev.shaded.jackson</shadedPattern>
                                </relocation>
                            </relocations>
                            <shadedArtifactAttached>false</shadedArtifactAttached>
                        </configuration>
                    </execution>
                </executions>
            </plugin>
        </plugins>
    </build>
</project>
```

Update the `neo4j.version` property to match your own Neo4j Desktop installation.

## The Java UDF

The UDF uses Java's built-in `HttpClient` — no third-party HTTP library needed. The Jev API key is read first from the environment variable `TYPESAFE_API_KEY`, then from a JVM system property of the same name as a fallback. The fallback matters because Neo4j Desktop runs as a separate process and doesn't inherit your shell's environment variables; we'll set the JVM property in `neo4j.conf` in the deployment section.

```java
package com.example;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.neo4j.procedure.Description;
import org.neo4j.procedure.Name;
import org.neo4j.procedure.UserFunction;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.HashMap;
import java.util.Map;

public class JevUDF {

    private static final String API_URL = "https://api.typesafe.ai/v1/systemone";
    private static final ObjectMapper mapper = new ObjectMapper();
    private static final HttpClient client = HttpClient.newHttpClient();

    private static String getApiKey() {
        String key = System.getenv("TYPESAFE_API_KEY");
        if (key == null || key.isBlank()) {
            key = System.getProperty("TYPESAFE_API_KEY");
        }
        if (key == null || key.isBlank()) {
            throw new RuntimeException("TYPESAFE_API_KEY not set as environment variable or JVM property");
        }
        return key;
    }

    @UserFunction("com.example.jev.sentiment")
    @Description("Classifies text sentiment using Jev (TypeSafe AI). Returns choice, probabilities and confidence.")
    public Map<String, Object> sentiment(@Name("text") String text) throws Exception {

        if (text == null || text.isBlank()) {
            Map<String, Object> empty = new HashMap<>();
            empty.put("choice", "neutral");
            empty.put("confidence", 0.0);
            return empty;
        }

        String apiKey = getApiKey();

        ObjectNode body = mapper.createObjectNode();
        body.put("model", "jev-latest");
        body.put("state", text);

        ObjectNode questions = mapper.createObjectNode();

        ObjectNode sentiment = mapper.createObjectNode();
        sentiment.put("type", "choice");
        sentiment.put("instructions", "What is the overall sentiment of this text?");
        ObjectNode criteria = mapper.createObjectNode();
        criteria.put("positive", "Text expresses positive sentiment");
        criteria.put("negative", "Text expresses negative sentiment");
        criteria.put("neutral",  "Text is neutral or factual");
        sentiment.set("criteria", criteria);
        questions.set("sentiment", sentiment);

        ObjectNode confidence = mapper.createObjectNode();
        confidence.put("type", "score");
        confidence.put("instructions", "How confident are you in the sentiment classification?");
        ArrayNode levels = mapper.createArrayNode();
        levels.add("Not confident");
        levels.add("Somewhat confident");
        levels.add("Very confident");
        confidence.set("criteria", levels);
        questions.set("confidence", confidence);

        body.set("questions", questions);

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(API_URL))
                .header("Content-Type", "application/json")
                .header("Authorization", "Bearer " + apiKey)
                .POST(HttpRequest.BodyPublishers.ofString(mapper.writeValueAsString(body)))
                .build();

        HttpResponse<String> response = client.send(request,
                HttpResponse.BodyHandlers.ofString());

        if (response.statusCode() != 200) {
            throw new RuntimeException("Jev API error " + response.statusCode()
                    + ": " + response.body());
        }

        JsonNode result = mapper.readTree(response.body());
        JsonNode answers = result.get("answers");
        JsonNode sentimentAnswer = answers.get("sentiment");
        JsonNode confidenceAnswer = answers.get("confidence");

        Map<String, Object> output = new HashMap<>();
        output.put("choice",     sentimentAnswer.get("choice").asText());
        output.put("confidence", sentimentAnswer.get("confidence").asDouble());
        output.put("score",      confidenceAnswer.get("score").asDouble());
        output.put("positive",   sentimentAnswer.get("probabilities").get("positive").asDouble());
        output.put("negative",   sentimentAnswer.get("probabilities").get("negative").asDouble());
        output.put("neutral",    sentimentAnswer.get("probabilities").get("neutral").asDouble());
        return output;
    }
}
```

A few things worth noting in the Jev request body. The `choice` question uses a `criteria` object — a map of option names to descriptions. The `score` question uses a `criteria` array — an ordered list of level descriptions. These are different shapes, and mixing them up produces a `422` error from the API. Both questions are answered in a single API call; Jev evaluates them in parallel against the same state.

## Deploying to Neo4j

Build the JAR:

```bash
mvn -q clean package
```

Stop Neo4j in Desktop, then copy the JAR to your plugins folder:

```bash
cp target/jev-neo4j-poc-1.0-SNAPSHOT.jar \
  ~/Library/Application\ Support/neo4j-desktop/Application/Data/dbmss//plugins/
```

On Linux, the plugins folder is typically under `$NEO4J_HOME/plugins/`. On Windows, the plugins folder is typically under `%NEO4J_HOME%\plugins\`.

Edit `neo4j.conf` in the `conf/` folder of the same dbms directory and add three lines:

```
dbms.security.procedures.unrestricted=com.example.jev.\*
dbms.security.procedures.allowlist=com.example.jev.\*
server.jvm.additional=-DTYPESAFE\_API\_KEY=
```

Two lines are shown in the security configuration. `allowlist` controls which extension functions are loaded and makes the function callable from Cypher. `unrestricted` gives matching extensions full database access, bypassing Neo4j's security restrictions and should be used only where necessary. For this example, both settings are configured for the `com.example.jev.*` namespace; use the narrowest configuration that your Neo4j version and deployment require.

The `server.jvm.additional` line passes the API key to Neo4j's JVM as a system property. For this local Neo4j Desktop example, that's a practical way to make the key available without modifying the UDF code. The Neo4j Desktop process does not automatically receive environment variables exported in your terminal session, so the JVM property is the reliable alternative. Do not commit this value to source control. For production, use your deployment environment's secret-management mechanism and inject the value into the Neo4j process without storing the secret in a tracked configuration file.

Restart Neo4j in Desktop, then select **Query** from the left-hand navigation pane, connect to your instance, and confirm the function is registered:

```cypher
SHOW FUNCTIONS
YIELD name
WHERE name STARTS WITH 'com.example'
RETURN name;
```

Expected: `"com.example.jev.sentiment"`

## Results

### Positive Sentence

```cypher
RETURN com.example.jev.sentiment('The movie was great!') AS result;
```

Expected:

```json
{
  "choice": "positive",
  "confidence": 1.0,
  "score": 2.0,
  "positive": 1.0,
  "negative": 0.0,
  "neutral": 0.0
}
```

### Negative Sentence

```cypher
RETURN com.example.jev.sentiment('The movie was absolutely terrible.') AS result;
```

Expected:

```json
{
  "choice": "negative",
  "confidence": 1.0,
  "score": 2.0,
  "positive": 0.0,
  "negative": 1.0,
  "neutral": 0.0
}
```

### Mixed Sentiment

```cypher
RETURN com.example.jev.sentiment('The movie had some good moments but ultimately disappointed.') AS result;
```

Expected:

```json
{
  "choice": "negative",
  "confidence": 1.0,
  "score": 1.55,
  "positive": 0.0,
  "negative": 1.0,
  "neutral": 0.0
}
```

The winning sentiment option has a confidence value of `1.0`. But the `score` drops to `1.55` out of `2` – somewhere between "Somewhat confident" and "Very confident." This is structured uncertainty output, and it's what distinguishes Jev from a rule-based scorer.

There are two different quantities here. `confidence` belongs to the sentiment choice itself: it describes the probability concentration of the classification. `score` comes from a separate question that explicitly asks Jev how confident it is in that classification. They answer different questions and should not be interpreted as two versions of the same confidence value.

### Blank-Input Guard

```cypher
RETURN com.example.jev.sentiment('') AS result;
```

Expected:

```json
{
  "choice": "neutral",
  "confidence": 0.0
}
```

The blank-input guard returns immediately without making an API call. Here `neutral` is a guard value rather than a Jev classification: no sentiment analysis is performed for blank or whitespace-only input.

## Scalability and Limitations

Every call to `com.example.jev.sentiment()` makes an outbound HTTPS request to the Jev API. That shapes where this approach fits and where it doesn't.

**Latency**.Jev responds in 70-500ms per call. For an occasional interactive request — a customer submitting a review, a user looking up a specific node — that latency may be acceptable. For a batch pipeline, if 1,000 calls are made sequentially at a 200ms average, that's over three minutes of API time; concurrent execution changes the wall-clock time but introduces its own rate-limit and resource considerations.

**Cost**. At the pricing available when this article was written, input tokens cost approximately $0.042 per million and output tokens were free. The test requests in this article averaged approximately 381 input tokens per call, giving a per-call cost of around $0.000016 (381 ÷ 1,000,000 × $0.042). Pricing is subject to change; check TypeSafe AI's current pricing before estimating production cost. At this workload size, cost is unlikely to be the primary constraint; latency, throughput, network dependency, and data residency are more significant considerations. At that rate, 1,000 calls would cost roughly $0.016, while one million calls would cost roughly $16, assuming the same average input size.

**Network dependency**. Parts 1 and 2 of this article series are fully local – no network, no external dependency, no data leaving the database. Part 3 introduces all three. The UDF itself runs inside Neo4j, but the inference does not: the text crosses the network boundary to TypeSafe AI, and only the structured result returns to Neo4j. If the Jev API is unreachable, the UDF throws. In a production deployment, that's an operational risk worth planning for: the `HttpClient` in this example has no explicit timeout configured; in production, set a connect timeout and a per-request timeout to prevent a slow or unresponsive API from blocking Neo4j worker threads indefinitely. A fallback return value and monitoring on the call path are also worth considering. Note also that the UDF makes a synchronous blocking HTTP call inside database query execution; concurrent Cypher invocations increase pressure on Neo4j worker threads, the API's rate limits, and the network.

**Data residency**. Text passed to `com.example.jev.sentiment()` leaves Neo4j and is sent to TypeSafe AI's API. If your data residency requirements prohibit that, this approach isn't suitable. Review TypeSafe AI's data handling policies before deploying in a regulated environment.

**Caching as a mitigation**. For workloads where the same text is scored repeatedly, results can be stored as node properties on first call and read directly on subsequent queries — skipping the API entirely. That's a natural pattern in Neo4j and largely eliminates the latency and cost concerns for stable datasets.

**The right use case.** This approach is best suited to low-frequency, interactive workloads where explicit uncertainty information matters more than throughput and where data leaving the database is acceptable. For high-volume batch ingestion, Part 1 (the Java VADER UDF) remains a strong fit — local, fast, no network dependency. Part 3 trades local throughput and offline operation for a richer decision output and explicit uncertainty information.

## Comparing the Three Approaches

|  | Part 1:  Java VADER | Part 2:  Wasm/VADER | Part 3:  Jev |
| --- | --- | --- | --- |
| **Output** | Polarity scores (float) | Polarity scores (float) | Typed decision + explicit uncertainty information |
| **Location** | Fully local | Fully local | Network (TypeSafe AI API) |
| **Latency** | < 1ms | 50-200ms (init) | 70-500ms |
| **Cost** | Free | Free | ~$0.000016/call |
| **Offline** | Yes | Yes | No |
| **Execution boundary** | Java UDF | Wasm runtime | External API |
| **Uncertainty output** | No | No | Yes |
| **Typical fit** | High-volume batch | Sandboxed local inference | Interactive, uncertainty-aware decisions |

## Summary

We added a third option to the series: calling TypeSafe AI's Jev from inside a Neo4j Java UDF, returning a typed sentiment decision with explicit uncertainty information directly from Cypher.

The implementation has the smallest inference footprint of the three – no Wasm runtime, no bundled lexicon, no native libraries. The JAR contains only the UDF class, a relocated Jackson, and Java's built-in `HttpClient`. The main configuration consideration is passing the API key to Neo4j's JVM via `server.jvm.additional` in `neo4j.conf`, since the Neo4j Desktop process does not automatically receive environment variables exported in your terminal session.

The key contribution of Part 3 isn't the mechanics — it's the explicit uncertainty output. For clear-cut sentences, Jev is unambiguous. For genuinely mixed sentiment, it returns both the decision and a structured confidence measure that Cypher can act on directly. That's something neither VADER nor the Wasm implementation provides.

The full source code is available on [GitHub](https://github.com/VeryFatBoy/neo4j/tree/main/jev-udf).