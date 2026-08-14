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
import itertools
import json
import math
import re
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from dataclasses import replace as dataclass_replace
from datetime import UTC, datetime
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
from app.batch.steps.group_similar_articles import build_grouping_algorithm_version
from app.core.settings import Settings
from app.core.text import normalize_text

DATASET_PATH = Path('tests/fixtures/article_similarity_pairs.json')
REPORT_PATH = Path('docs/evaluations/2026-08-13-article-similarity.md')
RESULT_PATH = Path('docs/evaluations/2026-08-13-article-similarity.json')
MOCK_EMBEDDING_ALGORITHM = 'mock-token-hash-v1'
MOCK_EMBEDDING_DIMENSION = 48
DATASET_SCHEMA_VERSION = 'article-similarity-pairs-v2'
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
    event_family_id: str
    left: ArticleText
    right: ArticleText
    hard_negative_type: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetSplit:
    """Hash-frozen, label-stratified calibration and holdout partitions."""

    calibration: tuple[PairRecord, ...]
    holdout: tuple[PairRecord, ...]
    assignment_sha256: str
    assignments: tuple[dict[str, str], ...]


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
    expected_root_keys = {
        'schema_version',
        'curation',
        'pairs',
        'dataset_sha256',
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_root_keys
        or payload.get('schema_version') != DATASET_SCHEMA_VERSION
        or not isinstance(payload.get('curation'), str)
        or not payload['curation'].strip()
        or not isinstance(payload.get('pairs'), list)
    ):
        raise ValueError('dataset must contain a pairs list')
    declared_hash = payload.get('dataset_sha256')
    if declared_hash != _dataset_hash(payload):
        raise ValueError('dataset hash does not match canonical content')
    records: list[PairRecord] = []
    seen_ids: set[str] = set()
    seen_article_ids: set[str] = set()
    expected_pair_keys = {
        'pair_id',
        'market',
        'label',
        'event_family_id',
        'left',
        'right',
        'hard_negative_type',
    }
    expected_article_keys = {'article_id', 'title', 'summary'}
    hard_negative_types = {'numeric', 'date', 'direction'}
    for raw in payload['pairs']:
        if not isinstance(raw, dict) or set(raw) != expected_pair_keys:
            raise ValueError('dataset pair must be an object')
        pair_id = raw.get('pair_id')
        market = raw.get('market')
        label = raw.get('label')
        event_family_id = raw.get('event_family_id')
        if (
            not isinstance(pair_id, str)
            or not pair_id.strip()
            or pair_id in seen_ids
            or market not in {'KR', 'US'}
            or label not in LABELS
            or not isinstance(event_family_id, str)
            or not event_family_id.strip()
        ):
            raise ValueError('dataset pair has invalid or duplicate identity/label')
        left = raw.get('left')
        right = raw.get('right')
        if (
            not isinstance(left, dict)
            or not isinstance(right, dict)
            or set(left) != expected_article_keys
            or set(right) != expected_article_keys
        ):
            raise ValueError('dataset pair requires left and right article objects')
        hard_type = raw.get('hard_negative_type')
        if label == 'HARD_NEGATIVE' and hard_type not in hard_negative_types:
            raise ValueError('hard negatives require hard_negative_type')
        if label != 'HARD_NEGATIVE' and hard_type is not None:
            raise ValueError('only hard negatives may have hard_negative_type')
        parsed_left = _article_from_payload(left)
        parsed_right = _article_from_payload(right)
        if (
            parsed_left.article_id in seen_article_ids
            or parsed_right.article_id in seen_article_ids
            or parsed_left.article_id == parsed_right.article_id
        ):
            raise ValueError('article IDs must be globally unique')
        records.append(
            PairRecord(
                pair_id=pair_id,
                market=market,
                label=label,
                event_family_id=event_family_id,
                left=parsed_left,
                right=parsed_right,
                hard_negative_type=hard_type,
            )
        )
        seen_ids.add(pair_id)
        seen_article_ids.update((parsed_left.article_id, parsed_right.article_id))
    if len(records) < 300:
        raise ValueError('calibration dataset must contain at least 300 pairs')
    if {record.market for record in records} != {'KR', 'US'}:
        raise ValueError('calibration dataset must cover KR and US markets')
    if {record.label for record in records} != LABELS:
        raise ValueError('calibration dataset must contain all required labels')
    for label in LABELS:
        for market in ('KR', 'US'):
            families = {
                record.event_family_id
                for record in records
                if record.label == label and record.market == market
            }
            if len(families) < 2:
                raise ValueError(
                    'each label and market needs at least two event families'
                )
    return tuple(records)


def split_dataset(pairs: Iterable[PairRecord]) -> DatasetSplit:
    """Freeze a deterministic approximate 70/30 split without content leakage.

    A family is an assignment unit, and families sharing any normalized
    ``(title, summary)`` fingerprint are unioned into one content component.
    Whole components are assigned together, then selected per label/market
    bucket with a deterministic closest-to-70-percent objective.
    """

    records = tuple(pairs)
    components = _content_components(records)
    buckets: dict[tuple[str, str], list[PairRecord]] = defaultdict(list)
    for pair in records:
        buckets[(pair.label, pair.market)].append(pair)
    bucket_totals = {
        bucket: len(bucket_pairs) for bucket, bucket_pairs in buckets.items()
    }
    bucket_targets = {
        bucket: round(count * 0.7) for bucket, count in bucket_totals.items()
    }
    component_groups = _component_interaction_groups(components)
    calibration_component_ids: set[str] = set()
    for group_components in component_groups:
        group_buckets = {
            bucket
            for component in group_components
            for bucket in component['bucket_counts']
        }
        calibration_component_ids.update(
            _select_calibration_components(
                group_components,
                bucket_targets={
                    bucket: bucket_targets[bucket] for bucket in group_buckets
                },
                bucket_totals={
                    bucket: bucket_totals[bucket] for bucket in group_buckets
                },
            )
        )
    calibration: list[PairRecord] = []
    holdout: list[PairRecord] = []
    assignments: list[dict[str, str]] = []
    component_by_pair_id = {
        pair.pair_id: component
        for component in components
        for pair in component['pairs']
    }
    for pair in sorted(records, key=lambda item: item.pair_id):
        component = component_by_pair_id[pair.pair_id]
        target = (
            'calibration'
            if component['component_id'] in calibration_component_ids
            else 'holdout'
        )
        (calibration if target == 'calibration' else holdout).append(pair)
        assignments.append(
            {
                'pair_id': pair.pair_id,
                'event_family_id': pair.event_family_id,
                'label': pair.label,
                'market': pair.market,
                'content_component_id': component['component_id'],
                'split': target,
            }
        )
    assignments.sort(key=lambda item: item['pair_id'])
    assignment_hash = hashlib.sha256(_canonical_json(assignments).encode()).hexdigest()
    return DatasetSplit(
        tuple(sorted(calibration, key=lambda item: item.pair_id)),
        tuple(sorted(holdout, key=lambda item: item.pair_id)),
        assignment_hash,
        tuple(assignments),
    )


def _content_fingerprint(article: ArticleText) -> tuple[str, str]:
    return normalize_text(article.title), normalize_text(article.summary)


def _family_key(pair: PairRecord) -> tuple[str, str, str]:
    return pair.label, pair.market, pair.event_family_id


def _content_components(
    pairs: Sequence[PairRecord],
) -> tuple[dict[str, Any], ...]:
    """Union event families that share exact normalized article content."""

    family_parent: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    fingerprint_owner: dict[tuple[str, str], tuple[str, str, str]] = {}

    def find(family: tuple[str, str, str]) -> tuple[str, str, str]:
        parent = family_parent[family]
        if parent != family:
            family_parent[family] = find(parent)
        return family_parent[family]

    def union(left: tuple[str, str, str], right: tuple[str, str, str]) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            family_parent[right_root] = left_root
        else:
            family_parent[left_root] = right_root

    for pair in pairs:
        family = _family_key(pair)
        family_parent.setdefault(family, family)
        for article in (pair.left, pair.right):
            fingerprint = _content_fingerprint(article)
            owner = fingerprint_owner.get(fingerprint)
            if owner is None:
                fingerprint_owner[fingerprint] = family
            else:
                union(family, owner)

    records_by_root: dict[tuple[str, str, str], list[PairRecord]] = defaultdict(list)
    for pair in pairs:
        records_by_root[find(_family_key(pair))].append(pair)

    components: list[dict[str, Any]] = []
    for _root, component_pairs in records_by_root.items():
        families = sorted({_family_key(pair) for pair in component_pairs})
        component_id = hashlib.sha256(_canonical_json(families).encode()).hexdigest()
        bucket_counts: dict[tuple[str, str], int] = defaultdict(int)
        for pair in component_pairs:
            bucket_counts[(pair.label, pair.market)] += 1
        components.append(
            {
                'component_id': component_id,
                'families': tuple(families),
                'pairs': tuple(sorted(component_pairs, key=lambda item: item.pair_id)),
                'bucket_counts': dict(bucket_counts),
            }
        )
    return tuple(sorted(components, key=lambda item: item['component_id']))


def _component_interaction_groups(
    components: Sequence[dict[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], ...]:
    """Group components sharing label/market buckets for local subset search."""

    bucket_parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(bucket: tuple[str, str]) -> tuple[str, str]:
        parent = bucket_parent[bucket]
        if parent != bucket:
            bucket_parent[bucket] = find(parent)
        return bucket_parent[bucket]

    def union(left: tuple[str, str], right: tuple[str, str]) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            bucket_parent[right_root] = left_root
        else:
            bucket_parent[left_root] = right_root

    for component in components:
        buckets = sorted(component['bucket_counts'])
        for bucket in buckets:
            bucket_parent.setdefault(bucket, bucket)
        for bucket in buckets[1:]:
            union(buckets[0], bucket)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for component in components:
        buckets = sorted(component['bucket_counts'])
        grouped[find(buckets[0])].append(component)
    return tuple(
        tuple(sorted(group, key=lambda item: item['component_id']))
        for _, group in sorted(grouped.items())
    )


def _selection_key(
    selected: Sequence[dict[str, Any]],
    *,
    bucket_targets: Mapping[tuple[str, str], int],
    bucket_totals: Mapping[tuple[str, str], int],
) -> tuple[float, int, int, tuple[str, ...]]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for component in selected:
        for bucket, count in component['bucket_counts'].items():
            counts[bucket] += count
    normalized_error = sum(
        abs(counts[bucket] - bucket_targets[bucket]) / bucket_totals[bucket]
        for bucket in bucket_targets
    )
    partition_penalty = sum(
        counts[bucket] in {0, bucket_totals[bucket]} for bucket in bucket_targets
    )
    total_error = abs(sum(counts.values()) - round(sum(bucket_totals.values()) * 0.7))
    return (
        normalized_error,
        partition_penalty,
        total_error,
        tuple(component['component_id'] for component in selected),
    )


def _select_calibration_components(
    components: Sequence[dict[str, Any]],
    *,
    bucket_targets: Mapping[tuple[str, str], int],
    bucket_totals: Mapping[tuple[str, str], int],
) -> set[str]:
    """Select whole content components with deterministic tie breaking."""

    if not components:
        return set()
    if len(components) <= 20:
        candidates: Iterable[tuple[int, ...]] = (
            indexes
            for count in range(len(components) + 1)
            for indexes in itertools.combinations(range(len(components)), count)
        )
        selected_indexes = min(
            candidates,
            key=lambda indexes: _selection_key(
                [components[index] for index in indexes],
                bucket_targets=bucket_targets,
                bucket_totals=bucket_totals,
            ),
        )
        return {components[index]['component_id'] for index in selected_indexes}

    selected: list[dict[str, Any]] = []
    for component in components:
        candidate = [*selected, component]
        if _selection_key(
            candidate, bucket_targets=bucket_targets, bucket_totals=bucket_totals
        ) < _selection_key(
            selected, bucket_targets=bucket_targets, bucket_totals=bucket_totals
        ):
            selected.append(component)
    return {component['component_id'] for component in selected}


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


def _implementation_sha256() -> str:
    """Hash the checked-in implementation bytes used by the mock evaluator."""

    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


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
            candidate[2].other_event_false_merge_count
            + candidate[2].hard_negative_false_merge_count,
            candidate[2].hard_negative_false_merge_count,
            candidate[2].other_event_false_merge_count,
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


def _prediction_records(
    pairs: Sequence[PairRecord],
    *,
    scores: Mapping[str, float],
    vetoes: Mapping[str, bool],
    threshold: float,
    split: Literal['calibration', 'holdout'],
) -> list[dict[str, Any]]:
    """Serialize auditable pair predictions without copying article bodies."""

    return [
        {
            'pair_id': pair.pair_id,
            'market': pair.market,
            'label': pair.label,
            'event_family_id': pair.event_family_id,
            'left_article_id': pair.left.article_id,
            'right_article_id': pair.right.article_id,
            'hard_negative_type': pair.hard_negative_type,
            'split': split,
            'score': scores[pair.pair_id],
            'vetoed': vetoes.get(pair.pair_id, False),
            'predicted_same_event': scores[pair.pair_id] >= threshold
            and not vetoes.get(pair.pair_id, False),
        }
        for pair in sorted(pairs, key=lambda item: item.pair_id)
    ]


def _stable_article_id(article_id: str) -> int:
    """Map fixture IDs to positive IDs for the production grouping function."""

    value = int.from_bytes(hashlib.sha256(article_id.encode()).digest()[:8], 'big')
    return value or 1


def _determinism_cases() -> tuple[tuple[ArticleCandidate, ...], ...]:
    """Return multi-article chain and contradiction cases for grouping audits."""

    first_time = datetime(2026, 8, 14, 12, tzinfo=UTC)
    chain = (
        ArticleCandidate(
            processed_article_id=1001,
            canonical_title='alpha event',
            source_summary='alpha revenue update',
            published_at=first_time,
            vector=cast(tuple[Real, ...], (1.0, 0.0)),
        ),
        ArticleCandidate(
            processed_article_id=1002,
            canonical_title='alpha beta event',
            source_summary='alpha beta revenue update',
            published_at=first_time,
            vector=cast(tuple[Real, ...], (0.8, 0.6)),
        ),
        ArticleCandidate(
            processed_article_id=1003,
            canonical_title='beta event',
            source_summary='beta revenue update',
            published_at=first_time,
            vector=cast(tuple[Real, ...], (0.0, 1.0)),
        ),
    )
    hard = (
        dataclass_replace(chain[0], source_summary='alpha revenue 10억원 증가'),
        *chain[1:],
        ArticleCandidate(
            processed_article_id=1004,
            canonical_title='alpha event',
            source_summary='alpha revenue 11억원 증가',
            published_at=first_time,
            vector=cast(tuple[Real, ...], (1.0, 0.0)),
        ),
    )
    return chain, hard


def _grouping_determinism_checks(
    cases: Sequence[Sequence[ArticleCandidate]],
    parameters: SimilarityParameters,
    threshold: float,
    *,
    runs: int = 3,
) -> tuple[int, int]:
    """Run complete-link grouping repeatedly across every input permutation."""

    matches = 0
    checks = 0
    for case in cases:
        permutations = tuple(itertools.permutations(case))
        expected = group_similar_articles(
            case, threshold=threshold, parameters=parameters
        ).to_dict()
        for _ in range(runs):
            for permutation in permutations:
                actual = group_similar_articles(
                    permutation, threshold=threshold, parameters=parameters
                ).to_dict()
                matches += actual == expected
                checks += 1
    return matches, checks


def _grouping_determinism_rate(
    cases: Sequence[Sequence[ArticleCandidate]] | None = None,
    parameters: SimilarityParameters | None = None,
    threshold: float = 0.45,
    *,
    runs: int = 3,
) -> float:
    """Return repeated multi-article grouping agreement across permutations."""

    selected_cases = cases or _determinism_cases()
    matches, checks = _grouping_determinism_checks(
        selected_cases, parameters or SimilarityParameters(), threshold, runs=runs
    )
    return matches / checks if checks else 1.0


async def _measure_cluster_runtime_samples(
    provider: OllamaEmbeddingProvider,
    cases: Sequence[Sequence[ArticleCandidate]],
    parameters: SimilarityParameters,
    threshold: float,
    *,
    runs: int = 3,
) -> list[float]:
    """Measure full mock/provider embed + score + group time per cluster run."""

    samples: list[float] = []
    for _ in range(runs):
        for case in cases:
            inputs = [
                EmbeddingArticle(
                    canonical_title=article.canonical_title,
                    source_summary=article.source_summary,
                )
                for article in case
            ]
            started = time.perf_counter()
            vectors = await provider.embed_articles(inputs)
            if len(vectors) != len(case):
                raise ValueError('runtime embedding count mismatch')
            candidates = [
                dataclass_replace(article, vector=tuple(vectors[index]))
                for index, article in enumerate(case)
            ]
            group_similar_articles(
                candidates, threshold=threshold, parameters=parameters
            )
            samples.append(time.perf_counter() - started)
    return samples


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


def _percentile(values: Sequence[float], percentile: float) -> float:
    """Return an interpolated percentile from non-empty runtime samples."""

    if not values:
        raise ValueError('runtime samples must not be empty')
    if not 0.0 <= percentile <= 1.0:
        raise ValueError('percentile must be between 0 and 1')
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _gates(
    metrics: EvaluationMetrics,
    runtime_p95_seconds: float,
    *,
    mode: Literal['mock', 'live'],
) -> dict[str, bool]:
    gates = {
        'precision_ge_95_percent': metrics.pair_precision >= 0.95,
        'same_event_recall_ge_85_percent': metrics.same_event_recall >= 0.85,
        'other_event_false_merge_le_3_percent': metrics.other_event_false_merge_rate
        <= 0.03,
        'hard_negative_false_merge_zero': metrics.hard_negative_false_merge_rate == 0.0,
        'determinism_100_percent': metrics.determinism_rate == 1.0,
    }
    runtime_gate = (
        'mock_pipeline_p95_le_30_seconds'
        if mode == 'mock'
        else 'live_pipeline_p95_le_30_seconds'
    )
    gates[runtime_gate] = runtime_p95_seconds <= 30.0
    return gates


def _build_report(result: Mapping[str, Any]) -> str:
    mode = result['mode']
    holdout = result['holdout_metrics']
    gates = result['gates']
    gate_text = 'PASS' if result['passed'] else 'FAIL'
    total_pairs = result['calibration_pair_count'] + result['holdout_pair_count']
    calibration_percent = result['calibration_pair_count'] / total_pairs * 100
    holdout_percent = result['holdout_pair_count'] / total_pairs * 100
    if mode == 'mock':
        evidence_lines = [
            'This artifact validates the calibration contract and pipeline with a repository-curated mock fixture using deterministic mock embeddings through `httpx.MockTransport`.',
            'The dataset is a synthetic deterministic contract fixture with fixture annotations, not a manually labeled real-news calibration corpus.',
            'It is not evidence of production `bge-m3` model quality, Ollama runtime, host latency, Ollama version, or model digest.',
            f'- Mock algorithm: `{result["embedding_algorithm"]}`; implementation source SHA-256: `{result["embedding_algorithm_sha256"]}`.',
            f'- Mock full-pipeline per-cluster p95: `{result["runtime_p95_seconds"]:.6f}s` (non-production; model/network latency evidence only for the mock transport).',
            '- Ollama version: **NOT COLLECTED (mock mode)**.',
            '- `bge-m3` model digest: **NOT COLLECTED (mock mode)**.',
            '- Production-host p95: **NOT MEASURED**.',
        ]
    else:
        evidence_lines = [
            'This run used the configured Ollama `/api/embed` provider; no mock-model quality claim is made.',
            f'- Provider: `{result["provider"]}`; model: `{result["model"]}`; input cap: `{result["input_chars"]}` characters.',
            f'- Live per-cluster p95: `{result["runtime_p95_seconds"]:.6f}s` (operator-run measurement).',
            '- Ollama version: **NOT COLLECTED BY THIS SCRIPT**.',
            '- `bge-m3` model digest: **NOT COLLECTED BY THIS SCRIPT**.',
        ]
    lines = [
        '# Article similarity mock contract evaluation (Task 7)',
        '',
        f'- Decision/status: **`{result["status"]}`**.',
        f'- Mock arithmetic gates: **{gate_text}** (mode: `{mode}`; this is not real-model acceptance).',
        '- Approved real-model calibration gate: **UNVERIFIED — REAL `bge-m3` CALIBRATION REQUIRED**.',
        f'- Dataset SHA-256: `{result["dataset_sha256"]}`.',
        f'- Synthetic fixture pairs: `{total_pairs}` (`{result["calibration_pair_count"]}` calibration / `{result["holdout_pair_count"]}` holdout = `{calibration_percent:.3f}%` / `{holdout_percent:.3f}%`; approximate 70/30 constrained by label/market, event family, and normalized content components).',
        f'- Split assignment SHA-256: `{result["split_assignment_sha256"]}` (content components never cross partitions).',
        f'- Grid candidates: `{result["grid_candidate_count"]}`; search was run on calibration pairs only.',
        f'- Selected parameters: `{json.dumps(result["selected_parameters"], sort_keys=True)}`; threshold `{result["selected_threshold"]}`.',
        f'- Grouping algorithm version: `{result["algorithm_version"]}`.',
        f'- Determinism audit: `{result["determinism_check_count"]}` checks across `{result["determinism_case_count"]}` multi-article clusters, `{result["determinism_runs"]}` runs, and all input permutations.',
        f'- Runtime samples: `{result["runtime_sample_count"]}` per-cluster full-pipeline measurements across `{result["runtime_cluster_count"]}` clusters.',
        '',
        '## Mock holdout arithmetic gates (not real-model acceptance)',
        '',
        f'- Precision: `{holdout["pair_precision"]:.4f}` (required >= 0.95).',
        f'- SAME_EVENT recall: `{holdout["same_event_recall"]:.4f}` (required >= 0.85).',
        f'- OTHER_EVENT false merge: `{holdout["other_event_false_merge_rate"]:.4f}` (required <= 0.03).',
        f'- HARD_NEGATIVE false merge: `{holdout["hard_negative_false_merge_rate"]:.4f}` (required 0).',
        f'- Repeated-run determinism: `{holdout["determinism_rate"]:.4f}` (required 1.0).',
        '',
        '## Evidence boundary',
        '',
        *evidence_lines,
        '',
        '## Gate detail',
        '',
    ]
    lines.extend(f'- `{name}`: `{value}`.' for name, value in gates.items())
    lines.extend(
        [
            '',
            'Grid tie-break order is precision, SAME_EVENT recall, total false merges, HARD_NEGATIVE false merges, OTHER_EVENT false merges, then canonical parameter order; contradiction vetoes are applied during calibration and holdout.',
            'The selected `SimilarityParameters` are provisional for this synthetic deterministic contract fixture, are not release-calibrated, and must not be treated as production-model evidence or a satisfied real holdout gate.',
            'Run the explicitly opted-in `--live` mode separately against the configured provider before any production-quality or release-calibration decision.',
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
    if mode not in {'mock', 'live'}:
        raise ValueError('mode must be mock or live')
    if mode == 'mock' and provider is not None:
        raise ValueError('mock mode does not accept a provider; use mock_transport')
    if mock_transport is not None and not isinstance(
        mock_transport, MockEmbeddingTransport
    ):
        raise TypeError('mock_transport must be MockEmbeddingTransport')

    pairs = load_dataset(dataset_path)
    split = split_dataset(pairs)
    started = time.perf_counter()
    owned_client: httpx.AsyncClient | None = None
    transport = mock_transport
    if mode == 'mock':
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
    else:
        settings = Settings()
        provider = provider or OllamaEmbeddingProvider(settings)

    try:
        vectors = await _embed_pairs(pairs, provider)
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
        calibration_selected_scores, calibration_selected_vetoes = _default_scores(
            split.calibration, vectors, grid.parameters
        )
        holdout_scores, holdout_vetoes = _default_scores(
            split.holdout, vectors, grid.parameters
        )
        holdout = evaluate_metrics(
            split.holdout,
            scores=holdout_scores,
            threshold=grid.threshold,
            vetoes=holdout_vetoes,
        )
        determinism_cases = _determinism_cases()
        determinism_matches, determinism_checks = _grouping_determinism_checks(
            determinism_cases, grid.parameters, grid.threshold, runs=3
        )
        holdout = dataclass_replace(
            holdout,
            determinism_rate=(
                determinism_matches / determinism_checks if determinism_checks else 0.0
            ),
        )
        runtime_samples = await _measure_cluster_runtime_samples(
            provider,
            determinism_cases,
            grid.parameters,
            grid.threshold,
            runs=3,
        )
        runtime_p95 = _percentile(runtime_samples, 0.95)
        algorithm_version = build_grouping_algorithm_version(
            model=settings.ollama_embed_model,
            input_chars=settings.similarity_input_chars,
            parameters=grid.parameters,
            threshold=grid.threshold,
        )
        dataset_payload = json.loads(dataset_path.read_text(encoding='utf-8'))
        gates = _gates(holdout, runtime_p95, mode=mode)
        pair_predictions = _prediction_records(
            split.calibration,
            scores=calibration_selected_scores,
            vetoes=calibration_selected_vetoes,
            threshold=grid.threshold,
            split='calibration',
        ) + _prediction_records(
            split.holdout,
            scores=holdout_scores,
            vetoes=holdout_vetoes,
            threshold=grid.threshold,
            split='holdout',
        )
        status = (
            'MOCK_PIPELINE_PASS_REAL_BGE_M3_CALIBRATION_REQUIRED'
            if mode == 'mock' and all(gates.values())
            else 'MOCK_PIPELINE_FAIL_REAL_BGE_M3_CALIBRATION_REQUIRED'
            if mode == 'mock'
            else 'LIVE_CALIBRATION_OPERATOR_RESULT'
        )
        result_payload: dict[str, Any] = {
            'schema_version': 'article-similarity-calibration-result-v2',
            'status': status,
            'decision': status,
            'gate_scope': 'mock-contract-only'
            if mode == 'mock'
            else 'live-operator-run',
            'mock_gate_status': 'PASS' if all(gates.values()) else 'FAIL',
            'real_model_calibration_status': (
                'UNVERIFIED_REQUIRED' if mode == 'mock' else 'OPERATOR_REVIEW_REQUIRED'
            ),
            'real_model_calibration_approved': False,
            'mode': mode,
            'provider': 'httpx.MockTransport'
            if mode == 'mock'
            else 'ollama:/api/embed',
            'model': settings.ollama_embed_model,
            'input_chars': settings.similarity_input_chars,
            'dataset_sha256': dataset_payload['dataset_sha256'],
            'dataset_schema_version': DATASET_SCHEMA_VERSION,
            'dataset_curation': dataset_payload['curation'],
            'algorithm_version': algorithm_version,
            'split_assignment_sha256': split.assignment_sha256,
            'split_assignments': list(split.assignments),
            'calibration_pair_count': len(split.calibration),
            'holdout_pair_count': len(split.holdout),
            'grid_candidate_count': grid.evaluated_candidate_count,
            'selected_parameters': asdict(grid.parameters),
            'selected_threshold': grid.threshold,
            'calibration_metrics': grid.metrics.to_dict(),
            'holdout_metrics': holdout.to_dict(),
            'pair_predictions': pair_predictions,
            'gates': gates,
            'passed': all(gates.values()),
            'embedding_algorithm': (
                MOCK_EMBEDDING_ALGORITHM if mode == 'mock' else 'configured-ollama'
            ),
            'embedding_algorithm_sha256': (
                _implementation_sha256() if mode == 'mock' else None
            ),
            'runtime_p95_seconds': runtime_p95,
            'runtime_sample_count': len(runtime_samples),
            'runtime_cluster_count': len(determinism_cases),
            'runtime_evidence_scope': (
                'mock-full-pipeline-per-cluster; non-production evidence'
                if mode == 'mock'
                else 'operator live full-pipeline per-cluster measurement'
            ),
            'determinism_case_count': len(determinism_cases),
            'determinism_min_articles': min(len(case) for case in determinism_cases),
            'determinism_runs': 3,
            'determinism_check_count': determinism_checks,
            'ollama_version': 'NOT_COLLECTED BY THIS SCRIPT',
            'bge_m3_model_digest': 'NOT_COLLECTED BY THIS SCRIPT',
            'elapsed_seconds': time.perf_counter() - started,
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
    finally:
        if owned_client is not None:
            await owned_client.aclose()


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
                'status': result.result['status'],
                'mock_gate_status': result.result['mock_gate_status'],
                'report': str(args.report),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
