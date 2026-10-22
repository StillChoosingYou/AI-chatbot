# School ID ANN evaluation

Training: 355 examples. Held-out test: 89 examples.

- Raw intent accuracy: 67.4%
- Raw macro F1: 0.646
- Accuracy including fallback: 68.5%
- Macro F1 including fallback: 0.699

Deployment model fitted on 444 source examples after evaluation. Scores above describe the evaluation model.

- Small synthetic dataset; holdout scores do not establish real-world accuracy.
- Review/group near-duplicate paraphrases before formal evaluation.
- Softmax confidence is uncalibrated; threshold was fixed before evaluation.
- Held-out results describe the evaluation model, not the refit deployment model.
