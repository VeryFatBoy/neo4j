wit_bindgen::generate!({
    world: "sentimentable",
});

struct Component;

impl Guest for Component {
    fn sentimentable(input: String) -> (f32, f32, f32, f32) {
        lazy_static::lazy_static! {
            static ref ANALYZER: vader_sentiment::SentimentIntensityAnalyzer<'static> =
                vader_sentiment::SentimentIntensityAnalyzer::new();
        }
        let scores = ANALYZER.polarity_scores(input.as_str());
        (
            *scores.get("compound").unwrap_or(&0.0) as f32,
            *scores.get("pos").unwrap_or(&0.0) as f32,
            *scores.get("neg").unwrap_or(&0.0) as f32,
            *scores.get("neu").unwrap_or(&0.0) as f32,
        )
    }
}

export!(Component);
