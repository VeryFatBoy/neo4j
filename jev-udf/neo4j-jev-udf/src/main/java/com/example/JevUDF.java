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

        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());

        if (response.statusCode() != 200) {
            throw new RuntimeException("Jev API error " + response.statusCode() + ": " + response.body());
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
