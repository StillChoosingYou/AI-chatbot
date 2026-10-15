# School ID ANN evaluation

Training: 204 examples. Held-out test: 51 examples.

- Raw intent accuracy: 68.6%
- Raw macro F1: 0.665
- Accuracy including fallback: 68.6%
- Macro F1 including fallback: 0.682

Deployment model fitted on 255 source examples after evaluation. Scores above describe the evaluation model.

- Small synthetic dataset; holdout scores do not establish real-world accuracy.
- Review/group near-duplicate paraphrases before formal evaluation.
- Softmax confidence is uncalibrated; threshold was fixed before evaluation.
- Held-out results describe the evaluation model, not the refit deployment model.
