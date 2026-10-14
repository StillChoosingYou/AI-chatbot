# School ID ANN evaluation

Training: 192 examples. Held-out test: 48 examples.

- Raw intent accuracy: 70.8%
- Raw macro F1: 0.689
- Accuracy including fallback: 68.8%
- Macro F1 including fallback: 0.669

Deployment model fitted on 240 source examples after evaluation. Scores above describe the evaluation model.

- Small synthetic dataset; holdout scores do not establish real-world accuracy.
- Review/group near-duplicate paraphrases before formal evaluation.
- Softmax confidence is uncalibrated; threshold was fixed before evaluation.
- Held-out results describe the evaluation model, not the refit deployment model.
