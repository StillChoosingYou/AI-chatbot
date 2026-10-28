# School ID ANN evaluation

Training: 144 examples. Held-out test: 36 examples.

- Raw intent accuracy: 55.6%
- Raw macro F1: 0.540
- Accuracy including fallback: 55.6%
- Macro F1 including fallback: 0.544
- Held-out questions turned away by the fallback: 3 of 36

Deployment model fitted on 180 source examples after evaluation. Scores above describe the evaluation model.

- Small synthetic dataset; holdout scores do not establish real-world accuracy.
- The dataset holds school ID questions only. out_of_scope has no examples: a held-out question with no known topic word, or a score under the threshold, is turned away and counts as wrong for its own topic.
- Review/group near-duplicate paraphrases before formal evaluation.
- Softmax confidence is uncalibrated; threshold was fixed before evaluation.
- Held-out results describe the evaluation model, not the refit deployment model.
