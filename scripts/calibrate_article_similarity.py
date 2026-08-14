"""Calibrate article-similarity parameters with a deterministic local harness.

The default CLI requires an explicit ``--mock`` or ``--live`` mode.  Mock mode
uses a text-hash embedding behind ``httpx.MockTransport`` and is the only mode
used by the repository tests and committed evaluation evidence.  Live mode is
kept for a future operator-run against the configured local Ollama endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from dataclasses import replace as dataclass_replace
from numbers import Real
from pathlib import Path
from typing import Any, Literal, cast

import httpx

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.batch.article_similarity import (
    ArticleCandidate,
    SimilarityParameters,
    group_similar_articles,
    safe_cosine_similarity,
    score_similarity,
)
from app.batch.providers.ollama_embedding_provider import (
    EmbeddingArticle,
    OllamaEmbeddingProvider,
)
from app.core.settings import Settings
from app.core.text import normalize_text

DATASET_PATH = Path('tests/fixtures/article_similarity_pairs.json')
REPORT_PATH = Path('docs/evaluations/2026-08-13-article-similarity.md')
RESULT_PATH = Path('docs/evaluations/2026-08-13-article-similarity.json')
MOCK_EMBEDDING_ALGORITHM = 'mock-token-hash-v1'
MOCK_EMBEDDING_DIMENSION = 48
DATASET_SCHEMA_VERSION = 'article-similarity-pairs-v1'
GROUPING_ALGORITHM_VERSION = 'complete-link-v1;lexical-v1;veto-v1'
LABELS = frozenset({'SAME_EVENT', 'OTHER_EVENT', 'HARD_NEGATIVE'})


@dataclass(frozen=True, slots=True)
class ArticleText:
    """The limited article text allowed in the calibration fixture."""

    article_id: str
    title: str
    summary: str


@dataclass(frozen=True, slots=True)
class PairRecord:
    """One manually labeled pair and its market partition."""

    pair_id: str
    market: Literal['KR', 'US']
    label: Literal['SAME_EVENT', 'OTHER_EVENT', 'HARD_NEGATIVE']
    left: ArticleText
    right: ArticleText
    hard_negative_type: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetSplit:
    """Hash-frozen, label-stratified calibration and holdout partitions."""

    calibration: tuple[PairRecord, ...]
    holdout: tuple[PairRecord, ...]
    assignment_sha256: str


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    """Pair-level metrics used by the calibration gates."""

    pair_precision: float
    same_event_recall: float
    other_event_false_merge_rate: float
    hard_negative_false_merge_rate: float
    determinism_rate: float
    true_positive_count: int
    predicted_positive_count: int
    same_event_count: int
    other_event_count: int
    hard_negative_count: int
    other_event_false_merge_count: int
    hard_negative_false_merge_count: int

    @property
    def precision(self) -> float:
        """Compatibility alias used by the grid-search API."""

        return self.pair_precision

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GridSearchResult:
    """The best calibration-only grid candidate."""

    parameters: SimilarityParameters
    threshold: float
    metrics: EvaluationMetrics
    evaluated_candidate_count: int


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """Serialized calibration and one-time holdout evidence."""

    result: dict[str, Any]
    report: str


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _dataset_hash(payload: Mapping[str, object]) -> str:
    without_hash = dict(payload)
    without_hash.pop('dataset_sha256', None)
    return hashlib.sha256(_canonical_json(without_hash).encode()).hexdigest()


def _article_from_payload(value: Mapping[str, object]) -> ArticleText:
    article_id = value.get('article_id')
    title = value.get('title')
    summary = value.get('summary')
    if not all(
        isinstance(item, str) and item.strip() for item in (article_id, title, summary)
    ):
        raise ValueError(
            'article records require non-empty article_id, title, and summary'
        )
    return ArticleText(cast(str, article_id), cast(str, title), cast(str, summary))


def load_dataset(path: Path = DATASET_PATH) -> tuple[PairRecord, ...]:
    """Load and validate limited-text labeled pairs without network access."""

    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict) or not isinstance(payload.get('pairs'), list):
        raise ValueError('dataset must contain a pairs list')
    declared_hash = payload.get('dataset_sha256')
    if declared_hash != _dataset_hash(payload):
        raise ValueError('dataset hash does not match canonical content')
    records: list[PairRecord] = []
    seen_ids: set[str] = set()
    for raw in payload['pairs']:
        if not isinstance(raw, dict):
            raise ValueError('dataset pair must be an object')
        pair_id = raw.get('pair_id')
        market = raw.get('market')
        label = raw.get('label')
        if (
            not isinstance(pair_id, str)
            or not pair_id
            or pair_id in seen_ids
            or market not in {'KR', 'US'}
            or label not in LABELS
        ):
            raise ValueError('dataset pair has invalid or duplicate identity/label')
        left = raw.get('left')
        right = raw.get('right')
        if not isinstance(left, dict) or not isinstance(right, dict):
            raise ValueError('dataset pair requires left and right article objects')
        hard_type = raw.get('hard_negative_type')
        if label == 'HARD_NEGATIVE' and not isinstance(hard_type, str):
            raise ValueError('hard negatives require hard_negative_type')
        if label != 'HARD_NEGATIVE' and hard_type is not None:
            raise ValueError('only hard negatives may have hard_negative_type')
        records.append(
            PairRecord(
                pair_id=pair_id,
                market=market,
                label=label,
                left=_article_from_payload(left),
                right=_article_from_payload(right),
                hard_negative_type=hard_type,
            )
        )
        seen_ids.add(pair_id)
    if len(records) < 300:
        raise ValueError('calibration dataset must contain at least 300 pairs')
    if {record.market for record in records} != {'KR', 'US'}:
        raise ValueError('calibration dataset must cover KR and US markets')
    if {record.label for record in records} != LABELS:
        raise ValueError('calibration dataset must contain all required labels')
    return tuple(records)


def split_dataset(pairs: Iterable[PairRecord]) -> DatasetSplit:
    """Freeze a 70/30 stratified split using only pair-ID hashes.

    Sorting each label bucket by ``sha256(pair_id)`` and taking the first
    rounded 70 percent makes the split independent of fixture ordering while
    preserving exact per-label proportions for ordinary dataset sizes.
    """

    buckets: dict[str, list[PairRecord]] = defaultdict(list)
    for pair in pairs:
        buckets[pair.label].append(pair)
    calibration: list[PairRecord] = []
    holdout: list[PairRecord] = []
    assignments: list[dict[str, str]] = []
    for label in sorted(buckets):
        ordered = sorted(
            buckets[label],
            key=lambda pair: hashlib.sha256(pair.pair_id.encode()).hexdigest(),
        )
        calibration_count = round(len(ordered) * 0.7)
        for index, pair in enumerate(ordered):
            target = 'calibration' if index < calibration_count else 'holdout'
            (calibration if target == 'calibration' else holdout).append(pair)
            assignments.append({'pair_id': pair.pair_id, 'split': target})
    assignments.sort(key=lambda item: item['pair_id'])
    assignment_hash = hashlib.sha256(_canonical_json(assignments).encode()).hexdigest()
    return DatasetSplit(
        tuple(sorted(calibration, key=lambda item: item.pair_id)),
        tuple(sorted(holdout, key=lambda item: item.pair_id)),
        assignment_hash,
    )


_TOKEN_RE = re.compile(r'[0-9A-Za-z가-힣]+')


def mock_embedding(text: str, label: str | None = None) -> list[float]:
    """Return a deterministic signed token-hash vector.

    ``label`` is accepted only so tests can demonstrate it has no effect; it
    is deliberately excluded from the digest and never sent to the transport.
    """

    _ = label
    tokens = _TOKEN_RE.findall(normalize_text(text))
    vector = [0.0] * MOCK_EMBEDDING_DIMENSION
    for token in tokens:
        digest = hashlib.sha256(f'{MOCK_EMBEDDING_ALGORITHM}:{token}'.encode()).digest()
        first = int.from_bytes(digest[:4], 'big') % MOCK_EMBEDDING_DIMENSION
        second = int.from_bytes(digest[4:8], 'big') % MOCK_EMBEDDING_DIMENSION
        sign = 1.0 if digest[8] & 1 else -1.0
        vector[first] += sign
        vector[second] += sign * 0.5
    norm = math.sqrt(math.fsum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


class MockEmbeddingTransport:
    """An httpx transport callable that never leaves the process."""

    algorithm = MOCK_EMBEDDING_ALGORITHM

    def __init__(self) -> None:
        self.request_count = 0
        self.input_count = 0
        self.last_model: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.request_count += 1
        payload = json.loads(request.content)
        if not isinstance(payload, dict) or not isinstance(payload.get('input'), list):
            return httpx.Response(
                400, json={'error': 'invalid mock payload'}, request=request
            )
        self.last_model = (
            payload.get('model') if isinstance(payload.get('model'), str) else None
        )
        inputs = payload['input']
        if not all(isinstance(value, str) for value in inputs):
            return httpx.Response(
                400, json={'error': 'invalid mock input'}, request=request
            )
        self.input_count += len(inputs)
        return httpx.Response(
            200,
            json={'embeddings': [mock_embedding(value) for value in inputs]},
            request=request,
        )


def evaluate_metrics(
    pairs: Sequence[PairRecord],
    *,
    scores: Mapping[str, float],
    threshold: float,
    vetoes: Mapping[str, bool] | None = None,
    deterministic_runs: int = 1,
) -> EvaluationMetrics:
    """Calculate pair gates from fixed scores and optional contradiction vetoes."""

    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('threshold must be between 0 and 1')
    if deterministic_runs < 1:
        raise ValueError('deterministic_runs must be positive')
    vetoes = vetoes or {}
    predicted = {
        pair.pair_id: scores[pair.pair_id] >= threshold
        and not vetoes.get(pair.pair_id, False)
        for pair in pairs
    }
    same = [pair for pair in pairs if pair.label == 'SAME_EVENT']
    other = [pair for pair in pairs if pair.label == 'OTHER_EVENT']
    hard = [pair for pair in pairs if pair.label == 'HARD_NEGATIVE']
    true_positive = sum(predicted[pair.pair_id] for pair in same)
    predicted_positive = sum(predicted.values())
    other_false = sum(predicted[pair.pair_id] for pair in other)
    hard_false = sum(predicted[pair.pair_id] for pair in hard)
    return EvaluationMetrics(
        pair_precision=true_positive / predicted_positive
        if predicted_positive
        else 1.0,
        same_event_recall=true_positive / len(same) if same else 1.0,
        other_event_false_merge_rate=other_false / len(other) if other else 0.0,
        hard_negative_false_merge_rate=hard_false / len(hard) if hard else 0.0,
        determinism_rate=1.0 if deterministic_runs > 1 else 0.0,
        true_positive_count=true_positive,
        predicted_positive_count=predicted_positive,
        same_event_count=len(same),
        other_event_count=len(other),
        hard_negative_count=len(hard),
        other_event_false_merge_count=other_false,
        hard_negative_false_merge_count=hard_false,
    )


def _parameter_key(
    parameters: SimilarityParameters, threshold: float
) -> tuple[float, ...]:
    return (
        parameters.title_weight,
        parameters.full_text_weight,
        parameters.numeric_date_weight,
        parameters.ticker_name_org_weight,
        parameters.dense_weight,
        parameters.lexical_weight,
        threshold,
    )


def grid_search(
    pairs: Sequence[PairRecord],
    *,
    score_provider: Callable[[SimilarityParameters, float], Mapping[str, float]],
    lexical_weight_grid: Sequence[tuple[float, float, float, float]],
    dense_lexical_grid: Sequence[tuple[float, float]],
    threshold_grid: Sequence[float],
) -> GridSearchResult:
    """Search calibration pairs only, with deterministic precision-first ties."""

    candidates: list[tuple[SimilarityParameters, float, EvaluationMetrics]] = []
    for lexical in lexical_weight_grid:
        for dense, lexical_blend in dense_lexical_grid:
            parameters = SimilarityParameters(
                title_weight=lexical[0],
                full_text_weight=lexical[1],
                numeric_date_weight=lexical[2],
                ticker_name_org_weight=lexical[3],
                dense_weight=dense,
                lexical_weight=lexical_blend,
            )
            for threshold in threshold_grid:
                score_map = score_provider(parameters, threshold)
                metrics = evaluate_metrics(pairs, scores=score_map, threshold=threshold)
                candidates.append((parameters, threshold, metrics))
    if not candidates:
        raise ValueError('parameter grid must not be empty')
    selected = min(
        candidates,
        key=lambda candidate: (
            -candidate[2].pair_precision,
            -candidate[2].same_event_recall,
            candidate[2].other_event_false_merge_rate,
            candidate[2].hard_negative_false_merge_rate,
            _parameter_key(candidate[0], candidate[1]),
        ),
    )
    return GridSearchResult(
        parameters=selected[0],
        threshold=selected[1],
        metrics=selected[2],
        evaluated_candidate_count=len(candidates),
    )


def _default_lexical_grid() -> tuple[tuple[float, float, float, float], ...]:
    return (
        (0.20, 0.30, 0.30, 0.20),
        (0.25, 0.35, 0.25, 0.15),
        (0.30, 0.30, 0.25, 0.15),
        (0.30, 0.25, 0.30, 0.15),
        (0.35, 0.25, 0.25, 0.15),
        (0.40, 0.25, 0.20, 0.15),
        (0.30, 0.40, 0.20, 0.10),
    )


def _default_scores(
    pairs: Sequence[PairRecord],
    vectors: Mapping[str, Sequence[float]],
    parameters: SimilarityParameters,
) -> tuple[dict[str, float], dict[str, bool]]:
    scores: dict[str, float] = {}
    vetoes: dict[str, bool] = {}
    for pair in pairs:
        result = score_similarity(
            pair.left.title,
            pair.left.summary,
            pair.right.title,
            pair.right.summary,
            dense_score=safe_cosine_similarity(
                cast(Sequence[Real], vectors[pair.left.article_id]),
                cast(Sequence[Real], vectors[pair.right.article_id]),
            ),
            parameters=parameters,
        )
        scores[pair.pair_id] = result.combined_score
        vetoes[pair.pair_id] = result.contradiction_veto
    return scores, vetoes


def _stable_article_id(article_id: str) -> int:
    """Map fixture IDs to positive IDs for the production grouping function."""

    value = int.from_bytes(hashlib.sha256(article_id.encode()).digest()[:8], 'big')
    return value or 1


def _grouping_determinism_rate(
    pairs: Sequence[PairRecord],
    vectors: Mapping[str, Sequence[float]],
    parameters: SimilarityParameters,
    threshold: float,
) -> float:
    """Compare repeated grouping with reversed input order for every pair."""

    if not pairs:
        return 1.0
    matches = 0
    for pair in pairs:
        candidates = (
            ArticleCandidate(
                processed_article_id=_stable_article_id(pair.left.article_id),
                canonical_title=pair.left.title,
                source_summary=pair.left.summary,
                vector=cast(tuple[Real, ...], tuple(vectors[pair.left.article_id])),
            ),
            ArticleCandidate(
                processed_article_id=_stable_article_id(pair.right.article_id),
                canonical_title=pair.right.title,
                source_summary=pair.right.summary,
                vector=cast(tuple[Real, ...], tuple(vectors[pair.right.article_id])),
            ),
        )
        first = group_similar_articles(
            candidates, threshold=threshold, parameters=parameters
        ).to_dict()
        second = group_similar_articles(
            tuple(reversed(candidates)), threshold=threshold, parameters=parameters
        ).to_dict()
        matches += first == second
    return matches / len(pairs)


async def _embed_pairs(
    pairs: Sequence[PairRecord], provider: OllamaEmbeddingProvider
) -> dict[str, list[float]]:
    articles: list[EmbeddingArticle] = []
    article_ids: list[str] = []
    seen: set[str] = set()
    for pair in pairs:
        for article in (pair.left, pair.right):
            if article.article_id not in seen:
                seen.add(article.article_id)
                article_ids.append(article.article_id)
                articles.append(EmbeddingArticle(article.title, article.summary))
    vectors = await provider.embed_articles(articles)
    if len(vectors) != len(article_ids):
        raise ValueError('embedding count mismatch')
    return dict(zip(article_ids, vectors, strict=True))


def _gates(metrics: EvaluationMetrics, runtime_p95_seconds: float) -> dict[str, bool]:
    return {
        'precision_ge_95_percent': metrics.pair_precision >= 0.95,
        'same_event_recall_ge_85_percent': metrics.same_event_recall >= 0.85,
        'other_event_false_merge_le_3_percent': metrics.other_event_false_merge_rate
        <= 0.03,
        'hard_negative_false_merge_zero': metrics.hard_negative_false_merge_rate == 0.0,
        'determinism_100_percent': metrics.determinism_rate == 1.0,
        'mock_pipeline_p95_le_30_seconds': runtime_p95_seconds <= 30.0,
    }


def _build_report(result: Mapping[str, Any]) -> str:
    mode = result['mode']
    holdout = result['holdout_metrics']
    gates = result['gates']
    gate_text = 'PASS' if result['passed'] else 'FAIL'
    lines = [
        '# Article similarity calibration (Task 7)',
        '',
        f'- Overall gate: **{gate_text}** (mode: `{mode}`).',
        f'- Dataset SHA-256: `{result["dataset_sha256"]}`.',
        f'- Labeled pairs: `{result["calibration_pair_count"] + result["holdout_pair_count"]}` (`{result["calibration_pair_count"]}` calibration / `{result["holdout_pair_count"]}` holdout).',
        f'- Split assignment SHA-256: `{result["split_assignment_sha256"]}` (70% calibration / 30% holdout, frozen by pair-ID hash).',
        f'- Grid candidates: `{result["grid_candidate_count"]}`; search was run on calibration pairs only.',
        f'- Selected parameters: `{json.dumps(result["selected_parameters"], sort_keys=True)}`; threshold `{result["selected_threshold"]}`.',
        f'- Grouping algorithm version: `{result["algorithm_version"]}`.',
        '',
        '## Holdout gates',
        '',
        f'- Precision: `{holdout["pair_precision"]:.4f}` (required >= 0.95).',
        f'- SAME_EVENT recall: `{holdout["same_event_recall"]:.4f}` (required >= 0.85).',
        f'- OTHER_EVENT false merge: `{holdout["other_event_false_merge_rate"]:.4f}` (required <= 0.03).',
        f'- HARD_NEGATIVE false merge: `{holdout["hard_negative_false_merge_rate"]:.4f}` (required 0).',
        f'- Repeated-run determinism: `{holdout["determinism_rate"]:.4f}` (required 1.0).',
        '',
        '## Evidence boundary',
        '',
        'This artifact validates the calibration contract and pipeline using deterministic mock embeddings through `httpx.MockTransport`.',
        'It is not evidence of production `bge-m3` model quality, Ollama runtime, host latency, Ollama version, or model digest.',
        f'- Mock algorithm: `{result["embedding_algorithm"]}`; algorithm hash: `{result["embedding_algorithm_sha256"]}`.',
        f'- Mock pipeline p95: `{result["runtime_p95_seconds"]:.6f}s` (non-production; model/network latency excluded).',
        '- Ollama version: **NOT COLLECTED (mock mode)**.',
        '- `bge-m3` model digest: **NOT COLLECTED (mock mode)**.',
        '- Production-host p95: **NOT MEASURED**.',
        '',
        '## Gate detail',
        '',
    ]
    lines.extend(f'- `{name}`: `{value}`.' for name, value in gates.items())
    lines.extend(
        [
            '',
            'The selected `SimilarityParameters` are provisional for this mock calibration and are frozen in production code only because the recorded mock holdout gate passed.',
            'Run the explicitly opted-in `--live` mode separately before treating them as evidence about the configured production model.',
            '',
        ]
    )
    return '\n'.join(lines)


async def run_calibration(
    *,
    dataset_path: Path = DATASET_PATH,
    report_path: Path | None = REPORT_PATH,
    result_path: Path | None = RESULT_PATH,
    mode: Literal['mock', 'live'] = 'mock',
    provider: OllamaEmbeddingProvider | None = None,
    mock_transport: MockEmbeddingTransport | None = None,
) -> CalibrationResult:
    """Run calibration, with a single holdout evaluation after grid selection."""

    pairs = load_dataset(dataset_path)
    split = split_dataset(pairs)
    started = time.perf_counter()
    owned_client: httpx.AsyncClient | None = None
    transport = mock_transport
    if provider is None and mode == 'mock':
        transport = transport or MockEmbeddingTransport()
        settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            ollama_base_url='http://mock.invalid',
            ollama_embed_model='mock-bge-m3',
            ollama_timeout_seconds=1.0,
            ollama_max_retries=0,
        )
        owned_client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        provider = OllamaEmbeddingProvider(settings, client=owned_client)
    elif provider is None and mode == 'live':
        provider = OllamaEmbeddingProvider(Settings())
    elif provider is None:
        raise ValueError('mode must be mock or live')
    try:
        vectors = await _embed_pairs(pairs, provider)
    finally:
        if owned_client is not None:
            await owned_client.aclose()
    calibration_scores: dict[
        tuple[float, ...], tuple[dict[str, float], dict[str, bool]]
    ] = {}

    def score_provider(
        parameters: SimilarityParameters, _threshold: float
    ) -> Mapping[str, float]:
        key = _parameter_key(parameters, _threshold)
        if key not in calibration_scores:
            calibration_scores[key] = _default_scores(
                split.calibration, vectors, parameters
            )
        scores, vetoes = calibration_scores[key]
        return {
            pair_id: score if not vetoes.get(pair_id, False) else 0.0
            for pair_id, score in scores.items()
        }

    grid = grid_search(
        split.calibration,
        score_provider=score_provider,
        lexical_weight_grid=_default_lexical_grid(),
        dense_lexical_grid=((0.6, 0.4), (0.7, 0.3), (0.8, 0.2)),
        threshold_grid=(0.45, 0.55, 0.65, 0.75, 0.80, 0.85, 0.90),
    )
    holdout_scores, holdout_vetoes = _default_scores(
        split.holdout, vectors, grid.parameters
    )
    holdout = evaluate_metrics(
        split.holdout,
        scores=holdout_scores,
        threshold=grid.threshold,
        vetoes=holdout_vetoes,
        deterministic_runs=2,
    )
    elapsed = time.perf_counter() - started
    runtime_samples = [elapsed]
    if mode == 'mock':
        for _ in range(4):
            sample_started = time.perf_counter()
            _default_scores(pairs, vectors, grid.parameters)
            runtime_samples.append(time.perf_counter() - sample_started)
    runtime_p95 = (
        statistics.quantiles(runtime_samples, n=20, method='inclusive')[-1]
        if len(runtime_samples) > 1
        else runtime_samples[0]
    )
    holdout = dataclass_replace(
        holdout,
        determinism_rate=_grouping_determinism_rate(
            split.holdout, vectors, grid.parameters, grid.threshold
        ),
    )
    dataset_payload = json.loads(dataset_path.read_text(encoding='utf-8'))
    algorithm_hash = hashlib.sha256(MOCK_EMBEDDING_ALGORITHM.encode()).hexdigest()
    gates = _gates(holdout, runtime_p95)
    result_payload: dict[str, Any] = {
        'schema_version': 'article-similarity-calibration-result-v1',
        'mode': mode,
        'dataset_sha256': dataset_payload['dataset_sha256'],
        'dataset_schema_version': DATASET_SCHEMA_VERSION,
        'algorithm_version': GROUPING_ALGORITHM_VERSION,
        'split_assignment_sha256': split.assignment_sha256,
        'calibration_pair_count': len(split.calibration),
        'holdout_pair_count': len(split.holdout),
        'grid_candidate_count': grid.evaluated_candidate_count,
        'selected_parameters': asdict(grid.parameters),
        'selected_threshold': grid.threshold,
        'calibration_metrics': grid.metrics.to_dict(),
        'holdout_metrics': holdout.to_dict(),
        'gates': gates,
        'passed': all(gates.values()),
        'embedding_algorithm': MOCK_EMBEDDING_ALGORITHM
        if mode == 'mock'
        else 'configured-ollama',
        'embedding_algorithm_sha256': algorithm_hash if mode == 'mock' else None,
        'runtime_p95_seconds': runtime_p95,
        'runtime_evidence_scope': 'mock-pipeline-only; non-production model/network latency evidence',
        'ollama_version': 'NOT_COLLECTED (mock mode)'
        if mode == 'mock'
        else 'operator-supplied',
        'bge_m3_model_digest': 'NOT_COLLECTED (mock mode)'
        if mode == 'mock'
        else 'operator-supplied',
    }
    report = _build_report(result_payload)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding='utf-8')
    if result_path is not None:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2, sort_keys=True)
            + '\n',
            encoding='utf-8',
        )
    return CalibrationResult(result=result_payload, report=report)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--mock', action='store_const', const='mock', dest='mode')
    mode.add_argument('--live', action='store_const', const='live', dest='mode')
    parser.add_argument('--dataset', type=Path, default=DATASET_PATH)
    parser.add_argument('--report', type=Path, default=REPORT_PATH)
    parser.add_argument('--result', type=Path, default=RESULT_PATH)
    args = parser.parse_args(argv)
    result = asyncio.run(
        run_calibration(
            dataset_path=args.dataset,
            report_path=args.report,
            result_path=args.result,
            mode=args.mode,
        )
    )
    print(
        json.dumps(
            {
                'status': 'PASS' if result.result['passed'] else 'FAIL',
                'report': str(args.report),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
