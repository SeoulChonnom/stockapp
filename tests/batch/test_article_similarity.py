from __future__ import annotations

import math

import pytest

from app.batch.article_similarity import (
    SimilarityParameters,
    extract_lexical_features,
    safe_cosine_similarity,
    score_similarity,
)


@pytest.mark.parametrize(
    ('left', 'right', 'expected'),
    [
        ([1.0, 2.0], [1.0, 2.0], 1.0),
        ([1.0, 0.0], [0.0, 1.0], 0.0),
        ([1.0, 0.0], [-1.0, 0.0], -1.0),
        ([0.0, 0.0], [1.0, 0.0], 0.0),
    ],
)
def test_safe_cosine_similarity_handles_boundary_vectors(left, right, expected):
    assert safe_cosine_similarity(left, right) == pytest.approx(expected)


def test_safe_cosine_similarity_handles_finite_float_extremes_without_overflow():
    assert safe_cosine_similarity([1e308, 1e308], [1e308, -1e308]) == pytest.approx(0.0)


def test_safe_cosine_similarity_rejects_dimension_mismatch():
    with pytest.raises(ValueError, match='same dimension'):
        safe_cosine_similarity([1.0], [1.0, 2.0])


@pytest.mark.parametrize('vector', [[math.nan], [math.inf], [-math.inf]])
def test_safe_cosine_similarity_rejects_nonfinite_values(vector):
    with pytest.raises(ValueError, match='finite'):
        safe_cosine_similarity(vector, [1.0] * len(vector))


def test_safe_cosine_similarity_rejects_unrepresentable_real_values():
    with pytest.raises(ValueError, match='finite'):
        safe_cosine_similarity([10**400], [1.0])


def test_similarity_parameters_are_frozen_and_validate_weight_sums():
    parameters = SimilarityParameters()
    with pytest.raises((AttributeError, TypeError)):
        parameters.dense_weight = 0.4  # type: ignore[misc]
    with pytest.raises(ValueError, match='sum to 1'):
        SimilarityParameters(dense_weight=0.8, lexical_weight=0.3)
    with pytest.raises(ValueError, match='non-negative'):
        SimilarityParameters(dense_weight=-0.1, lexical_weight=1.1)


@pytest.mark.parametrize(
    ('field', 'value', 'error'),
    [
        ('title_weight', -0.1, 'non-negative'),
        ('title_weight', float('nan'), 'finite'),
        ('title_weight', float('inf'), 'finite'),
        ('title_weight', True, 'real numbers'),
    ],
)
def test_similarity_parameters_reject_invalid_component_weights(field, value, error):
    with pytest.raises((TypeError, ValueError), match=error):
        SimilarityParameters(**{field: value})


def test_similarity_parameters_reject_zero_lexical_weight_sum():
    with pytest.raises(ValueError, match='sum to 1'):
        SimilarityParameters(
            title_weight=0.0,
            full_text_weight=0.0,
            numeric_date_weight=0.0,
            ticker_name_org_weight=0.0,
        )


@pytest.mark.parametrize(
    ('field', 'value', 'error'),
    [
        ('dense_weight', -0.1, 'non-negative'),
        ('dense_weight', float('nan'), 'finite'),
        ('dense_weight', True, 'real numbers'),
        ('lexical_weight', -0.1, 'non-negative'),
        ('lexical_weight', float('inf'), 'finite'),
        ('lexical_weight', False, 'real numbers'),
    ],
)
def test_similarity_parameters_reject_invalid_blend_weights(field, value, error):
    with pytest.raises((TypeError, ValueError), match=error):
        SimilarityParameters(**{field: value})


def test_similarity_parameters_reject_zero_blend_weight_sum():
    with pytest.raises(ValueError, match='sum to 1'):
        SimilarityParameters(dense_weight=0.0, lexical_weight=0.0)


def test_lexical_features_normalize_nfc_case_and_whitespace_deterministically():
    first = extract_lexical_features('  Café\n실적  개선 ', '  본문   요약 ')
    second = extract_lexical_features('Cafe\u0301 실적 개선', '본문 요약')

    assert first.content_tokens == second.content_tokens
    assert first.content_tokens == frozenset(first.content_tokens)


def test_lexical_features_preserve_structured_numbers_dates_directions_and_tickers():
    features = extract_lexical_features(
        'AAPL 매출 1,000억원, 2026-08-14 상승',
        '삼성전자 영업이익 10 % 증가',
    )

    assert '1000' in features.numeric_values
    assert '10%' in features.numeric_values
    assert '2026-08-14' in features.dates
    assert 'positive' in features.direction_terms
    assert 'AAPL' in features.ticker_tokens
    assert '삼성전자' in features.name_org_tokens


def test_lexical_features_parse_decimal_thousands_and_percentage_as_one_value():
    features = extract_lexical_features(
        '매출 1,234.56억원',
        '성장률 12,345.67% 기록',
    )

    assert features.numeric_values == frozenset({'1234.56', '12345.67%'})
    assert len(features.metric_values) == 2
    assert {item.value for item in features.metric_values} == {
        '1234.56',
        '12345.67%',
    }


def test_lexical_features_ignore_malformed_grouped_numbers_conservatively():
    features = extract_lexical_features('매출 1,23.4억원')

    assert features.numeric_values == frozenset()
    assert features.metric_values == ()


@pytest.mark.parametrize('value', ['2026-02-30', '2026년 2월 30일'])
def test_lexical_features_reject_impossible_dates(value):
    features = extract_lexical_features(value)

    assert value not in features.dates
    assert features.numeric_values == frozenset()


def test_lexical_features_keep_explicit_entities_conservative_and_tickers_separate():
    features = extract_lexical_features(
        'Today Market The REUTERS MARKETS $AAPL BRK.B 삼성전자',
    )

    assert features.ticker_tokens == frozenset({'$AAPL', 'BRK.B'})
    assert features.name_org_tokens == frozenset({'삼성전자'})


def test_lexical_features_keep_explicit_multiword_entities():
    features = extract_lexical_features('Federal Reserve and Acme Corp')

    assert features.name_org_tokens == frozenset({'federal reserve', 'acme corp'})


@pytest.mark.parametrize('word', ['정부', '산업', '금융', '통신', '센터'])
def test_lexical_features_ignore_standalone_korean_suffix_nouns(word):
    features = extract_lexical_features(word)

    assert features.name_org_tokens == frozenset()


def test_lexical_features_require_korean_organization_prefix():
    features = extract_lexical_features('한국정부 삼성전자 산업은행')

    assert features.name_org_tokens == frozenset({'한국정부', '삼성전자', '산업은행'})


def test_long_input_has_bounded_deterministic_features():
    text = ('삼성전자 실적 개선 1,234.56억원 2026-08-14 상승 ' * 2_000).strip()

    first = extract_lexical_features('AAPL', text)
    second = extract_lexical_features('AAPL', text)

    assert first == second
    result = score_similarity('AAPL', text, 'AAPL', text, dense_score=0.42)
    assert 0.0 <= result.combined_score <= 1.0


def test_identical_article_scores_one_without_spurious_empty_set_match():
    result = score_similarity(
        '시장 브리핑',
        '오늘 발표된 내용입니다.',
        '시장 브리핑',
        '오늘 발표된 내용입니다.',
        dense_score=1.0,
    )

    assert result.title_dice == 1.0
    assert result.full_dice == 1.0
    assert result.numeric_date_agreement == 0.0
    assert result.ticker_name_org_agreement == 0.0
    assert result.lexical_score < 1.0
    assert result.combined_score <= 1.0
    assert not result.contradiction_veto


def test_score_clamps_negative_dense_similarity_before_combining():
    result = score_similarity(
        '같은 제목', '같은 내용', '같은 제목', '같은 내용', dense_score=-1.0
    )

    assert result.dense_score == 0.0
    assert 0.0 <= result.combined_score <= 1.0


def test_score_uses_exact_numeric_date_and_entity_agreement():
    result = score_similarity(
        '삼성전자 AAPL 실적 10%',
        '2026년 8월 14일 상승',
        '삼성전자 AAPL 실적 10 %',
        '2026-08-14 상승',
        dense_score=0.0,
    )

    assert result.numeric_date_agreement == 1.0
    assert result.ticker_name_org_agreement == 1.0


@pytest.mark.parametrize(
    ('left', 'right'),
    [
        ('영업이익 100억원 증가', '영업이익 120억원 증가'),
        ('매출 10% 증가', '매출 12% 증가'),
    ],
)
def test_score_vetoes_different_comparable_metric_values(left, right):
    result = score_similarity('실적', left, '실적', right, dense_score=1.0)

    assert result.contradiction_veto
    assert 'numeric' in result.contradiction_reasons
    assert result.combined_score > 0.0


def test_score_does_not_veto_unrelated_bare_numbers():
    result = score_similarity(
        '시장 현황',
        '참석자 수는 10이고 기사 수는 2라고 소개',
        '시장 현황',
        '참석자 수는 12이고 기사 수는 3이라고 소개',
        dense_score=1.0,
    )

    assert not result.contradiction_veto


def test_score_vetoes_explicit_different_event_dates():
    result = score_similarity(
        '정책 발표',
        '2026-08-14 정부 발표',
        '정책 발표',
        '2026년 8월 15일 정부 발표',
        dense_score=1.0,
    )

    assert result.contradiction_veto
    assert 'date' in result.contradiction_reasons


def test_score_vetoes_opposing_english_and_korean_directions():
    result = score_similarity(
        '주가 상승',
        'earnings increase and market gain',
        '주가 하락',
        'earnings decrease and market loss',
        dense_score=1.0,
    )

    assert result.contradiction_veto
    assert 'direction' in result.contradiction_reasons


def test_score_is_deterministic_for_repeated_inputs():
    args = (
        'AAPL 삼성전자 10% 상승',
        '2026-08-14 실적 개선',
        'AAPL 삼성전자 10 % 상승',
        '2026년 8월 14일 실적 개선',
    )

    assert score_similarity(*args, dense_score=0.42) == score_similarity(
        *args, dense_score=0.42
    )
