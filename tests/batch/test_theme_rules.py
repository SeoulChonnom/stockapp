from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from app.batch.theme_rules import (
    APPROVED_FALLBACK_CODES,
    CANONICAL_LEAF_CODES,
    ThemeRuleValidationError,
    load_theme_rules,
)


def _entry(*, enabled: bool = False) -> dict[str, object]:
    return {
        'inclusionCriteria': ['포함 문구'],
        'exclusionCriteria': ['제외 문구'],
        'fallback': {
            'enabled': enabled,
            'minimumScore': 6,
            'strongPhrases': ['강한 문구'] if enabled else [],
            'supportingTermGroups': [['보조 문구']] if enabled else [],
            'excludedPhrases': ['제외 문구'],
        },
    }


def _document(
    codes: tuple[str, ...] = ('CODE_A',),
    *,
    enabled: bool = False,
) -> dict[str, object]:
    return {code: _entry(enabled=enabled) for code in codes}


def _write_document(tmp_path: Path, document: dict[str, object]) -> Path:
    path = tmp_path / 'theme_rules.yaml'
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding='utf-8')
    return path


def test_default_catalog_loads_all_leaf_rules_as_frozen_objects() -> None:
    rules = load_theme_rules(CANONICAL_LEAF_CODES)

    assert len(rules) == 40
    assert tuple(rules) == CANONICAL_LEAF_CODES
    assert {
        code for code, rule in rules.items() if rule.fallback.enabled
    } == APPROVED_FALLBACK_CODES
    rule = rules['MACRO_ECONOMIC_DATA_INFLATION']
    assert rule.include == rule.inclusion_criteria
    assert rule.strong == rule.fallback.strong_phrases
    assert isinstance(rule.inclusion_criteria, tuple)
    assert isinstance(rule.fallback.supporting_term_groups, tuple)
    with pytest.raises(FrozenInstanceError):
        rule.code = 'OTHER'  # type: ignore[misc]


def test_loader_resolves_default_yaml_relative_to_module(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    rules = load_theme_rules(CANONICAL_LEAF_CODES)

    assert len(rules) == 40


def test_loader_rejects_duplicate_yaml_codes(tmp_path) -> None:
    path = tmp_path / 'theme_rules.yaml'
    path.write_text(
        'CODE_A:\n'
        '  inclusionCriteria: [include]\n'
        '  exclusionCriteria: [exclude]\n'
        '  fallback: {enabled: false, minimumScore: 6, strongPhrases: [], '
        'supportingTermGroups: [], excludedPhrases: []}\n'
        'CODE_A:\n'
        '  inclusionCriteria: [include-2]\n'
        '  exclusionCriteria: [exclude-2]\n'
        '  fallback: {enabled: false, minimumScore: 6, strongPhrases: [], '
        'supportingTermGroups: [], excludedPhrases: []}\n',
        encoding='utf-8',
    )

    with pytest.raises(ThemeRuleValidationError, match='duplicate'):
        load_theme_rules(('CODE_A',), rules_path=path)


@pytest.mark.parametrize(
    ('mutation', 'message'),
    [
        ('missing', 'missing'),
        ('extra', 'unknown'),
    ],
)
def test_loader_requires_exact_catalog_leaf_codes(
    tmp_path,
    mutation: str,
    message: str,
) -> None:
    codes = ('CODE_A', 'CODE_B')
    document = _document(codes)
    catalog_codes = codes
    if mutation == 'missing':
        document.pop('CODE_B')
    else:
        document['CODE_C'] = _entry()

    path = _write_document(tmp_path, document)

    with pytest.raises(ThemeRuleValidationError, match=message):
        load_theme_rules(catalog_codes, rules_path=path)


def test_loader_rejects_blank_terms(tmp_path) -> None:
    document = _document()
    document['CODE_A']['inclusionCriteria'] = ['  ']
    path = _write_document(tmp_path, document)

    with pytest.raises(ThemeRuleValidationError, match='blank'):
        load_theme_rules(('CODE_A',), rules_path=path)


def test_loader_rejects_include_exclude_overlap(tmp_path) -> None:
    document = _document()
    document['CODE_A']['exclusionCriteria'] = ['포함 문구']
    path = _write_document(tmp_path, document)

    with pytest.raises(ThemeRuleValidationError, match='overlap'):
        load_theme_rules(('CODE_A',), rules_path=path)


def test_loader_rejects_non_list_criteria(tmp_path) -> None:
    document = _document()
    document['CODE_A']['inclusionCriteria'] = 'not a list'
    path = _write_document(tmp_path, document)

    with pytest.raises(ThemeRuleValidationError, match='list'):
        load_theme_rules(('CODE_A',), rules_path=path)


def test_loader_rejects_unknown_rule_keys(tmp_path) -> None:
    document = _document()
    document['CODE_A']['legacyInclude'] = ['wrong contract']
    path = _write_document(tmp_path, document)

    with pytest.raises(ThemeRuleValidationError, match='unknown'):
        load_theme_rules(('CODE_A',), rules_path=path)


def test_loader_rejects_enabled_rule_without_strong_evidence(tmp_path) -> None:
    document = _document()
    fallback = document['CODE_A']['fallback']
    assert isinstance(fallback, dict)
    fallback['enabled'] = True
    fallback['strongPhrases'] = []
    path = _write_document(tmp_path, document)

    with pytest.raises(ThemeRuleValidationError, match='strong'):
        load_theme_rules(('CODE_A',), rules_path=path)


def test_loader_rejects_enabled_rule_below_precision_score(tmp_path) -> None:
    document = _document(enabled=True)
    fallback = document['CODE_A']['fallback']
    assert isinstance(fallback, dict)
    fallback['minimumScore'] = 5
    path = _write_document(tmp_path, document)

    with pytest.raises(ThemeRuleValidationError, match='minimumScore'):
        load_theme_rules(('CODE_A',), rules_path=path)


def test_loader_rejects_duplicate_terms(tmp_path) -> None:
    document = _document()
    document['CODE_A']['inclusionCriteria'] = ['같은 문구', '같은 문구']
    path = _write_document(tmp_path, document)

    with pytest.raises(ThemeRuleValidationError, match='duplicate'):
        load_theme_rules(('CODE_A',), rules_path=path)


def test_loader_rejects_general_fallback_codes(tmp_path) -> None:
    path = _write_document(tmp_path, _document(('GENERAL',)))

    with pytest.raises(ThemeRuleValidationError, match='reserved'):
        load_theme_rules(('GENERAL',), rules_path=path)
