# Article similarity calibration (Task 7)

- Overall gate: **PASS** (mode: `mock`).
- Dataset SHA-256: `86b9006e1f613e0709f2a1519db805faba68de9a4f1d0b4a32480009a930e783`.
- Labeled pairs: `320` (`222` calibration / `98` holdout).
- Split assignment SHA-256: `a3f31157a2a0ffe7fa2409cd1daf77c93f69110290940519da052eb0cd46f5c5` (70% calibration / 30% holdout by label/market/family hash; families never cross partitions).
- Grid candidates: `147`; search was run on calibration pairs only.
- Selected parameters: `{"dense_weight": 0.6, "full_text_weight": 0.3, "lexical_weight": 0.4, "numeric_date_weight": 0.3, "ticker_name_org_weight": 0.2, "title_weight": 0.2}`; threshold `0.45`.
- Grouping algorithm version: `format=similarity-v2;model=mock-bge-m3;inputChars=2048;lexical=lexical-v1;weights=0x1.999999999999ap-3,0x1.3333333333333p-2,0x1.3333333333333p-2,0x1.999999999999ap-3,0x1.3333333333333p-1,0x1.999999999999ap-2;threshold=0x1.ccccccccccccdp-2;veto=veto-v1;grouping=complete-link-v1`.
- Determinism audit: `90` checks across `2` multi-article clusters, `3` runs, and all input permutations.
- Runtime samples: `6` per-cluster full-pipeline measurements across `2` clusters.

## Holdout gates

- Precision: `1.0000` (required >= 0.95).
- SAME_EVENT recall: `1.0000` (required >= 0.85).
- OTHER_EVENT false merge: `0.0000` (required <= 0.03).
- HARD_NEGATIVE false merge: `0.0000` (required 0).
- Repeated-run determinism: `1.0000` (required 1.0).

## Evidence boundary

This artifact validates the calibration contract and pipeline with a repository-curated mock fixture using deterministic mock embeddings through `httpx.MockTransport`.
It is not evidence of production `bge-m3` model quality, Ollama runtime, host latency, Ollama version, or model digest.
- Mock algorithm: `mock-token-hash-v1`; implementation source SHA-256: `a957ff624c17f16f4d64a1e97114971471c50ed04cb2ec90d21b91be0c6c8875`.
- Mock full-pipeline per-cluster p95: `0.000734s` (non-production; model/network latency evidence only for the mock transport).
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

Grid tie-break order is precision, SAME_EVENT recall, total false merges, HARD_NEGATIVE false merges, OTHER_EVENT false merges, then canonical parameter order; contradiction vetoes are applied during calibration and holdout.
The selected `SimilarityParameters` are provisional for the repository-curated mock fixture and must not be treated as production-model evidence.
