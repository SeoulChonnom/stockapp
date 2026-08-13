from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

from app.batch.theme_classifier import (
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
    representative_title: str | None = None,
    article_titles: tuple[str, ...] = (),
    source_summaries: tuple[str, ...] = (),
    source_excerpts: tuple[str, ...] = (),
) -> ThemeEvidence:
    return ThemeEvidence(
        cluster_title=cluster_title,
        representative_title=representative_title,
        article_titles=article_titles,
        source_summaries=source_summaries,
        source_excerpts=source_excerpts,
    )


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


def test_cluster_title_match_contributes_five_points() -> None:
    catalog = _single_rule_catalog()
    score = score_theme_rule(
        catalog.rules[0],
        _evidence(
            cluster_title='자동차 판매 뉴스',
            article_titles=('자동차 판매 동향', '자동차 판매 전망'),
        ),
    )

    assert score.cluster_title_match is True
    assert score.score == 11


def test_representative_title_match_contributes_four_points() -> None:
    catalog = _single_rule_catalog()
    score = score_theme_rule(
        catalog.rules[0],
        _evidence(
            representative_title='자동차 판매 뉴스',
            article_titles=('자동차 판매 동향', '자동차 판매 전망'),
        ),
    )

    assert score.representative_title_match is True
    assert score.score == 10


def test_general_article_title_points_are_three_each_and_capped_at_six() -> None:
    catalog = _single_rule_catalog()
    score = score_theme_rule(
        catalog.rules[0],
        _evidence(
            article_titles=('자동차 부품 수요', '자동차 부품 공급', '자동차 부품 수출'),
        ),
    )

    assert score.general_article_match_count == 3
    assert score.score == 6


def test_summary_and_excerpt_points_are_one_each_and_capped_at_three() -> None:
    catalog = _single_rule_catalog()
    score = score_theme_rule(
        catalog.rules[0],
        _evidence(
            source_summaries=(
                '자동차 판매 요약',
                '자동차 판매 요약2',
                '자동차 판매 요약3',
                '자동차 판매 요약4',
            ),
        ),
    )

    assert score.summary_match_count == 4
    assert score.score == 3


def test_strong_phrase_bonus_adds_two_points_and_qualifies_title_evidence() -> None:
    catalog = _single_rule_catalog()
    score = score_theme_rule(
        catalog.rules[0],
        _evidence(representative_title='강한 자동차 문구 발표'),
    )

    assert score.strong_phrase_match is True
    assert score.strong_phrase_in_title is True
    assert score.score == 6
    assert score.qualified is True


def test_any_excluded_phrase_vetoes_an_otherwise_strong_candidate() -> None:
    catalog = _single_rule_catalog()
    assignments = classify_theme_fallback(
        _evidence(representative_title='강한 자동차 문구와 배터리 셀'),
        catalog,
    )

    assert assignments == []


def test_low_score_or_single_weak_article_does_not_assign() -> None:
    catalog = _single_rule_catalog()
    assignments = classify_theme_fallback(
        _evidence(article_titles=('자동차 부품 단신',)),
        catalog,
    )

    assert assignments == []


def test_llm_analysis_and_tags_are_not_fallback_evidence() -> None:
    catalog = _single_rule_catalog()
    assignments = classify_theme_fallback(
        {
            'cluster_title': '관련 뉴스',
            'analysis': '강한 자동차 문구',
            'tags': ['자동차'],
        },
        catalog,
    )

    assert assignments == []


def test_assignments_are_ranked_contiguously_and_limited_to_three() -> None:
    rules = load_theme_rules(CANONICAL_LEAF_CODES)
    evidence = _evidence(
        cluster_title='자동차 판매와 전기차 판매 및 배터리 수주',
        representative_title='자동차 판매와 전기차 배터리 수주',
        article_titles=(
            '자동차 판매량 증가',
            '전기차 판매 확대',
            '배터리 수주 호조',
            '완성차 업체 실적',
        ),
    )

    assignments = classify_theme_fallback(evidence, rules)

    assert len(assignments) <= 3
    assert [assignment.rank for assignment in assignments] == list(
        range(1, len(assignments) + 1)
    )
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
        _evidence(cluster_title='자동차 산업 강세'),
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
        assert case['expected_primary_leaf'] in (*APPROVED_FALLBACK_CODES, None)

    assert set(by_theme) == set(APPROVED_FALLBACK_CODES)
    for code in APPROVED_FALLBACK_CODES:
        labels = Counter(case['label'] for case in by_theme[code])
        assert labels['positive'] >= 10, code
        assert labels['boundary'] >= 5, code
        assert labels['negative'] >= 10, code


def test_fixture_evaluation_meets_each_theme_gate_and_is_repeatable() -> None:
    cases = yaml.safe_load(FIXTURE_PATH.read_text(encoding='utf-8'))
    rules = load_theme_rules(CANONICAL_LEAF_CODES)
    assert isinstance(cases, list)
    by_theme: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        by_theme[case['theme_code']].append(case)

    for code in APPROVED_FALLBACK_CODES:
        expected_cases = [
            case for case in by_theme[code] if case['expected_primary_leaf'] is not None
        ]
        negative_cases = [
            case for case in by_theme[code] if case['expected_primary_leaf'] is None
        ]
        true_positives = 0
        false_positives = 0
        for case in by_theme[code]:
            first = classify_theme_fallback(case, rules)
            second = classify_theme_fallback(case, rules)
            assert first == second
            assert repr(first).encode('utf-8') == repr(second).encode('utf-8')
            if case['expected_primary_leaf'] == code:
                true_positives += bool(first and first[0].theme_code == code)
            elif first and first[0].theme_code == code:
                false_positives += 1

        precision = true_positives / max(true_positives + false_positives, 1)
        recall = true_positives / max(len(expected_cases), 1)
        false_positive_rate = false_positives / max(len(negative_cases), 1)
        assert precision >= 0.95, code
        assert recall >= 0.70, code
        assert false_positive_rate <= 0.05, code


def test_assignment_shape_is_stable_for_serialization() -> None:
    assignments = classify_theme_fallback(
        _evidence(representative_title='강한 자동차 문구'),
        _single_rule_catalog(),
    )

    assert assignments == [
        ThemeAssignment(
            theme_code='SECTOR_AUTOS_MOBILITY_AUTOMAKERS_COMPONENTS',
            rank=1,
            classification_method='KEYWORD_FALLBACK',
        )
    ]
