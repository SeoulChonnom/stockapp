# B4 Task 3 report

## Scope

- Added deterministic, pure-Python pair scoring in `app/batch/article_similarity.py`.
- Added focused tests in `tests/batch/test_article_similarity.py`.
- No Ollama, network, database, or grouping behavior is included.

## Contract

- `safe_cosine_similarity` returns the signed cosine for valid vectors using
  scale-normalized components and `math.fsum`, returns `0.0` for a zero vector,
  and rejects dimension mismatch/non-finite values without overflow.
- `SimilarityParameters` is frozen and validates non-negative lexical component
  weights and dense/lexical weights whose sums are exactly one (within a small
  floating-point tolerance).
- NFC/casefold/whitespace normalization and deterministic token regexes produce
  title/full tokens, normalized numbers and percentages, ISO/Korean dates,
  direction labels, uppercase ticker-like tokens, and organization/name tokens.
- Empty structured feature sets score `0.0`, avoiding a false perfect match.
  Dense cosine values are clamped from `[-1, 1]` to `[0, 1]` before blending;
  the final score is bounded in `[0, 1]`.
- Numeric vetoes require a shared local metric/unit pair, date vetoes require
  explicit non-overlapping dates, and direction vetoes require exclusively
  opposing positive/negative claims. Veto reasons remain independent from the
  combined score.
- Grouped decimal numbers are parsed as one value; malformed separators and
  impossible calendar dates are ignored conservatively. Entity extraction only
  retains Korean organization suffixes, explicit organization suffixes, and
  multi-word proper names; common headline words and ticker fragments are not
  entities.

## Verification

```text
40 focused pytest tests passed
ruff format/check passed
pyright app/batch/article_similarity.py: 0 errors, 0 warnings
```
