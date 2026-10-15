# School ID ANN evaluation

Training: 216 examples. Held-out test: 54 examples.

- Raw intent accuracy: 66.7%
- Raw macro F1: 0.653
- Accuracy including fallback: 59.3%
- Macro F1 including fallback: 0.579

Deployment model fitted on 270 source examples after evaluation. Scores above describe the evaluation model.

- Small synthetic dataset; holdout scores do not establish real-world accuracy.
- Review/group near-duplicate paraphrases before formal evaluation.
- Softmax confidence is uncalibrated; threshold was fixed before evaluation.
- Held-out results describe the evaluation model, not the refit deployment model.
