from __future__ import annotations

import hashlib
import json
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any, Literal, cast

import httpx
import pytest

from app.batch.article_similarity import SimilarityParameters
from app.batch.steps.group_similar_articles import build_grouping_algorithm_version
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
    family: str | None = None,
    left_title: str = '삼성전자 실적 발표',
    right_title: str = '삼성전자 분기 실적 발표',
) -> PairRecord:
    return PairRecord(
        pair_id=pair_id,
        market='KR',
        label=cast(PairLabel, label),
        event_family_id=family or f'{label.lower()}-{pair_id}',
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


def test_family_split_keeps_each_event_family_in_one_partition() -> None:
    pairs = tuple(
        _pair(
            f'{label.lower()}-{family}-{index}',
            label,
            family=f'{label.lower()}-family-{family}',
        )
        for label in ('SAME_EVENT', 'OTHER_EVENT', 'HARD_NEGATIVE')
        for family in range(1, 5)
        for index in range(2)
    )

    split = split_dataset(pairs)
    assignment = {item.event_family_id: 'calibration' for item in split.calibration}
    assignment.update({item.event_family_id: 'holdout' for item in split.holdout})

    assert len(assignment) == 12
    assert all(
        len(
            {
                'calibration' if item in split.calibration else 'holdout'
                for item in pairs
                if item.event_family_id == family
            }
        )
        == 1
        for family in assignment
    )
    assert all(
        len(
            {
                item.event_family_id
                for item in partition
                if item.label == label and item.market == 'KR'
            }
        )
        >= 1
        for partition in (split.calibration, split.holdout)
        for label in ('SAME_EVENT', 'OTHER_EVENT', 'HARD_NEGATIVE')
    )


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


def test_calibration_uses_the_production_algorithm_version_builder() -> None:
    parameters = SimilarityParameters()

    assert build_grouping_algorithm_version(
        model='mock-bge-m3',
        input_chars=2048,
        parameters=parameters,
        threshold=0.45,
    ) == (
        'format=similarity-v2;model=mock-bge-m3;inputChars=2048;'
        'lexical=lexical-v1;'
        'weights=0x1.999999999999ap-3,0x1.3333333333333p-2,'
        '0x1.3333333333333p-2,0x1.999999999999ap-3,'
        '0x1.3333333333333p-1,0x1.999999999999ap-2;'
        'threshold=0x1.ccccccccccccdp-2;veto=veto-v1;grouping=complete-link-v1'
    )


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
async def test_mock_mode_rejects_injected_provider() -> None:
    from scripts.calibrate_article_similarity import run_calibration

    with pytest.raises(ValueError, match='mock mode does not accept a provider'):
        await run_calibration(mode='mock', provider=cast(Any, object()))


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


def test_committed_artifact_recomputes_without_running_the_writer() -> None:
    from scripts.calibrate_article_similarity import (
        _dataset_hash,
        _default_scores,
        _gates,
        _grouping_determinism_rate,
        _implementation_sha256,
        evaluate_metrics,
    )

    dataset_path = Path('tests/fixtures/article_similarity_pairs.json')
    result_path = Path('docs/evaluations/2026-08-13-article-similarity.json')
    dataset = json.loads(dataset_path.read_text(encoding='utf-8'))
    result = json.loads(result_path.read_text(encoding='utf-8'))
    pairs = load_dataset(dataset_path)
    split = split_dataset(pairs)

    assert _dataset_hash(dataset) == result['dataset_sha256']
    assert result['split_assignment_sha256'] == split.assignment_sha256
    assert result['split_assignments'] == list(split.assignments)
    assert all(
        len(
            {
                assignment['split']
                for assignment in result['split_assignments']
                if assignment['event_family_id'] == family
            }
        )
        == 1
        for family in {item.event_family_id for item in pairs}
    )
    assert result['calibration_pair_count'] == len(split.calibration)
    assert result['holdout_pair_count'] == len(split.holdout)
    assert result['algorithm_version'] == build_grouping_algorithm_version(
        model='mock-bge-m3',
        input_chars=2048,
        parameters=SimilarityParameters(**result['selected_parameters']),
        threshold=result['selected_threshold'],
    )
    vectors = {
        article.article_id: mock_embedding(f'{article.title} {article.summary}')
        for pair in pairs
        for article in (pair.left, pair.right)
    }
    parameters = SimilarityParameters(**result['selected_parameters'])
    scores, vetoes = _default_scores(split.holdout, vectors, parameters)
    holdout = evaluate_metrics(
        split.holdout,
        scores=scores,
        threshold=result['selected_threshold'],
        vetoes=vetoes,
    )
    holdout = dataclass_replace(
        holdout,
        determinism_rate=_grouping_determinism_rate(
            parameters=parameters,
            threshold=result['selected_threshold'],
            runs=result['determinism_runs'],
        ),
    )
    assert result['holdout_metrics'] == holdout.to_dict()
    assert result['gates'] == _gates(
        holdout, result['runtime_p95_seconds'], mode='mock'
    )
    assert result['embedding_algorithm_sha256'] == _implementation_sha256()
    assert result['mode'] == 'mock'
    assert result['passed'] is True
    assert result['gates'] == {
        'precision_ge_95_percent': True,
        'same_event_recall_ge_85_percent': True,
        'other_event_false_merge_le_3_percent': True,
        'hard_negative_false_merge_zero': True,
        'determinism_100_percent': True,
        'mock_pipeline_p95_le_30_seconds': True,
    }


def test_determinism_helper_requires_multi_article_cases() -> None:
    from app.batch.article_similarity import group_similar_articles
    from scripts.calibrate_article_similarity import _determinism_cases

    cases = _determinism_cases()

    assert cases
    assert min(len(case) for case in cases) >= 3
    assert len(cases) >= 2
    chain_result = group_similar_articles(
        cases[0], threshold=0.45, parameters=SimilarityParameters()
    )
    hard_result = group_similar_articles(
        cases[1], threshold=0.45, parameters=SimilarityParameters()
    )
    assert [
        [member.processed_article_id for member in group.members]
        for group in chain_result.groups
    ] == [[1001, 1002], [1003]]
    assert all(
        not ({1001, 1004} <= {member.processed_article_id for member in group.members})
        for group in hard_result.groups
    )
