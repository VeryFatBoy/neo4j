# wasm-udf

Source code for the DZone article [Wasm Inside Neo4j: Building the Example That Didn't Exist](https://dzone.com/articles/wasm-inside-neo4j).

This repo demonstrates embedding a `wasmtime` Wasm runtime inside a Neo4j Java UDF using `wasmtime-java`. A Rust function compiled to WebAssembly rides inside the plugin JAR alongside the Java code, callable directly from Cypher.

## Repo Structure

```
wasm-udf/
├── sentimentable/          -- Rust crate that compiles to Wasm
│   ├── Cargo.toml
│   ├── src/
│   │   └── lib.rs
│   └── wit/
│       └── sentimentable.wit
└── neo4j-wasm-udf/         -- Maven project hosting the Neo4j UDF
    ├── pom.xml
    └── src/
        └── main/
            ├── java/
            │   └── com/example/
            │       ├── WasmUDF.java
            │       └── SentimentUDF.java
            └── resources/
                ├── add.wat
                ├── add.wasm
                └── sentimentable.wasm
```

## Version Summary

| Component | Version |
|---|---|
| OpenJDK | 21.0.12.1 |
| Maven | 3.9.6 |
| Rust | 1.96.0 |
| WABT | 1.0.41 |
| wit-bindgen CLI | 0.59.0 |
| wit-bindgen crate | 0.40.0 |
| vader_sentiment crate | 0.1.1 |
| wasmtime-java | 0.19.0 |
| Neo4j | 2026.07.0 |

**Note:** `wasmtime-java` 0.19.0 is a community JNI binding, not an official Bytecode Alliance product. The API used here is version-specific; newer releases or alternative JVM Wasm runtimes may expose different interfaces.

## Reproduction Checklist

This checklist covers every command needed to reproduce the article on macOS Apple Silicon. For explanations of each step, refer to the main article.

Replace `<your-dbms-id>` throughout with the UUID of your Neo4j Desktop database instance. You can find it by looking at the path under your database in Neo4j Desktop, or by running:

```bash
ls ~/Library/Application\ Support/neo4j-desktop/Application/Data/dbmss/
```

---

### Prerequisites

1. Confirm Java 21:
```bash
java -version
```
Expected: `openjdk version "21..."`

2. Confirm Maven is using Java 21:
```bash
mvn -version
```
Expected: `Java version: 21`

3. Confirm Rust:
```bash
rustc --version
```
Expected: `rustc 1.96.0`

4. Confirm wasm32-wasip1 target is installed:
```bash
rustup target list --installed
```
Expected: `wasm32-wasip1` in the list

5. Confirm WABT:
```bash
wat2wasm --version
```
Expected: `1.0.41`

6. Confirm wit-bindgen CLI:
```bash
wit-bindgen --version
```
Expected: `wit-bindgen-cli 0.59.0`

---

### Clone the Repo

7. Clone and navigate into the repo:
```bash
git clone https://github.com/VeryFatBoy/wasm-udf.git
cd wasm-udf
```

---

### Case 1: Integer Addition

8. Navigate to the Maven project and compile the WAT file to binary:
```bash
cd ~/wasm-udf/neo4j-wasm-udf
wat2wasm src/main/resources/add.wat -o src/main/resources/add.wasm
```

9. Inspect the binary:
```bash
wasm-objdump -x src/main/resources/add.wasm
```
Expected: export name `"add"`, type `(i32, i32) -> i32`

10. Build the JAR. Update `neo4j.version` in `pom.xml` to match your Neo4j Desktop installation first, then:
```bash
mvn -q clean package
```
Expected: no output, clean build. JAR is around 23MB.

11. Stop Neo4j in Desktop.

12. Copy the JAR to the plugins folder:
```bash
cp target/neo4j-wasm-udf-1.0-SNAPSHOT.jar \
  $NEO4J_HOME/plugins/
```
On Linux the plugins folder is typically under `$NEO4J_HOME/plugins/`. On Windows the plugins folder is typically under `%NEO4J_HOME%\plugins\`. `NEO4J_HOME` refers to the root directory of your Neo4j installation; on macOS with Neo4j Desktop the plugins folder is under `~/Library/Application Support/neo4j-desktop/Application/Data/dbmss/<your-dbms-id>/plugins/`.

13. Edit `neo4j.conf` manually and add exactly one line:
```
dbms.security.procedures.allowlist=com.example.wasm.*
```
File location: `~/Library/Application Support/neo4j-desktop/Application/Data/dbmss/<your-dbms-id>/conf/neo4j.conf`

14. Restart Neo4j in Desktop.

15. Open Neo4j Browser at `http://localhost:7474` and confirm the function registered:
```cypher
SHOW FUNCTIONS
YIELD name
WHERE name STARTS WITH 'com.example'
RETURN name;
```
Expected: `"com.example.wasm.add"`

16. Call the function:
```cypher
RETURN com.example.wasm.add(7, 35) AS result;
```
Expected: `42`

---

### Case 2: Single Compound Score

17. Navigate to the Rust crate and build the Wasm binary:
```bash
cd ~/wasm-udf/sentimentable
cargo build --target wasm32-wasip1 --release
```
Expected: `Finished release profile`

18. Confirm binary format:
```bash
wasm-objdump -x target/wasm32-wasip1/release/sentimentable.wasm | head -5
```
Expected: `file format wasm 0x1`

19. Confirm WASI imports:
```bash
wasm-objdump -x target/wasm32-wasip1/release/sentimentable.wasm | grep "Import" -A 10
```
Expected: 5 WASI imports

20. Confirm exports:
```bash
wasm-objdump -x target/wasm32-wasip1/release/sentimentable.wasm | grep "^Export" -A 6
```
Expected: `memory`, `sentimentable`, `cabi_realloc`, `cabi_realloc_wit_bindgen_0_40_0`

21. Find the function's sig index:
```bash
wasm-objdump -x target/wasm32-wasip1/release/sentimentable.wasm | grep "func\[8\]" | head -1
```
Note the `sig=N` value, then look up the type (replace N with the sig index):
```bash
wasm-objdump -x target/wasm32-wasip1/release/sentimentable.wasm | grep "type\[N\]"
```
Expected: `(i32, i32) -> f32`

22. Copy the Wasm binary to Maven resources:
```bash
cp target/wasm32-wasip1/release/sentimentable.wasm \
   ~/wasm-udf/neo4j-wasm-udf/src/main/resources/
```

23. Confirm the timestamp is current:
```bash
ls -lh ~/wasm-udf/neo4j-wasm-udf/src/main/resources/sentimentable.wasm
```

24. Rebuild and deploy:
```bash
cd ~/wasm-udf/neo4j-wasm-udf
mvn -q clean package
cp target/neo4j-wasm-udf-1.0-SNAPSHOT.jar \
  $NEO4J_HOME/plugins/
```

25. Stop Neo4j, restart it, then confirm both functions are registered:
```cypher
SHOW FUNCTIONS
YIELD name
WHERE name STARTS WITH 'com.example'
RETURN name;
```
Expected: `"com.example.wasm.add"` and `"com.example.wasm.sentiment"`

26. Test compound score:
```cypher
RETURN com.example.wasm.sentiment('The movie was great') AS score;
```
Expected: `0.624893307685852`

27. Test capitalization sensitivity:
```cypher
RETURN com.example.wasm.sentiment('The movie was GREAT!') AS score;
```
Expected: `0.7290259003639221`

---

### Case 3: Full Polarity Map

28. The `sentimentable.wit` file is already updated in the repo for the tuple return. Navigate to the Rust crate and rebuild:
```bash
cd ~/wasm-udf/sentimentable
cargo build --target wasm32-wasip1 --release
```
Expected: `Finished release profile`

29. Confirm exports unchanged:
```bash
wasm-objdump -x target/wasm32-wasip1/release/sentimentable.wasm | grep "^Export" -A 6
```
Expected: same four exports as Case 2

30. Find the sig index for func[9] and look up the type (replace N with the sig index):
```bash
wasm-objdump -x target/wasm32-wasip1/release/sentimentable.wasm | grep "func\[9\]" | head -1
```
```bash
wasm-objdump -x target/wasm32-wasip1/release/sentimentable.wasm | grep "type\[N\]"
```
Expected: `(i32, i32) -> i32` -- differs from Case 2; a tuple return uses a result pointer rather than a direct return value

31. Copy the updated Wasm binary:
```bash
cp target/wasm32-wasip1/release/sentimentable.wasm \
   ~/wasm-udf/neo4j-wasm-udf/src/main/resources/
```

32. Confirm the timestamp is current:
```bash
ls -lh ~/wasm-udf/neo4j-wasm-udf/src/main/resources/sentimentable.wasm
```

33. Rebuild and deploy:
```bash
cd ~/wasm-udf/neo4j-wasm-udf
mvn -q clean package
cp target/neo4j-wasm-udf-1.0-SNAPSHOT.jar \
  $NEO4J_HOME/plugins/
```

34. Stop Neo4j, restart it, then test full polarity map:
```cypher
RETURN com.example.wasm.sentiment('The movie was great') AS scores;
```
Expected:
```json
{
  "compound": 0.624893307685852,
  "positive": 0.577464759349823,
  "negative": 0.0,
  "neutral": 0.4225352108478546
}
```

35. Test capitalization:
```cypher
RETURN com.example.wasm.sentiment('The movie was GREAT!') AS scores;
```
Expected:
```json
{
  "compound": 0.7290259003639221,
  "positive": 0.6307692527770996,
  "negative": 0.0,
  "neutral": 0.3692307770252228
}
```

36. Test empty string guard:
```cypher
RETURN com.example.wasm.sentiment('') AS scores;
```
Expected:
```json
{
  "compound": 0.0,
  "positive": 0.0,
  "negative": 0.0,
  "neutral": 1.0
}
```
