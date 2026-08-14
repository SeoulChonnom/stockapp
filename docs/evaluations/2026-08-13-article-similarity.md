# Article similarity calibration (Task 7)

- Overall gate: **PASS** (mode: `mock`).
- Dataset SHA-256: `1eac122304c274f5b8279ae1abc7a47c03cf5a6641409094b9100101369895dd`.
- Labeled pairs: `320` (`224` calibration / `96` holdout).
- Split assignment SHA-256: `0cffc017c2e3ffe6a03de76064c7006b15f406c09167a7bd2bb54475448469f4` (70% calibration / 30% holdout, frozen by pair-ID hash).
- Grid candidates: `147`; search was run on calibration pairs only.
- Selected parameters: `{"dense_weight": 0.6, "full_text_weight": 0.3, "lexical_weight": 0.4, "numeric_date_weight": 0.3, "ticker_name_org_weight": 0.2, "title_weight": 0.2}`; threshold `0.45`.
- Grouping algorithm version: `complete-link-v1;lexical-v1;veto-v1`.

## Holdout gates

- Precision: `1.0000` (required >= 0.95).
- SAME_EVENT recall: `1.0000` (required >= 0.85).
- OTHER_EVENT false merge: `0.0000` (required <= 0.03).
- HARD_NEGATIVE false merge: `0.0000` (required 0).
- Repeated-run determinism: `1.0000` (required 1.0).

## Evidence boundary

This artifact validates the calibration contract and pipeline using deterministic mock embeddings through `httpx.MockTransport`.
It is not evidence of production `bge-m3` model quality, Ollama runtime, host latency, Ollama version, or model digest.
- Mock algorithm: `mock-token-hash-v1`; algorithm hash: `d4c43fd72cbb39b37a51695df5d316c7cda977ca3528428b95dab912c3419134`.
- Mock pipeline p95: `2.559741s` (non-production; model/network latency excluded).
- Ollama version: **NOT COLLECTED (mock mode)**.
- `bge-m3` model digest: **NOT COLLECTED (mock mode)**.
- Production-host p95: **NOT MEASURED**.

## Gate detail

- `precision_ge_95_percent`: `True`.
- `same_event_recall_ge_85_percent`: `True`.
- `other_event_false_merge_le_3_percent`: `True`.
- `hard_negative_false_merge_zero`: `True`.
- `determinism_100_percent`: `True`.
- `mock_pipeline_p95_le_30_seconds`: `True`.

The selected `SimilarityParameters` are provisional for this mock calibration and are frozen in production code only because the recorded mock holdout gate passed.
Run the explicitly opted-in `--live` mode separately before treating them as evidence about the configured production model.
