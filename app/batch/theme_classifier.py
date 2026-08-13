"""Deterministic precision-first fallback for news-cluster themes."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.batch.theme_rules import (
    CANONICAL_PARENT_CODES,
    ThemeRule,
    ThemeRuleCatalog,
    load_theme_rules,
)

ArticleId = int | str
ClassificationMethod = Literal['LLM', 'KEYWORD_FALLBACK']


def normalize_text(value: str | None) -> str:
    """NFC-compose, case-fold, and collapse whitespace in source text."""

    if value is None:
        return ''
    if not isinstance(value, str):
        raise TypeError('value must be a string or None')
    return ' '.join(unicodedata.normalize('NFC', value).casefold().split())


def _validate_optional_text(value: object, field_name: str) -> None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f'{field_name} must be a string or None')


def _validate_article_id(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError(f'{field_name} must be a non-boolean integer or string')
    if isinstance(value, str) and not value.strip():
        raise ValueError(f'{field_name} must not be blank')


@dataclass(frozen=True, slots=True)
class ArticleEvidence:
    """Evidence for one source article, retained under a stable identity."""

    article_id: ArticleId
    title: str | None = None
    source_summary: str | None = None
    article_body_excerpt: str | None = None

    def __post_init__(self) -> None:
        _validate_article_id(self.article_id, 'article_id')
        _validate_optional_text(self.title, 'title')
        _validate_optional_text(self.source_summary, 'source_summary')
        _validate_optional_text(self.article_body_excerpt, 'article_body_excerpt')


@dataclass(frozen=True, slots=True)
class ThemeEvidence:
    """Allowed cluster title and structured article evidence.

    ``representative_article_id`` identifies one item in ``articles``.  The
    same article is scored as the representative title and never again as a
    general article title, even when titles differ or are duplicated elsewhere.
    """

    cluster_title: str | None = None
    representative_article_id: ArticleId | None = None
    articles: tuple[ArticleEvidence, ...] = ()

    def __post_init__(self) -> None:
        _validate_optional_text(self.cluster_title, 'cluster_title')
        if self.representative_article_id is not None:
            _validate_article_id(
                self.representative_article_id, 'representative_article_id'
            )
        if isinstance(self.articles, (str, bytes)) or not isinstance(
            self.articles, Iterable
        ):
            raise TypeError('articles must be an iterable of ArticleEvidence')
        articles = tuple(self.articles)
        if any(not isinstance(article, ArticleEvidence) for article in articles):
            raise TypeError('articles must contain ArticleEvidence values')
        ids = tuple(article.article_id for article in articles)
        if len(ids) != len(set(ids)):
            raise ValueError('article_id values must be unique within evidence')
        if self.representative_article_id is not None and (
            self.representative_article_id not in ids
        ):
            raise ValueError('representative_article_id must identify an article')
        object.__setattr__(self, 'articles', articles)

    @property
    def representative_article(self) -> ArticleEvidence | None:
        """Return the representative article identified by the evidence."""

        if self.representative_article_id is None:
            return None
        return next(
            article
            for article in self.articles
            if article.article_id == self.representative_article_id
        )


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


def _normalise_terms(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        if normalized:
            result.append(normalized)
    return tuple(result)


def _matches(text: str, phrases: Iterable[str]) -> bool:
    return bool(text) and any(phrase in text for phrase in phrases)


def _article_key(article_id: ArticleId) -> str:
    return f'{type(article_id).__name__}:{article_id}'


def score_theme_rule(rule: ThemeRule, evidence: ThemeEvidence) -> ThemeScore:
    """Score one frozen rule using the approved weights and qualification gate."""

    if not isinstance(evidence, ThemeEvidence):
        raise TypeError('evidence must be ThemeEvidence')
    fallback = rule.fallback
    strong = _normalise_terms(fallback.strong_phrases)
    supporting = _normalise_terms(
        term for group in fallback.supporting_term_groups for term in group
    )
    excluded_phrases = _normalise_terms(fallback.excluded_phrases)
    positive = (*strong, *supporting)

    cluster_title = normalize_text(evidence.cluster_title)
    representative = evidence.representative_article
    representative_title = normalize_text(
        representative.title if representative else None
    )
    normalized_articles = tuple(
        (
            article,
            normalize_text(article.title),
            normalize_text(article.source_summary),
            normalize_text(article.article_body_excerpt),
        )
        for article in evidence.articles
    )
    cluster_match = _matches(cluster_title, positive)
    representative_match = _matches(representative_title, positive)
    general_count = 0
    summary_count = 0
    distinct_count = 0
    evidence_keys: list[str] = []
    for article, title, summary, excerpt in normalized_articles:
        title_match = _matches(title, positive)
        summary_match = _matches(summary, positive)
        excerpt_match = _matches(excerpt, positive)
        article_key = _article_key(article.article_id)
        if title_match:
            if article.article_id != evidence.representative_article_id:
                general_count += 1
            evidence_keys.append(f'article_title:{article_key}')
        if summary_match:
            evidence_keys.append(f'article_summary:{article_key}')
        if excerpt_match:
            evidence_keys.append(f'article_excerpt:{article_key}')
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
            for _, title, summary, excerpt in normalized_articles
            for text in (title, summary, excerpt)
        ),
    )
    strong_match = any(_matches(text, strong) for text in all_texts)
    strong_in_title = any(
        _matches(text, strong)
        for text in (cluster_title, *(title for _, title, _, _ in normalized_articles))
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
        fallback.enabled
        and not vetoed
        and score >= fallback.minimum_score
        and (strong_in_title or distinct_count >= 2)
    )
    return ThemeScore(
        score=score,
        distinct_evidence_count=distinct_count,
        cluster_title_match=cluster_match,
        representative_title_match=representative_match,
        general_article_match_count=general_count,
        summary_match_count=summary_count,
        strong_phrase_match=strong_match,
        strong_phrase_in_title=strong_in_title,
        excluded=vetoed,
        qualified=qualified,
        evidence_keys=tuple(evidence_keys),
    )


def _sibling_parent(code: str) -> str | None:
    parents = [
        parent for parent in CANONICAL_PARENT_CODES if code.startswith(f'{parent}_')
    ]
    return max(parents, key=len) if parents else None


def classify_theme_fallback(
    evidence: ThemeEvidence,
    catalog: ThemeRuleCatalog | None = None,
) -> list[ThemeAssignment]:
    """Return at most three ranked, high-confidence fallback assignments."""

    if not isinstance(evidence, ThemeEvidence):
        raise TypeError('evidence must be ThemeEvidence')
    selected_catalog = catalog if catalog is not None else load_theme_rules()
    candidates: list[tuple[int, ThemeRule, ThemeScore]] = []
    for index, rule in enumerate(selected_catalog.rules):
        score = score_theme_rule(rule, evidence)
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
        ThemeAssignment(theme_code=rule.code, rank=rank)
        for rank, (_, rule, _) in enumerate(selected, start=1)
    ]


__all__ = [
    'ArticleEvidence',
    'ArticleId',
    'ClassificationMethod',
    'ThemeAssignment',
    'ThemeEvidence',
    'ThemeScore',
    'classify_theme_fallback',
    'normalize_text',
    'score_theme_rule',
]
