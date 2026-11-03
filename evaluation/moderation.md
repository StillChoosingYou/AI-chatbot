# Chat moderation evaluation

Rows: 2963 (train 2073, test 890). The test rows hold out 30% of the rude words and 20% of the sentence frames.

| What is measured | Precision | Recall | False alarms on clean test rows |
| --- | --- | --- | --- |
| Rules only | 1.0 | 0.9852 | 0.0 |
| Model only | 0.9845 | 0.6293 | 0.0212 |
| Rules and model (what the app uses) | 0.9934 | 0.9934 | 0.0141 |

Threats and sexual harassment in the test rows judged severe: 30 of 37.
The 351 real school ID questions flagged as offensive: 0.
Clean messages that would earn a strike (severity 2 or more), of 690 checked: 0.

## Hand-written probes (116 messages, evaluation/moderation_probes.csv)

Recall 0.85, precision 0.9808, false alarms 0.0179. The probes were used while the lists were tuned, so this number is optimistic.

## Limits
- The data are written by the team from templates: a synthetic test. Real students write in ways it does not cover.
- Scores are for held-out words and frames of the same templates, so they are an upper bound on real-world accuracy.
- The model alone never decides a severe case; the rules and the administrator do.
- Sarcasm, quoting someone, and words with a harmless meaning in context can still be flagged. That is why every flag is reviewable.
