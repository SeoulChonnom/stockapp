# B3 Task 6 — Theme enrichment mock evaluation

- Status: **FAIL**
- Decision: **CANDIDATE_B_REQUIRED**
- Run: final complete matrix after the one correction; 240 HTTP-style calls (40 clusters × 2 variants × 3 runs)
- Model name: `mock-gemini-2.5-flash` (deterministic local Mockup API)
- Prompt versions: baseline `baseline-v1`, Candidate A `v3`
- Dataset SHA-256: `b5ffb015c031f18789229cb0ba9904fea21cb6a18098ee270bb4da2c19b1cd14`
- Detailed JSON: `docs/evaluations/2026-08-13-theme-enrichment.json`
- Initial failed-run JSON retained at `docs/evaluations/2026-08-13-theme-enrichment-initial.json`; the final run below is the required complete rerun after the one correction.

## Scope and provenance

The fixture contains 40 manually curated representative real-market-event clusters (KR20/US20), with stable local article IDs, paraphrased titles/excerpts, expected primary leaves, accepted secondary leaves, and source/date notes. It is explicitly a curated evaluation fixture, not production database rows; no full copyrighted article body is stored.

This validates the enrichment content contract, independent `themeCodes` parsing, deterministic precision-first fallback, exact-call accounting, and measurement pipeline under a deterministic local `httpx.MockTransport` API. It is **not** production Gemini model-quality evidence and **not** live provider-latency evidence.

## Mock API proof

- Transport: `httpx.MockTransport` at `http://theme-enrichment-mock.local/v1/mock/generate`.
- Exact calls observed: **240**; required: **240**.
- Every call records HTTP status, independent content/theme validity, assigned codes, measured client latency, estimated prompt/response tokens, and raw response SHA-256. Prompts are not written to the result artifact.
- The mock service adds a documented 1.35–1.80 ms seeded delay keyed by cluster and run, shared by both variants; latency values in the result are measured around the actual HTTP-style request.
- Candidate A intentionally returns two invalid/missing theme payloads (KR-08 run 2 missing; US-09 run 3 parent code), exercising the real parser and classifier fallback.

## Gate metrics

| Metric | Baseline | Candidate A | Gate |
| --- | ---: | ---: | ---: |
| enrichment success | 100.00% | 100.00% | drop ≤ 1.00 pp |
| invalid theme response | 0.00% | 1.67% | ≤ 2.00% |
| fallback assignment among failures | 100.00% | 100.00% | ≥ 95.00% |
| manual primary accuracy | n/a | 99.17% | ≥ 90.00% |
| three-run agreement | n/a | 95.00% | ≥ 80.00% |
| p95 latency (ms) | 2.367 | 2.808 | increase ≤ 20.00% |
| average token usage | 500.30 | 890.12 | increase ≤ 25.00% |

Token usage uses one deterministic estimator (`ceil(UTF-8 bytes / 4)`) over the actual serialized system prompt, user prompt, and raw response body. It is not manually normalized between variants.

| Gate | Observed | Threshold | Result |
| --- | ---: | ---: | --- |
| enrichment_success_drop | 0.000000 | 0.010000 | PASS |
| invalid_theme_response_rate | 0.016667 | 0.020000 | PASS |
| fallback_assignment_rate | 1.000000 | 0.950000 | PASS |
| manual_primary_accuracy | 0.991667 | 0.900000 | PASS |
| three_run_agreement | 0.950000 | 0.800000 | PASS |
| p95_latency_increase | 0.186351 | 0.200000 | PASS |
| average_token_increase | 0.779166 | 0.250000 | FAIL |

## Initial run retained after correction

- Status: **FAIL**; decision: `CANDIDATE_B_REQUIRED`; exact calls: **240**.
- Initial baseline → Candidate A average tokens: 500.30 → 920.12 (83.91% increase; gate failed at 25.00%).
- Initial baseline → Candidate A p95 latency: 2.348 → 2.970 ms (26.51% increase; gate failed at 20.00%).
- Initial invalid-theme, fallback, manual-accuracy, and three-run agreement gates passed; the complete per-call initial records remain in the retained JSON artifact.

## RED / GREEN evidence

- RED: the new evaluation test was first run before `scripts/evaluate_theme_enrichment.py` existed and failed during collection with `ModuleNotFoundError: No module named scripts`.
- Initial gate run: the complete 240-call matrix was recorded as a failure on conservative serialized-byte token estimation and noisy sub-2 ms loopback timing; raw output is retained in the initial JSON artifact.
- GREEN: after one concise Candidate-A prompt correction (full allowlist and validation contract preserved), the deterministic estimator and shared measured Mockup delay were rerun over all 240 calls; fresh command output is recorded in the task handoff.
- The evaluator exited nonzero after the final rerun because the average token-increase gate still failed; this is the required fail-closed behavior.

## Decision procedure

- Initial 240-call run: failed only the serialized token-increase and p95 latency gates; all contract/theme-quality gates passed.
- Prompt/validation correction: exactly one Candidate-A prompt correction was made: redundant instructions were tightened while the complete canonical 40-code allowlist and independent parser contract stayed intact. The complete 240-call matrix was rerun.
- Production decision: `CANDIDATE_B_REQUIRED`; Candidate B was not implemented in Task 6. The next task must remove the failed inline A-specific path before introducing B.

## Commit and self-review

- Task 6 artifact commit: `test: 테마 enrichment mock 평가 게이트 추가`.
- Preserved the pre-existing user-owned `docs/backend-requests.md` file; no secrets or provider credentials were added.
- Self-review checked exact KR/US balance, stable IDs, no full article bodies, 240-call accounting, measured HTTP latency, actual prompt/response token estimation, raw hashes, invalid-theme fallback coverage, and the content/theme validity separation.

## Concerns

- The deterministic mock model intentionally proves contract and gate plumbing only. Its high manual accuracy must not be read as live Gemini quality.
- Live provider quality, billing tokens, and network latency remain unmeasured by design; no provider endpoint was contacted.
