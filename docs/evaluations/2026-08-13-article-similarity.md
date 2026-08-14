# Article similarity mock contract evaluation (Task 7)

- Decision/status: **`MOCK_PIPELINE_PASS_REAL_BGE_M3_CALIBRATION_REQUIRED`**.
- Mock arithmetic gates: **PASS** (mode: `mock`; this is not real-model acceptance).
- Approved real-model calibration gate: **UNVERIFIED — REAL `bge-m3` CALIBRATION REQUIRED**.
- Dataset SHA-256: `95a7ca7614ce694317a30109e19a080e1477c1d0cfd267c66eae3f4164dbc666`.
- Synthetic fixture pairs: `320` (`222` calibration / `98` holdout = `69.375%` / `30.625%`; approximate 70/30 constrained by label/market, event family, and normalized content components).
- Split assignment SHA-256: `02dbce5b03084a0591c93afd57dea6972e43ff111abf78b8e1e021238b34b11f` (content components never cross partitions).
- Grid candidates: `147`; search was run on calibration pairs only.
- Selected parameters: `{"dense_weight": 0.6, "full_text_weight": 0.3, "lexical_weight": 0.4, "numeric_date_weight": 0.3, "ticker_name_org_weight": 0.2, "title_weight": 0.2}`; threshold `0.45`.
- Grouping algorithm version: `format=similarity-v2;model=mock-bge-m3;inputChars=2048;lexical=lexical-v1;weights=0x1.999999999999ap-3,0x1.3333333333333p-2,0x1.3333333333333p-2,0x1.999999999999ap-3,0x1.3333333333333p-1,0x1.999999999999ap-2;threshold=0x1.ccccccccccccdp-2;veto=veto-v1;grouping=complete-link-v1`.
- Determinism audit: `90` checks across `2` multi-article clusters, `3` runs, and all input permutations.
- Runtime samples: `6` per-cluster full-pipeline measurements across `2` clusters.

## Mock holdout arithmetic gates (not real-model acceptance)

- Precision: `1.0000` (required >= 0.95).
- SAME_EVENT recall: `1.0000` (required >= 0.85).
- OTHER_EVENT false merge: `0.0000` (required <= 0.03).
- HARD_NEGATIVE false merge: `0.0000` (required 0).
- Repeated-run determinism: `1.0000` (required 1.0).

## Evidence boundary

This artifact validates the calibration contract and pipeline with a repository-curated mock fixture using deterministic mock embeddings through `httpx.MockTransport`.
The dataset is a synthetic deterministic contract fixture with fixture annotations, not a manually labeled real-news calibration corpus.
It is not evidence of production `bge-m3` model quality, Ollama runtime, host latency, Ollama version, or model digest.
- Mock algorithm: `mock-token-hash-v1`; implementation source SHA-256: `098587ddd0c0e0f85a96b90abc7d405f07a23c7bc809811cc8ff7c95e8fbc8bd`.
- Mock full-pipeline per-cluster p95: `0.000747s` (non-production; model/network latency evidence only for the mock transport).
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
The selected `SimilarityParameters` are provisional for this synthetic deterministic contract fixture, are not release-calibrated, and must not be treated as production-model evidence or a satisfied real holdout gate.
Run the explicitly opted-in `--live` mode separately against the configured provider before any production-quality or release-calibration decision.
