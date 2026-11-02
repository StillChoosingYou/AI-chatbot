# School ID ANN evaluation

Training: 280 examples. Held-out test: 71 examples.

- Raw intent accuracy: 77.5%
- Raw macro F1: 0.773
- Accuracy including fallback: 73.2%
- Macro F1 including fallback: 0.776
- Held-out questions turned away by the fallback: 9 of 71

Deployment model fitted on 351 source examples after evaluation. Scores above describe the evaluation model.

- Small synthetic dataset; holdout scores do not establish real-world accuracy.
- The dataset holds school ID questions only. out_of_scope has no examples: a held-out question with no known topic word, or a score under the threshold, is turned away and counts as wrong for its own topic.
- Review/group near-duplicate paraphrases before formal evaluation.
- Softmax confidence is uncalibrated; threshold was fixed before evaluation.
- Held-out results describe the evaluation model, not the refit deployment model.
