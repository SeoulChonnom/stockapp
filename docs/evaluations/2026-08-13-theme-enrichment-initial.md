# B3 Task 6 — Theme enrichment mock evaluation

- Status: **FAIL**
- Decision: **CANDIDATE_B_REQUIRED**
- Run: `initial-before-one-correction-replay`; 240 matrix HTTP-style calls (40 clusters × 2 variants × 3 runs), plus 2 excluded warmups
- Model name: `mock-gemini-2.5-flash` (deterministic local Mockup API)
- Prompt versions: baseline `historical-production-36411a6-parent`, Candidate A `v2`
- Dataset SHA-256: `b5ffb015c031f18789229cb0ba9904fea21cb6a18098ee270bb4da2c19b1cd14`
- Result JSON SHA-256: `fe17fd956d009b8bfef23e65d6213e361bdbec915571e7898d409d4fc533210e`
- Hash manifest: `docs/evaluations/2026-08-13-theme-enrichment-initial.manifest.json`
- Detailed JSON: `docs/evaluations/2026-08-13-theme-enrichment-initial.json`

## Scope and provenance

The fixture contains 40 manually curated representative real-market-event clusters (KR20/US20), with stable local article IDs, paraphrased titles/excerpts, expected primary leaves, accepted secondary leaves, and source/date notes. It is explicitly a curated evaluation fixture, not production database rows; no full copyrighted article body is stored.

This validates the enrichment content contract, independent `themeCodes` parsing, deterministic precision-first fallback, exact-call accounting, and measurement pipeline under a deterministic local `httpx.MockTransport` API. There is no retry or error-response scenario in this run. It is **not** production Gemini model-quality evidence and **not** live provider-latency evidence.

## Mock API proof

- Transport: `httpx.MockTransport` at `http://theme-enrichment-mock.local/v1/mock/generate`.
- Exact matrix calls observed: **240**; required: **240**. Warmup calls: **2**, excluded from the matrix; total Mockup requests: **242**.
- Every matrix call records HTTP status, independent content/theme validity, raw-invalid reason, accepted theme subset, actual fallback use, measured client latency, estimated prompt/response tokens, prompt hashes, and raw response SHA-256. Prompt bodies are not written to the result artifact.
- The mock service adds a documented 1.35–1.80 ms seeded delay keyed by cluster and run (the code clamps to a 1.35 ms minimum); latency values in the result are measured around the actual HTTP-style request. Baseline/Candidate requests are paired and interleaved.
- Candidate A intentionally returns two invalid/missing theme payloads (KR-08 run 2 missing; US-09 run 3 parent code), exercising the real parser and classifier fallback.

## Gate metrics

| Metric | Baseline | Candidate A | Gate |
| --- | ---: | ---: | ---: |
| enrichment success | 100.00% | 100.00% | drop ≤ 1.00 pp |
| invalid theme response | 0.00% | 1.67% | ≤ 2.00% |
| zero-valid theme output | 0.00% | 1.67% | diagnostic |
| accepted theme subset | 0.00% | 98.33% | diagnostic |
| fallback assignment among failures | n/a | 100.00% | ≥ 95.00% |
| manual primary accuracy | n/a | 99.17% | ≥ 90.00% |
| three-run agreement | n/a | 95.00% | ≥ 80.00% |
| p95 latency (ms) | 2.581 | 2.549 | increase ≤ 20.00% |
| average token usage | 415.30 | 920.12 | increase ≤ 25.00% |

Token usage uses one deterministic estimator (`ceil(UTF-8 bytes / 4)`) over the actual serialized system prompt, user prompt, and raw response body. It is not manually normalized between variants.
- Baseline prompt: `b8eabdbda4dcb46ff18797d12867ba8148dcbf0ec7ee5c12ab269bf74e6c7193` (178 bytes); Candidate v2: `89478326d2e6e9ceb7ded5bfb90bef4caf20d268fa40a23fbbcafa4d2566bc20`; Candidate v3: `2b12eb68abfd4b153fdce2d90d44a00aabbbc003e019d7ff5aba0e38f9378034`.
- Evaluator script SHA-256: `5d8b25ccf9fd2dea1ea447f6403520ebfd81b659aa356c8c7ce62cac4b9595c1`; Mock implementation SHA-256: `ae64b3d145112896945a1e76045a12164fc6f4ef1ddee236b20f262a5a63c854`.

| Gate | Observed | Threshold | Result |
| --- | ---: | ---: | --- |
| enrichment_success_drop | 0.000000 | 0.010000 | DECISIVE / PASS |
| invalid_theme_response_rate | 0.016667 | 0.020000 | DECISIVE / PASS |
| fallback_assignment_rate | 1.000000 | 0.950000 | DECISIVE / PASS |
| manual_primary_accuracy | 0.991667 | 0.900000 | DECISIVE / PASS |
| three_run_agreement | 0.950000 | 0.800000 | DECISIVE / PASS |
| p95_latency_increase | -0.012254 | 0.200000 | NOT_ASSESSED / PASS |
| average_token_increase | 1.215547 | 0.250000 | DECISIVE / FAIL |

## Latency repeatability audit

- No repeatability audit runs were performed for this artifact; the canonical matrix p95 is reported without a repeatability determination.
- Wall-clock MockTransport gate status: **NOT_ASSESSED**. No repeatability status is inferred from zero audit runs, and the measurement is never treated as production latency evidence.

## RED / GREEN evidence

- RED: the new evaluation test was first run before `scripts/evaluate_theme_enrichment.py` existed and failed during collection with `ModuleNotFoundError: No module named scripts`.
- Initial gate run: the complete 240-call matrix was recorded as a failure on conservative serialized-byte token estimation; local wall-clock p95 is measured but interpreted through the repeatability audit.
- GREEN: the evaluation harness and production parser/content semantics are implemented and tested; the final corrected run is recorded separately.
- The evaluator exited nonzero after the final rerun because the average token-increase gate still failed; this is the required fail-closed behavior.

## Decision procedure

- Initial 240-call run: failed the serialized token-increase gate; local p95 timing is an observed mock measurement, not production evidence.
- Prompt/validation correction: exactly one Candidate-A prompt correction was made before this evidence repair: redundant instructions were tightened while the complete canonical 40-code allowlist and independent parser contract stayed intact. The complete corrected 240-call matrix was rerun.
- Production decision: `CANDIDATE_B_REQUIRED`; Candidate B was not implemented in Task 6. The next task must remove the failed inline A-specific path before introducing B.

## Commit and self-review

- Task 6 artifact commit: `test: 테마 enrichment mock 평가 게이트 추가`.
- Evidence correction commit: `fix: 테마 enrichment 평가 증거 정합성 보강`.
- Preserved the pre-existing user-owned `docs/backend-requests.md` file; no secrets or provider credentials were added.
- Self-review checked exact KR/US balance, stable IDs, no full article bodies, 240-call accounting, measured HTTP latency, actual prompt/response token estimation, raw hashes, invalid-theme fallback coverage, and the content/theme validity separation.

## Concerns

- The deterministic mock model intentionally proves contract and gate plumbing only. Its high manual accuracy must not be read as live Gemini quality.
- Live provider quality, billing tokens, and network latency remain unmeasured by design; no provider endpoint was contacted.
