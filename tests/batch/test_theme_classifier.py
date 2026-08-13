from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.batch.theme_classifier import (
    ArticleEvidence,
    ThemeAssignment,
    ThemeEvidence,
    classify_theme_fallback,
    normalize_text,
    score_theme_rule,
)
from app.batch.theme_rules import (
    APPROVED_FALLBACK_CODES,
    CANONICAL_LEAF_CODES,
    FallbackRule,
    ThemeRule,
    ThemeRuleCatalog,
    load_theme_rules,
)

FIXTURE_PATH = Path(__file__).parents[1] / 'fixtures' / 'theme_fallback_cases.yaml'


def _evidence(
    *,
    cluster_title: str | None = None,
    representative_article_id: str | int | None = None,
    articles: tuple[ArticleEvidence, ...] = (),
) -> ThemeEvidence:
    return ThemeEvidence(
        cluster_title=cluster_title,
        representative_article_id=representative_article_id,
        articles=articles,
    )


def _article(
    article_id: str | int,
    title: str | None = None,
    summary: str | None = None,
    excerpt: str | None = None,
) -> ArticleEvidence:
    return ArticleEvidence(
        article_id=article_id,
        title=title,
        source_summary=summary,
        article_body_excerpt=excerpt,
    )


def _fixture_evidence(case: dict[str, Any]) -> ThemeEvidence:
    return ThemeEvidence(
        cluster_title=case.get('cluster_title'),
        representative_article_id=case.get('representative_article_id'),
        articles=tuple(
            ArticleEvidence(
                article_id=article['article_id'],
                title=article.get('title'),
                source_summary=article.get('source_summary'),
                article_body_excerpt=article.get('article_body_excerpt'),
            )
            for article in case['articles']
        ),
    )


def _serialize_assignments(assignments: list[ThemeAssignment]) -> bytes:
    return json.dumps(
        [asdict(assignment) for assignment in assignments],
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')


def _single_rule_catalog(
    *,
    code: str = 'SECTOR_AUTOS_MOBILITY_AUTOMAKERS_COMPONENTS',
    strong: tuple[str, ...] = ('강한 자동차 문구',),
    support: tuple[tuple[str, ...], ...] = (('자동차',),),
    excluded: tuple[str, ...] = ('배터리 셀',),
) -> ThemeRuleCatalog:
    rule = ThemeRule(
        code=code,
        inclusion_criteria=(),
        exclusion_criteria=(),
        fallback=FallbackRule(
            enabled=True,
            minimum_score=6,
            strong_phrases=strong,
            supporting_term_groups=support,
            excluded_phrases=excluded,
        ),
    )
    return ThemeRuleCatalog((rule,))


def test_normalize_text_is_nfc_casefolded_and_whitespace_collapsed() -> None:
    assert normalize_text('  Cafe\u0301\t  HBM4\n') == 'café hbm4'


def test_representative_identity_excludes_only_that_article_from_general_score() -> (
    None
):
    catalog = _single_rule_catalog()
    score = score_theme_rule(
        catalog.rules[0],
        _evidence(
            representative_article_id='rep',
            articles=(
                _article('rep', '자동차 판매량'),
                _article('a', '자동차 판매량 다른 표기'),
                _article('b', '자동차 판매량 다른 표기'),
            ),
        ),
    )

    assert score.general_article_match_count == 2
    assert score.distinct_evidence_count == 3
    assert score.score == 10


def test_nullable_article_fields_preserve_identity_and_validate_types() -> None:
    evidence = _evidence(
        articles=(
            _article('a', title=None, summary=''),
            _article('b', title='자동차'),
        )
    )
    assert [article.article_id for article in evidence.articles] == ['a', 'b']
    score = score_theme_rule(_single_rule_catalog().rules[0], evidence)
    assert score.general_article_match_count == 1
    assert score.distinct_evidence_count == 1

    with pytest.raises(TypeError, match='title'):
        _article('bad', title=123)  # type: ignore[arg-type]


def test_score_evidence_keys_use_stable_article_identity() -> None:
    score = score_theme_rule(
        _single_rule_catalog().rules[0],
        _evidence(articles=(_article(42, '자동차'),)),
    )

    assert score.evidence_keys == ('article_title:int:42',)


def test_cluster_title_match_contributes_five_points() -> None:
    catalog = _single_rule_catalog()
    score = score_theme_rule(
        catalog.rules[0],
        _evidence(
            cluster_title='자동차 판매 뉴스',
            articles=(
                _article('a', '자동차 판매 동향'),
                _article('b', '자동차 판매 전망'),
            ),
        ),
    )

    assert score.cluster_title_match is True
    assert score.score == 11


def test_representative_title_match_contributes_four_points() -> None:
    catalog = _single_rule_catalog()
    score = score_theme_rule(
        catalog.rules[0],
        _evidence(
            representative_article_id='rep',
            articles=(
                _article('rep', '자동차 판매 뉴스'),
                _article('a', '자동차 판매 동향'),
                _article('b', '자동차 판매 전망'),
            ),
        ),
    )

    assert score.representative_title_match is True
    assert score.score == 10


def test_general_article_title_points_are_three_each_and_capped_at_six() -> None:
    catalog = _single_rule_catalog()
    score = score_theme_rule(
        catalog.rules[0],
        _evidence(
            articles=(
                _article('a', '자동차 부품 수요'),
                _article('b', '자동차 부품 공급'),
                _article('c', '자동차 부품 수출'),
            ),
        ),
    )

    assert score.general_article_match_count == 3
    assert score.score == 6


def test_summary_and_excerpt_points_are_one_each_and_capped_at_three() -> None:
    catalog = _single_rule_catalog()
    score = score_theme_rule(
        catalog.rules[0],
        _evidence(
            articles=(
                _article('a', summary='자동차 판매 요약'),
                _article('b', summary='자동차 판매 요약2'),
                _article('c', summary='자동차 판매 요약3'),
                _article('d', summary='자동차 판매 요약4'),
            ),
        ),
    )

    assert score.summary_match_count == 4
    assert score.score == 3


def test_strong_phrase_bonus_adds_two_points_and_qualifies_title_evidence() -> None:
    catalog = _single_rule_catalog()
    score = score_theme_rule(
        catalog.rules[0],
        _evidence(
            representative_article_id='rep',
            articles=(_article('rep', '강한 자동차 문구 발표'),),
        ),
    )

    assert score.strong_phrase_match is True
    assert score.strong_phrase_in_title is True
    assert score.score == 6
    assert score.qualified is True


def test_any_excluded_phrase_vetoes_an_otherwise_strong_candidate() -> None:
    catalog = _single_rule_catalog()
    assignments = classify_theme_fallback(
        _evidence(
            representative_article_id='rep',
            articles=(_article('rep', '강한 자동차 문구와 배터리 셀'),),
        ),
        catalog,
    )

    assert assignments == []


def test_low_score_or_single_weak_article_does_not_assign() -> None:
    catalog = _single_rule_catalog()
    assignments = classify_theme_fallback(
        _evidence(articles=(_article('a', '자동차 부품 단신'),)),
        catalog,
    )

    assert assignments == []


def test_llm_analysis_and_tags_are_not_fallback_evidence() -> None:
    catalog = _single_rule_catalog()
    assignments = classify_theme_fallback(_evidence(cluster_title='관련 뉴스'), catalog)

    assert assignments == []


def test_assignments_are_ranked_contiguously_and_limited_to_three() -> None:
    rules = ThemeRuleCatalog(
        tuple(
            ThemeRule(
                code=code,
                inclusion_criteria=(),
                exclusion_criteria=(),
                fallback=FallbackRule(
                    enabled=True,
                    minimum_score=6,
                    strong_phrases=strong,
                    supporting_term_groups=support,
                    excluded_phrases=(),
                ),
            )
            for code, strong, support in (
                ('ALPHA', ('alpha strong',), ()),
                ('BETA', ('beta strong',), ()),
                ('GAMMA', (), (('gamma',),)),
                ('DELTA', (), (('delta',),)),
            )
        )
    )
    evidence = _evidence(
        cluster_title='alpha strong',
        representative_article_id='rep',
        articles=(
            _article('rep', 'beta strong'),
            _article('gamma-a', 'gamma'),
            _article('gamma-b', 'gamma'),
            _article('delta-a', 'delta'),
            _article('delta-b', 'delta'),
        ),
    )

    assignments = classify_theme_fallback(evidence, rules)

    assert [assignment.theme_code for assignment in assignments] == [
        'ALPHA',
        'GAMMA',
        'DELTA',
    ]
    assert [assignment.rank for assignment in assignments] == [1, 2, 3]
    assert all(
        assignment.classification_method == 'KEYWORD_FALLBACK'
        for assignment in assignments
    )


def test_sibling_themes_with_identical_evidence_are_deduplicated() -> None:
    from app.batch.theme_rules import FallbackRule

    rules = ThemeRuleCatalog(
        (
            ThemeRule(
                code='SECTOR_AUTOS_MOBILITY_FIRST',
                inclusion_criteria=(),
                exclusion_criteria=(),
                fallback=FallbackRule(
                    enabled=True,
                    minimum_score=6,
                    strong_phrases=('자동차 산업 강세',),
                    supporting_term_groups=(),
                    excluded_phrases=(),
                ),
            ),
            ThemeRule(
                code='SECTOR_AUTOS_MOBILITY_SECOND',
                inclusion_criteria=(),
                exclusion_criteria=(),
                fallback=FallbackRule(
                    enabled=True,
                    minimum_score=6,
                    strong_phrases=('자동차 산업 강세',),
                    supporting_term_groups=(),
                    excluded_phrases=(),
                ),
            ),
        )
    )

    assignments = classify_theme_fallback(
        _evidence(
            cluster_title='자동차 산업 강세',
            representative_article_id='rep',
            articles=(_article('rep', '자동차 산업 강세'),),
        ),
        rules,
    )

    assert [assignment.theme_code for assignment in assignments] == [
        'SECTOR_AUTOS_MOBILITY_FIRST'
    ]


def test_fixture_has_per_theme_case_gates_and_human_expected_labels() -> None:
    cases = yaml.safe_load(FIXTURE_PATH.read_text(encoding='utf-8'))
    assert isinstance(cases, list)

    by_theme: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        assert isinstance(case, dict)
        code = case['theme_code']
        by_theme[code].append(case)
        assert case['label'] in {'positive', 'boundary', 'negative'}
        assert case['expected_primary_leaf'] in (*APPROVED_FALLBACK_CODES, None)
        if case['expected_primary_leaf'] is not None:
            assert case['expected_primary_leaf'] == code
        if case['label'] == 'positive':
            assert case['expected_primary_leaf'] == code
        if case['label'] == 'negative':
            assert case['expected_primary_leaf'] is None
        accepted_secondary = case.get('accepted_secondary_leaves', [])
        assert isinstance(accepted_secondary, list)
        assert len(accepted_secondary) == len(set(accepted_secondary))
        assert set(accepted_secondary) <= APPROVED_FALLBACK_CODES
        assert case['expected_primary_leaf'] not in accepted_secondary
        if case['label'] == 'negative':
            assert accepted_secondary == []
        article_ids = [article['article_id'] for article in case['articles']]
        assert len(article_ids) == len(set(article_ids))
        if case.get('representative_article_id') is not None:
            assert case['representative_article_id'] in article_ids

    assert set(by_theme) == set(APPROVED_FALLBACK_CODES)
    accepted_secondary_cases = {
        case['id']: tuple(case.get('accepted_secondary_leaves', []))
        for case in cases
        if case.get('accepted_secondary_leaves')
    }
    assert accepted_secondary_cases == {
        '17-positive-10': ('CORPORATE_EVENT_PERFORMANCE_ORDERS_CONTRACTS',),
        '20-positive-10': ('MARKET_FLOW_INVESTOR_INSTITUTIONAL',),
    }
    for code in APPROVED_FALLBACK_CODES:
        labels = Counter(case['label'] for case in by_theme[code])
        assert labels['positive'] >= 10, code
        assert labels['boundary'] >= 5, code
        assert labels['negative'] >= 10, code


def test_fixture_evaluation_meets_each_theme_gate_and_is_repeatable() -> None:
    cases = yaml.safe_load(FIXTURE_PATH.read_text(encoding='utf-8'))
    rules = load_theme_rules(CANONICAL_LEAF_CODES)
    assert isinstance(cases, list)
    metrics: dict[str, Counter[str]] = {
        code: Counter() for code in APPROVED_FALLBACK_CODES
    }
    multi_assignment_cases: list[str] = []
    negative_case_count = sum(case['label'] == 'negative' for case in cases)
    for case in cases:
        accepted = set(case.get('accepted_secondary_leaves', []))
        if case['expected_primary_leaf'] is not None:
            accepted.add(case['expected_primary_leaf'])
        first = classify_theme_fallback(_fixture_evidence(case), rules)
        second = classify_theme_fallback(_fixture_evidence(case), rules)
        assert first == second
        assert _serialize_assignments(first) == _serialize_assignments(second)
        assert [assignment.rank for assignment in first] == list(
            range(1, len(first) + 1)
        )
        if len(first) > 1:
            multi_assignment_cases.append(case['id'])
        if case['expected_primary_leaf'] is not None:
            assert first[0].theme_code == case['expected_primary_leaf']
        if case['id'] == '13-negative-05':
            assert first == []
        for assignment in first:
            assert assignment.theme_code in APPROVED_FALLBACK_CODES
            row = metrics[assignment.theme_code]
            row['predictions'] += 1
            if assignment.theme_code in accepted:
                row['true_positives'] += 1
            else:
                row['false_positives'] += 1
                if case['label'] == 'negative':
                    row['negative_false_positives'] += 1
        for code in accepted:
            metrics[code]['accepted_cases'] += 1

    for code in APPROVED_FALLBACK_CODES:
        row = metrics[code]
        precision = row['true_positives'] / max(row['predictions'], 1)
        recall = row['true_positives'] / max(row['accepted_cases'], 1)
        false_positive_rate = row['negative_false_positives'] / max(
            negative_case_count, 1
        )
        assert precision >= 0.95, (code, dict(row), precision)
        assert recall >= 0.70, (code, dict(row), recall)
        assert false_positive_rate <= 0.05, (
            code,
            dict(row),
            false_positive_rate,
        )

    assert len(multi_assignment_cases) >= 2


def test_assignment_bytes_are_stable_across_hash_seeds() -> None:
    script = """
import json
from dataclasses import asdict
from app.batch.theme_classifier import ArticleEvidence, ThemeEvidence, classify_theme_fallback
from app.batch.theme_rules import CANONICAL_LEAF_CODES, load_theme_rules

rules = load_theme_rules(CANONICAL_LEAF_CODES)
evidence = ThemeEvidence(
    cluster_title='자동차 판매와 전기차 판매',
    articles=(
        ArticleEvidence(1, '자동차 판매량 증가'),
        ArticleEvidence(2, '전기차 판매 확대'),
        ArticleEvidence(3, '배터리 수주 호조'),
    ),
)
print(json.dumps([asdict(item) for item in classify_theme_fallback(evidence, rules)], ensure_ascii=False, sort_keys=True, separators=(',', ':')))
"""
    outputs = []
    for hash_seed in ('1', '987654'):
        environment = os.environ | {'PYTHONHASHSEED': hash_seed}
        result = subprocess.run(
            [sys.executable, '-c', script],
            check=True,
            capture_output=True,
            cwd=Path(__file__).parents[2],
            env=environment,
            text=True,
        )
        outputs.append(result.stdout.encode('utf-8'))

    assert outputs[0] == outputs[1]


def test_assignment_shape_is_stable_for_serialization() -> None:
    assignments = classify_theme_fallback(
        _evidence(
            representative_article_id='rep',
            articles=(_article('rep', '강한 자동차 문구'),),
        ),
        _single_rule_catalog(),
    )

    assert assignments == [
        ThemeAssignment(
            theme_code='SECTOR_AUTOS_MOBILITY_AUTOMAKERS_COMPONENTS',
            rank=1,
            classification_method='KEYWORD_FALLBACK',
        )
    ]
