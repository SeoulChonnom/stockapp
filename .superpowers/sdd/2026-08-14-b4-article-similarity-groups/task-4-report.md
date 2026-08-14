# B4 Task 4 report

## Implemented

- Added immutable `ArticleCandidate`, `SimilarityGroupMember`,
  `SimilarityGroup`, and `SimilarityGroupingResult` value objects.
- Added deterministic complete-link first-fit grouping via
  `group_similar_articles` (plus compatibility aliases).
- Candidates are ordered by UTC-normalized `published_at` descending, then
  `processed_article_id` ascending. Missing publication times sort last.
- Every unordered pair is scored once with the existing cosine and lexical
  scoring pipeline. Group admission checks every current member and vetoes
  contradiction pairs, preventing similarity chains.
- Representative selection uses the required 0.70/0.20/0.10 similarity,
  completeness, and normalized-recency weights. Exact duplicate counts and
  existing representative flags do not affect selection.
- Output group/member ranks are contiguous, immutable, and deterministic;
  singleton member similarity is `1.0`.
- Added validation for thresholds, duplicate IDs, timezone-naive timestamps,
  missing/non-finite vectors, and vector dimension mismatches.

## Verification

- `uv run pytest tests/batch/test_article_similarity.py -q`: **56 passed**
- `uv run pytest tests/batch -q`: **495 passed**, 2 dependency warnings
- `uv run ruff format app/batch/article_similarity.py tests/batch/test_article_similarity.py`
- `uv run ruff check app/batch/article_similarity.py tests/batch/test_article_similarity.py`: clean
- `uv run pyright app/batch/article_similarity.py`: **0 errors**

Only the Task 4 module and focused test file were changed. No database or
Task 5 files were modified.
