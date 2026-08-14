from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, cast

import httpx
import pytest

from app.batch.article_similarity import SimilarityParameters
from scripts.calibrate_article_similarity import (
    ArticleText,
    PairRecord,
    evaluate_metrics,
    grid_search,
    load_dataset,
    mock_embedding,
    split_dataset,
)

PairLabel = Literal['SAME_EVENT', 'OTHER_EVENT', 'HARD_NEGATIVE']


def _pair(
    pair_id: str,
    label: str,
    *,
    left_title: str = '삼성전자 실적 발표',
    right_title: str = '삼성전자 분기 실적 발표',
) -> PairRecord:
    return PairRecord(
        pair_id=pair_id,
        market='KR',
        label=cast(PairLabel, label),
        left=ArticleText(
            article_id=f'{pair_id}-left',
            title=left_title,
            summary='영업이익 120억원 증가',
        ),
        right=ArticleText(
            article_id=f'{pair_id}-right',
            title=right_title,
            summary='영업이익 120억원 늘어',
        ),
        hard_negative_type=None,
    )


def test_hash_split_is_stratified_stable_and_disjoint() -> None:
    pairs = tuple(
        _pair(f'pair-{index:03d}', label)
        for index, label in enumerate(
            ['SAME_EVENT'] * 10 + ['OTHER_EVENT'] * 10 + ['HARD_NEGATIVE'] * 10
        )
    )

    first = split_dataset(pairs)
    second = split_dataset(tuple(reversed(pairs)))

    assert first == second
    assert len(first.calibration) == 21
    assert len(first.holdout) == 9
    assert {item.pair_id for item in first.calibration}.isdisjoint(
        item.pair_id for item in first.holdout
    )
    assert first.assignment_sha256 == second.assignment_sha256
    for label in ('SAME_EVENT', 'OTHER_EVENT', 'HARD_NEGATIVE'):
        assert sum(item.label == label for item in first.calibration) == 7
        assert sum(item.label == label for item in first.holdout) == 3


def test_mock_embedding_depends_only_on_normalized_text_not_label() -> None:
    text = '삼성전자  실적 발표\n영업이익 증가'

    assert mock_embedding(text) == mock_embedding(text)
    assert mock_embedding(text) != mock_embedding('현대차 생산 확대')
    assert mock_embedding(text) == mock_embedding(text, label='OTHER_EVENT')


def test_metrics_apply_pair_gates_and_hard_negative_rate() -> None:
    pairs = (
        _pair('same', 'SAME_EVENT'),
        _pair(
            'other',
            'OTHER_EVENT',
            left_title='삼성전자 실적',
            right_title='현대차 생산',
        ),
        _pair(
            'hard',
            'HARD_NEGATIVE',
            left_title='삼성전자 실적 증가',
            right_title='삼성전자 실적 감소',
        ),
    )
    metrics = evaluate_metrics(
        pairs,
        scores={'same': 0.9, 'other': 0.2, 'hard': 0.95},
        threshold=0.8,
        deterministic_runs=2,
    )

    assert metrics.precision == pytest.approx(0.5)
    assert metrics.same_event_recall == pytest.approx(1.0)
    assert metrics.other_event_false_merge_rate == pytest.approx(0.0)
    assert metrics.hard_negative_false_merge_rate == pytest.approx(1.0)
    assert metrics.determinism_rate == pytest.approx(1.0)


def test_grid_search_uses_calibration_only_and_deterministic_tie_break() -> None:
    pairs = (
        _pair('same', 'SAME_EVENT'),
        _pair('other', 'OTHER_EVENT', left_title='삼성전자', right_title='현대차'),
    )
    scores_by_candidate = {
        (0.2, 0.3, 0.3, 0.2, 0.7, 0.3, 0.8): {'same': 0.9, 'other': 0.1},
        (0.3, 0.3, 0.2, 0.2, 0.7, 0.3, 0.8): {'same': 0.9, 'other': 0.1},
    }

    result = grid_search(
        pairs,
        score_provider=lambda parameters, threshold: scores_by_candidate[
            (
                parameters.title_weight,
                parameters.full_text_weight,
                parameters.numeric_date_weight,
                parameters.ticker_name_org_weight,
                parameters.dense_weight,
                parameters.lexical_weight,
                threshold,
            )
        ],
        lexical_weight_grid=(
            (0.2, 0.3, 0.3, 0.2),
            (0.3, 0.3, 0.2, 0.2),
        ),
        dense_lexical_grid=((0.7, 0.3),),
        threshold_grid=(0.8,),
    )

    assert result.parameters == SimilarityParameters(
        title_weight=0.2,
        full_text_weight=0.3,
        numeric_date_weight=0.3,
        ticker_name_org_weight=0.2,
        dense_weight=0.7,
        lexical_weight=0.3,
    )
    assert result.threshold == 0.8
    assert result.evaluated_candidate_count == 2


def test_fixture_has_required_curated_shape_and_dataset_hash() -> None:
    path = Path('tests/fixtures/article_similarity_pairs.json')
    payload = json.loads(path.read_text(encoding='utf-8'))
    pairs = load_dataset(path)

    assert len(pairs) >= 300
    assert {item.market for item in pairs} == {'KR', 'US'}
    assert {item.label for item in pairs} == {
        'SAME_EVENT',
        'OTHER_EVENT',
        'HARD_NEGATIVE',
    }
    assert all('body' not in record['left'] for record in payload['pairs'])
    assert all('body' not in record['right'] for record in payload['pairs'])
    assert {
        record['hard_negative_type']
        for record in payload['pairs']
        if record['label'] == 'HARD_NEGATIVE'
    } == {'numeric', 'date', 'direction'}
    assert all(item.pair_id for item in pairs)
    assert len({item.pair_id for item in pairs}) == len(pairs)
    canonical_payload = dict(payload)
    canonical_payload.pop('dataset_sha256')
    canonical = json.dumps(
        canonical_payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')
    )
    assert hashlib.sha256(canonical.encode()).hexdigest() == payload['dataset_sha256']


def test_production_defaults_match_mock_holdout_selection() -> None:
    assert SimilarityParameters() == SimilarityParameters(
        title_weight=0.2,
        full_text_weight=0.3,
        numeric_date_weight=0.3,
        ticker_name_org_weight=0.2,
        dense_weight=0.6,
        lexical_weight=0.4,
    )


def test_production_threshold_matches_mock_holdout_selection() -> None:
    from app.batch.steps.group_similar_articles import SIMILARITY_THRESHOLD

    assert SIMILARITY_THRESHOLD == pytest.approx(0.45)


@pytest.mark.anyio
async def test_mock_transport_round_trip_never_uses_real_network() -> None:
    from scripts.calibrate_article_similarity import MockEmbeddingTransport

    transport = MockEmbeddingTransport()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        base_url='http://mock.invalid',
    ) as client:
        response = await client.post(
            '/api/embed',
            json={
                'model': 'mock-bge-m3',
                'input': ['삼성전자 실적', '현대차 생산'],
                'truncate': False,
            },
        )

    assert response.status_code == 200
    assert len(response.json()['embeddings']) == 2
    assert transport.request_count == 1
    assert transport.last_model == 'mock-bge-m3'


@pytest.mark.anyio
async def test_mock_calibration_artifacts_are_consistent_and_non_production(
    tmp_path: Path,
) -> None:
    from scripts.calibrate_article_similarity import run_calibration

    report_path = tmp_path / 'calibration.md'
    result_path = tmp_path / 'calibration.json'
    run = await run_calibration(
        mode='mock', report_path=report_path, result_path=result_path
    )
    result = json.loads(result_path.read_text(encoding='utf-8'))

    assert run.result == result
    assert result['passed'] is True
    assert (
        result['dataset_sha256']
        == json.loads(Path('tests/fixtures/article_similarity_pairs.json').read_text())[
            'dataset_sha256'
        ]
    )
    assert 'httpx.MockTransport' in report_path.read_text(encoding='utf-8')
    assert 'NOT COLLECTED (mock mode)' in report_path.read_text(encoding='utf-8')
    assert 'production `bge-m3` model quality' in report_path.read_text(
        encoding='utf-8'
    )
