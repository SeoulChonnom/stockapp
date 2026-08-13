"""Deterministic precision-first fallback for news-cluster themes."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.batch.theme_rules import (
    CANONICAL_PARENT_CODES,
    ThemeRule,
    ThemeRuleCatalog,
    load_theme_rules,
)

ClassificationMethod = Literal['KEYWORD_FALLBACK']
CatalogInput = ThemeRuleCatalog | Mapping[str, ThemeRule] | Iterable[ThemeRule]


def normalize_text(value: str | None) -> str:
    """NFC-compose, case-fold, and collapse whitespace in source text."""

    if value is None:
        return ''
    if not isinstance(value, str):
        raise TypeError('value must be a string or None')
    return ' '.join(unicodedata.normalize('NFC', value).casefold().split())


# One pure implementation is shared by matching and future snapshot/search
# document builders.  The aliases keep that contract discoverable to callers.
normalize_for_matching = normalize_text
normalize_search_text = normalize_text
normalize_evidence = normalize_text


def _text_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _first(mapping: Mapping[str, Any], keys: Sequence[str]) -> object:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


@dataclass(frozen=True, slots=True)
class ThemeEvidence:
    """Allowed cluster/article evidence consumed by the fallback.

    Article titles, summaries, and excerpts align by index.  If the
    representative title is present in ``article_titles``, it is counted as
    the representative title rather than as a general article title.
    """

    cluster_title: str | None = None
    representative_title: str | None = None
    article_titles: tuple[str, ...] = ()
    source_summaries: tuple[str, ...] = ()
    source_excerpts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ('article_titles', 'source_summaries', 'source_excerpts'):
            object.__setattr__(self, field_name, _text_tuple(getattr(self, field_name)))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ThemeEvidence:
        """Build evidence from supported snake_case or camelCase fields.

        LLM analysis, tags, and other metadata are intentionally not read.
        """

        return cls(
            cluster_title=_optional_text(
                _first(value, ('cluster_title', 'clusterTitle', 'title'))
            ),
            representative_title=_optional_text(
                _first(
                    value,
                    (
                        'representative_title',
                        'representativeTitle',
                        'representative_article_title',
                        'representativeArticleTitle',
                    ),
                )
            ),
            article_titles=_text_tuple(
                _first(value, ('article_titles', 'articleTitles', 'all_article_titles'))
            ),
            source_summaries=_text_tuple(
                _first(
                    value,
                    (
                        'source_summaries',
                        'sourceSummaries',
                        'article_summaries',
                        'articleSummaries',
                        'source_summary',
                        'sourceSummary',
                        'summary',
                    ),
                )
            ),
            source_excerpts=_text_tuple(
                _first(
                    value,
                    (
                        'source_excerpts',
                        'sourceExcerpts',
                        'article_excerpts',
                        'articleExcerpts',
                        'article_body_excerpts',
                        'articleBodyExcerpts',
                        'article_body_excerpt',
                        'articleBodyExcerpt',
                        'source_excerpt',
                        'sourceExcerpt',
                        'excerpt',
                    ),
                )
            ),
        )

    @property
    def summaries(self) -> tuple[str, ...]:
        """Return source summaries through a short read-only alias."""

        return self.source_summaries

    @property
    def excerpts(self) -> tuple[str, ...]:
        """Return source excerpts through a short read-only alias."""

        return self.source_excerpts


@dataclass(frozen=True, slots=True)
class ThemeScore:
    """Breakdown of one rule score and its qualification gate."""

    score: int
    distinct_evidence_count: int
    cluster_title_match: bool
    representative_title_match: bool
    general_article_match_count: int
    summary_match_count: int
    strong_phrase_match: bool
    strong_phrase_in_title: bool
    excluded: bool
    qualified: bool
    evidence_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ThemeAssignment:
    """Ranked assignment ready for persistence."""

    theme_code: str
    rank: int
    classification_method: ClassificationMethod = 'KEYWORD_FALLBACK'


@dataclass(frozen=True, slots=True)
class _ArticleEvidence:
    title: str
    summary: str
    excerpt: str


def _coerce_evidence(
    evidence: ThemeEvidence | Mapping[str, Any] | str | None,
) -> ThemeEvidence:
    if isinstance(evidence, ThemeEvidence):
        return evidence
    if isinstance(evidence, Mapping):
        return ThemeEvidence.from_mapping(evidence)
    if isinstance(evidence, str):
        return ThemeEvidence(cluster_title=evidence)
    if evidence is None:
        return ThemeEvidence()
    raise TypeError('evidence must be ThemeEvidence, a mapping, a string, or None')


def _article_records(evidence: ThemeEvidence) -> tuple[_ArticleEvidence, ...]:
    count = max(
        len(evidence.article_titles),
        len(evidence.source_summaries),
        len(evidence.source_excerpts),
    )
    return tuple(
        _ArticleEvidence(
            evidence.article_titles[index]
            if index < len(evidence.article_titles)
            else '',
            evidence.source_summaries[index]
            if index < len(evidence.source_summaries)
            else '',
            evidence.source_excerpts[index]
            if index < len(evidence.source_excerpts)
            else '',
        )
        for index in range(count)
    )


def _rule_values(
    rule: ThemeRule,
) -> tuple[bool, int, tuple[str, ...], tuple[tuple[str, ...], ...], tuple[str, ...]]:
    fallback = rule.fallback
    return (
        fallback.enabled,
        fallback.minimum_score,
        fallback.strong_phrases,
        fallback.supporting_term_groups,
        fallback.excluded_phrases,
    )


def _normalise_terms(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        if normalized:
            result.append(normalized)
    return tuple(result)


def _matches(text: str, phrases: Iterable[str]) -> bool:
    return bool(text) and any(phrase in text for phrase in phrases)


def score_theme_rule(
    rule: ThemeRule,
    evidence: ThemeEvidence | Mapping[str, Any] | str | None,
) -> ThemeScore:
    """Score one rule using the approved field weights and qualification gate."""

    evidence = _coerce_evidence(evidence)
    enabled, minimum, raw_strong, raw_groups, raw_excluded = _rule_values(rule)
    strong = _normalise_terms(raw_strong)
    supporting = _normalise_terms(term for group in raw_groups for term in group)
    excluded_phrases = _normalise_terms(raw_excluded)
    positive = (*strong, *supporting)

    cluster_title = normalize_text(evidence.cluster_title)
    representative_title = normalize_text(evidence.representative_title)
    records = tuple(
        _ArticleEvidence(
            normalize_text(article.title),
            normalize_text(article.summary),
            normalize_text(article.excerpt),
        )
        for article in _article_records(evidence)
    )
    cluster_match = _matches(cluster_title, positive)
    representative_match = _matches(representative_title, positive)
    general_count = 0
    summary_count = 0
    distinct_count = 0
    representative_consumed = False
    evidence_keys: list[str] = []
    for index, article in enumerate(records):
        title_match = _matches(article.title, positive)
        summary_match = _matches(article.summary, positive)
        excerpt_match = _matches(article.excerpt, positive)
        if title_match:
            if (
                not representative_consumed
                and representative_title
                and article.title == representative_title
            ):
                representative_consumed = True
            else:
                general_count += 1
            evidence_keys.append(f'article_title:{index}')
        if summary_match:
            evidence_keys.append(f'article_summary:{index}')
        if excerpt_match:
            evidence_keys.append(f'article_excerpt:{index}')
        if summary_match or excerpt_match:
            summary_count += 1
        if title_match or summary_match or excerpt_match:
            distinct_count += 1

    if cluster_match:
        evidence_keys.insert(0, 'cluster_title')
    if representative_match:
        evidence_keys.insert(1 if cluster_match else 0, 'representative_title')

    all_texts = (
        cluster_title,
        representative_title,
        *(
            text
            for article in records
            for text in (article.title, article.summary, article.excerpt)
        ),
    )
    strong_match = any(_matches(text, strong) for text in all_texts)
    strong_in_title = any(
        _matches(text, strong)
        for text in (cluster_title, representative_title, *(a.title for a in records))
    )
    vetoed = any(_matches(text, excluded_phrases) for text in all_texts)
    score = (
        (5 if cluster_match else 0)
        + (4 if representative_match else 0)
        + min(general_count * 3, 6)
        + min(summary_count, 3)
        + (2 if strong_match else 0)
    )
    qualified = (
        enabled
        and not vetoed
        and score >= minimum
        and (strong_in_title or distinct_count >= 2)
    )
    return ThemeScore(
        score,
        distinct_count,
        cluster_match,
        representative_match,
        general_count,
        summary_count,
        strong_match,
        strong_in_title,
        vetoed,
        qualified,
        tuple(evidence_keys),
    )


def _catalog_rules(catalog: CatalogInput) -> tuple[ThemeRule, ...]:
    if isinstance(catalog, ThemeRuleCatalog):
        return catalog.rules
    if isinstance(catalog, Mapping):
        return tuple(catalog.values())
    return tuple(catalog)


def _sibling_parent(code: str) -> str | None:
    parents = [
        parent for parent in CANONICAL_PARENT_CODES if code.startswith(f'{parent}_')
    ]
    return (
        max(parents, key=len)
        if parents
        else (code.rsplit('_', 1)[0] if '_' in code else None)
    )


def classify_theme_fallback(
    evidence: ThemeEvidence | Mapping[str, Any] | str | None = None,
    catalog: CatalogInput | None = None,
    *,
    cluster_title: str | None = None,
    representative_title: str | None = None,
    article_titles: Iterable[str] | str | None = None,
    source_summaries: Iterable[str] | str | None = None,
    source_excerpts: Iterable[str] | str | None = None,
    article_summaries: Iterable[str] | str | None = None,
    article_excerpts: Iterable[str] | str | None = None,
    source_summary: str | None = None,
    source_excerpt: str | None = None,
    summary: str | None = None,
    excerpt: str | None = None,
    rules: CatalogInput | None = None,
) -> list[ThemeAssignment]:
    """Return at most three ranked, high-confidence fallback assignments."""

    if catalog is not None and rules is not None:
        raise TypeError('provide catalog or rules, not both')
    if source_summaries is None:
        source_summaries = next(
            (
                value
                for value in (
                    article_summaries,
                    source_summary,
                    summary,
                )
                if value is not None
            ),
            None,
        )
    if source_excerpts is None:
        source_excerpts = next(
            (
                value
                for value in (
                    article_excerpts,
                    source_excerpt,
                    excerpt,
                )
                if value is not None
            ),
            None,
        )
    if any(
        value is not None
        for value in (
            cluster_title,
            representative_title,
            article_titles,
            source_summaries,
            source_excerpts,
        )
    ):
        base = _coerce_evidence(evidence)
        evidence = ThemeEvidence(
            cluster_title if cluster_title is not None else base.cluster_title,
            representative_title
            if representative_title is not None
            else base.representative_title,
            _text_tuple(article_titles)
            if article_titles is not None
            else base.article_titles,
            _text_tuple(source_summaries)
            if source_summaries is not None
            else base.source_summaries,
            _text_tuple(source_excerpts)
            if source_excerpts is not None
            else base.source_excerpts,
        )

    selected_catalog = (
        rules
        if rules is not None
        else catalog
        if catalog is not None
        else load_theme_rules()
    )
    normalized_evidence = _coerce_evidence(evidence)
    candidates: list[tuple[int, ThemeRule, ThemeScore]] = []
    for index, rule in enumerate(_catalog_rules(selected_catalog)):
        score = score_theme_rule(rule, normalized_evidence)
        if score.qualified:
            candidates.append((index, rule, score))
    candidates.sort(
        key=lambda item: (
            -item[2].score,
            -item[2].distinct_evidence_count,
            item[0],
            item[1].code,
        )
    )

    selected: list[tuple[int, ThemeRule, ThemeScore]] = []
    for candidate in candidates:
        _, rule, score = candidate
        parent = _sibling_parent(rule.code)
        if parent and any(
            parent == _sibling_parent(existing.code)
            and score.evidence_keys == existing_score.evidence_keys
            for _, existing, existing_score in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) == 3:
            break
    return [
        ThemeAssignment(rule.code, rank)
        for rank, (_, rule, _) in enumerate(selected, start=1)
    ]


def classify_themes(
    evidence: ThemeEvidence | Mapping[str, Any] | str | None = None,
    *,
    catalog: CatalogInput | None = None,
    **fields: Any,
) -> list[ThemeAssignment]:
    """Plural-name wrapper for batch callers."""

    return classify_theme_fallback(evidence, catalog, **fields)


classify = classify_theme_fallback
classify_cluster = classify_theme_fallback
classify_fallback = classify_theme_fallback
score_rule = score_theme_rule
score_fallback = score_theme_rule

__all__ = [
    'ClassificationMethod',
    'ThemeAssignment',
    'ThemeEvidence',
    'ThemeScore',
    'classify',
    'classify_cluster',
    'classify_fallback',
    'classify_theme_fallback',
    'classify_themes',
    'normalize_evidence',
    'normalize_for_matching',
    'normalize_search_text',
    'normalize_text',
    'score_fallback',
    'score_rule',
    'score_theme_rule',
]
