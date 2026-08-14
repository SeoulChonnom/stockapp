"""Deterministic article pair scoring used by the similarity grouping step.

This module deliberately has no model, network, or database dependency.  Dense
vectors are supplied by the caller; all lexical and contradiction decisions are
made locally so repeated batch runs produce the same result.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from numbers import Real
from typing import Any

from app.core.text import normalize_text

_TOKEN_RE = re.compile(r'[0-9A-Za-z가-힣]+')
_NUMBER_RE = re.compile(
    r'(?<![0-9.,])[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)'
    r'(?:\s*%|\s*퍼센트)?(?![0-9.,])'
)
_ISO_DATE_RE = re.compile(
    r'(?<!\d)(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})(?!\d)'
)
_KOREAN_DATE_RE = re.compile(r'(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?')
_TICKER_RE = re.compile(
    r'(?<![A-Za-z0-9])(?:\$[A-Z]{1,6}|[A-Z]{1,6}(?:[.-][A-Z0-9]{1,6})?'
    r'|\d{6}(?:\.[A-Z]{1,3})?)(?![A-Za-z0-9])'
)
_UNIT_RE = re.compile(
    r'^\s*(?P<unit>%|퍼센트|조\s*원|억\s*원|만\s*원|원|달러|USD|EUR|'
    r'백만\s*달러|million\s+(?:dollars?|USD)|billion\s+(?:dollars?|USD)|'
    r'배|명|건|개|곳|회|도)',
    re.IGNORECASE,
)

_POSITIVE_DIRECTION_RE = re.compile(
    r'(?:정상|상승|오름|증가|늘어|개선|강세|반등|상향|급등|폭등|회복|'
    r'(?<![a-z])(?:rise|rises|rose|rising|gain|gains|gained|increase|'
    r'increases|increased|increasing|up|upward|surge|surged|higher|'
    r'improve|improved|improvement)(?![a-z]))',
)
_NEGATIVE_DIRECTION_RE = re.compile(
    r'(?:하락|내림|감소|줄어|악화|약세|하향|급락|폭락|'
    r'(?<![a-z])(?:fall|falls|fell|falling|loss|losses|lose|decrease|'
    r'decreases|decreased|decreasing|down|downward|drop|dropped|decline|'
    r'declined|lower|worsen|worsening)(?![a-z]))',
)
_ORGANIZATION_SUFFIX_RE = re.compile(
    r'(?:전자|증권|은행|그룹|기업|회사|공사|정부|위원회|대학교|대학|병원|'
    r'재단|협회|공단|연구원|산업|금융|통신|센터)$'
)
_EN_ORGANIZATION_SUFFIX_RE = re.compile(
    r'(?:inc|corp|corporation|ltd|llc|plc|company|bank|group|holdings|'
    r'university)$',
    re.IGNORECASE,
)
_PROPER_TOKEN_RE = re.compile(r'[A-Z][a-zA-Z]{2,}')
_ENTITY_STOP_WORDS = frozenset(
    {
        'a',
        'an',
        'and',
        'daily',
        'latest',
        'market',
        'markets',
        'news',
        'report',
        'the',
        'today',
    }
)
_METRIC_SUFFIXES = (
    '으로',
    '에서',
    '까지',
    '부터',
    '에는',
    '은',
    '는',
    '이',
    '가',
    '을',
    '를',
    '의',
    '에',
    '도',
    '와',
    '과',
)


@dataclass(frozen=True, slots=True)
class StructuredNumericValue:
    """One number that has a locally comparable metric and unit."""

    metric: str
    unit: str
    value: str


@dataclass(frozen=True, slots=True)
class LexicalFeatures:
    """Immutable lexical features extracted from one article input."""

    title_tokens: frozenset[str]
    content_tokens: frozenset[str]
    numeric_values: frozenset[str]
    dates: frozenset[str]
    direction_terms: frozenset[str]
    ticker_tokens: frozenset[str]
    name_org_tokens: frozenset[str]
    metric_values: tuple[StructuredNumericValue, ...] = ()

    @property
    def full_tokens(self) -> frozenset[str]:
        """Return tokens from the complete title-plus-content input."""

        return self.content_tokens

    @property
    def numeric_date_tokens(self) -> frozenset[str]:
        """Return normalized numeric and date tokens for agreement scoring."""

        return self.numeric_values | self.dates

    @property
    def organization_name_tokens(self) -> frozenset[str]:
        """Compatibility alias for organization/name features."""

        return self.name_org_tokens


@dataclass(frozen=True, slots=True)
class SimilarityParameters:
    """Weights for lexical components and dense/lexical blending.

    Lexical component weights and dense/lexical weights are each normalized
    independently.  Keeping these values in a frozen value object makes the
    algorithm version explicit at every call site.
    """

    title_weight: float = 0.20
    full_text_weight: float = 0.30
    numeric_date_weight: float = 0.30
    ticker_name_org_weight: float = 0.20
    dense_weight: float = 0.60
    lexical_weight: float = 0.40

    def __post_init__(self) -> None:
        lexical = (
            self.title_weight,
            self.full_text_weight,
            self.numeric_date_weight,
            self.ticker_name_org_weight,
        )
        dense_lexical = (self.dense_weight, self.lexical_weight)
        for value in (*lexical, *dense_lexical):
            if isinstance(value, bool) or not isinstance(value, Real):
                raise TypeError('weights must be real numbers')
            if not math.isfinite(float(value)):
                raise ValueError('weights must be finite')
            if value < 0:
                raise ValueError('weights must be non-negative')
        if not math.isclose(sum(lexical), 1.0, abs_tol=1e-9):
            raise ValueError('lexical component weights must sum to 1')
        if not math.isclose(sum(dense_lexical), 1.0, abs_tol=1e-9):
            raise ValueError('dense and lexical weights must sum to 1')

    @property
    def full_weight(self) -> float:
        """Compatibility alias for the full-input token component."""

        return self.full_text_weight

    @property
    def entity_weight(self) -> float:
        """Compatibility alias for ticker/name/organization agreement."""

        return self.ticker_name_org_weight


@dataclass(frozen=True, slots=True)
class SimilarityResult:
    """Component and final score for an article pair."""

    dense_score: float
    title_dice: float
    full_dice: float
    numeric_date_agreement: float
    ticker_name_org_agreement: float
    lexical_score: float
    combined_score: float
    contradiction_veto: bool
    contradiction_reasons: tuple[str, ...]

    @property
    def vetoed(self) -> bool:
        """Compatibility alias for the contradiction veto flag."""

        return self.contradiction_veto

    @property
    def score(self) -> float:
        """Return the bounded combined score."""

        return self.combined_score

    @property
    def numeric_date_score(self) -> float:
        """Compatibility alias for numeric/date agreement."""

        return self.numeric_date_agreement

    @property
    def entity_score(self) -> float:
        """Compatibility alias for ticker/name/organization agreement."""

        return self.ticker_name_org_agreement


@dataclass(frozen=True, slots=True)
class ArticleCandidate:
    """Immutable article input for deterministic similarity grouping.

    ``published_at`` must be timezone-aware when supplied.  The grouping
    function converts it to UTC before sorting, so equivalent instants with
    different offsets have identical ordering.  A missing timestamp is valid
    article data, but does not count toward information completeness.
    """

    processed_article_id: int
    canonical_title: str | None = None
    source_summary: str | None = None
    article_body_excerpt: str | None = None
    publisher_name: str | None = None
    origin_link: str | None = None
    published_at: datetime | None = None
    vector: tuple[Real, ...] | None = None
    exact_duplicate_count: int = 0
    is_cluster_representative: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.processed_article_id, bool) or not isinstance(
            self.processed_article_id, int
        ):
            raise TypeError('processed_article_id must be an integer')
        for field_name in (
            'canonical_title',
            'source_summary',
            'article_body_excerpt',
            'publisher_name',
            'origin_link',
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f'{field_name} must be a string or None')
        if self.published_at is not None and not isinstance(
            self.published_at, datetime
        ):
            raise TypeError('published_at must be a datetime or None')
        if isinstance(self.exact_duplicate_count, bool) or not isinstance(
            self.exact_duplicate_count, int
        ):
            raise TypeError('exact_duplicate_count must be an integer')
        if self.exact_duplicate_count < 0:
            raise ValueError('exact_duplicate_count must be non-negative')
        if not isinstance(self.is_cluster_representative, bool):
            raise TypeError('is_cluster_representative must be a boolean')
        if self.vector is not None:
            if isinstance(self.vector, (str, bytes)):
                raise TypeError('vector must be a sequence of real numbers')
            try:
                vector = tuple(self.vector)
            except TypeError:
                raise TypeError('vector must be a sequence of real numbers') from None
            object.__setattr__(self, 'vector', vector)

    @property
    def title(self) -> str | None:
        """Compatibility alias for the canonical title field."""

        return self.canonical_title

    @property
    def embedding(self) -> tuple[Real, ...] | None:
        """Compatibility alias for callers naming vectors embeddings."""

        return self.vector


SimilarityArticle = ArticleCandidate


@dataclass(frozen=True, slots=True)
class SimilarityGroupMember:
    """One ranked member of a persisted-ready similarity group."""

    processed_article_id: int
    similarity_score: float
    is_representative: bool
    article_rank: int

    @property
    def score(self) -> float:
        """Return this member's average in-group similarity."""

        return self.similarity_score


@dataclass(frozen=True, slots=True)
class SimilarityGroup:
    """A complete-link group with one deterministic representative."""

    group_rank: int
    representative_article_id: int
    members: tuple[SimilarityGroupMember, ...]

    @property
    def rank(self) -> int:
        """Compatibility alias for the contiguous group rank."""

        return self.group_rank

    @property
    def representative_id(self) -> int:
        """Compatibility alias for the representative article ID."""

        return self.representative_article_id


@dataclass(frozen=True, slots=True)
class SimilarityGroupingResult:
    """Immutable output of one cluster's complete-link grouping run."""

    groups: tuple[SimilarityGroup, ...]

    @property
    def article_count(self) -> int:
        """Return the number of grouped articles."""

        return sum(len(group.members) for group in self.groups)

    def to_dict(self) -> dict[str, object]:
        """Serialize grouping metadata without exposing input vectors."""

        return {
            'groups': [
                {
                    'groupRank': group.group_rank,
                    'representativeArticleId': group.representative_article_id,
                    'members': [
                        {
                            'processedArticleId': member.processed_article_id,
                            'similarityScore': member.similarity_score,
                            'isRepresentative': member.is_representative,
                            'articleRank': member.article_rank,
                        }
                        for member in group.members
                    ],
                }
                for group in self.groups
            ]
        }


def safe_cosine_similarity(left: Sequence[Real], right: Sequence[Real]) -> float:
    """Return cosine similarity, safely handling invalid vector boundaries.

    Zero vectors return ``0.0``.  Mismatched dimensions and non-finite values
    are input errors because silently accepting them can create false groups.
    """

    if len(left) != len(right):
        raise ValueError('vectors must have the same dimension')
    left_values: list[float] = []
    right_values: list[float] = []
    for value in left:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError('vector values must be real numbers')
        try:
            numeric_value = float(value)
        except OverflowError, TypeError, ValueError:
            raise ValueError('vector values must be finite') from None
        if not math.isfinite(numeric_value):
            raise ValueError('vector values must be finite')
        left_values.append(numeric_value)
    for value in right:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError('vector values must be real numbers')
        try:
            numeric_value = float(value)
        except OverflowError, TypeError, ValueError:
            raise ValueError('vector values must be finite') from None
        if not math.isfinite(numeric_value):
            raise ValueError('vector values must be finite')
        right_values.append(numeric_value)
    left_scale = max((abs(value) for value in left_values), default=0.0)
    right_scale = max((abs(value) for value in right_values), default=0.0)
    if left_scale == 0.0 or right_scale == 0.0:
        return 0.0
    left_scaled = [value / left_scale for value in left_values]
    right_scaled = [value / right_scale for value in right_values]
    left_norm = math.hypot(*left_scaled)
    right_norm = math.hypot(*right_scaled)
    left_normalized = [value / left_norm for value in left_scaled]
    right_normalized = [value / right_norm for value in right_scaled]
    result = math.fsum(
        a * b for a, b in zip(left_normalized, right_normalized, strict=True)
    )
    return max(-1.0, min(1.0, result))


cosine_similarity = safe_cosine_similarity


def _date_features(value: str) -> tuple[set[str], set[tuple[int, int]]]:
    dates: set[str] = set()
    spans: set[tuple[int, int]] = set()
    for pattern in (_ISO_DATE_RE, _KOREAN_DATE_RE):
        for match in pattern.finditer(value):
            year, month, day = (int(part) for part in match.groups())
            spans.add(match.span())
            try:
                parsed_date = date(year, month, day)
            except ValueError:
                continue
            dates.add(parsed_date.isoformat())
    return dates, spans


def _normalize_number(raw_value: str) -> str:
    is_percentage = '%' in raw_value or '퍼센트' in raw_value
    number = re.sub(r'[^0-9.+-]', '', raw_value)
    try:
        decimal = Decimal(number)
    except InvalidOperation:
        return ''
    if not decimal.is_finite():
        return ''
    normalized = format(decimal.normalize(), 'f')
    if '.' in normalized:
        normalized = normalized.rstrip('0').rstrip('.')
    if normalized in {'-0', '+0', ''}:
        normalized = '0'
    return f'{normalized}%' if is_percentage else normalized


def _unit_after(value: str, end: int, *, is_percentage: bool) -> str | None:
    if is_percentage:
        return '%'
    match = _UNIT_RE.match(value[end:])
    if match is None:
        return None
    unit = re.sub(r'\s+', '', match.group('unit')).casefold()
    aliases = {
        '조원': 'KRW_TRILLION',
        '억원': 'KRW_HUNDRED_MILLION',
        '만원': 'KRW_TEN_THOUSAND',
        '원': 'KRW',
        '달러': 'USD',
        'usd': 'USD',
        'eur': 'EUR',
        '백만달러': 'USD_MILLION',
        'milliondollars': 'USD_MILLION',
        'millionusd': 'USD_MILLION',
        'billiondollars': 'USD_BILLION',
        'billionusd': 'USD_BILLION',
    }
    return aliases.get(unit, unit.upper())


def _metric_before(value: str, start: int) -> str:
    prefix = value[max(0, start - 80) : start]
    tokens = _TOKEN_RE.findall(prefix)
    for token in reversed(tokens):
        metric = normalize_text(token)
        if not metric or metric in {'the', 'a', 'an', 'is', 'was', 'are', 'and'}:
            continue
        for suffix in _METRIC_SUFFIXES:
            if metric.endswith(suffix) and len(metric) > len(suffix) + 1:
                metric = metric[: -len(suffix)]
                break
        return metric
    return ''


def _structured_values(
    value: str,
    number_spans: Iterable[tuple[int, int, str]],
) -> tuple[StructuredNumericValue, ...]:
    values: set[StructuredNumericValue] = set()
    for start, end, normalized in number_spans:
        unit = _unit_after(value, end, is_percentage=normalized.endswith('%'))
        metric = _metric_before(value, start)
        if unit and metric:
            values.add(
                StructuredNumericValue(metric=metric, unit=unit, value=normalized)
            )
    return tuple(sorted(values, key=lambda item: (item.metric, item.unit, item.value)))


def _name_org_tokens(value: str, tickers: set[str]) -> set[str]:
    """Extract only explicit organization/name evidence.

    Single title-case words and all-uppercase news words are intentionally not
    entities.  They are too common in headlines and would inflate the entity
    agreement component.  Korean organization suffixes, English organization
    suffixes, and multi-word proper-name runs are retained.
    """

    names: set[str] = set()
    ticker_words = {
        part for ticker in tickers for part in re.findall(r'[A-Z]+', ticker.upper())
    }
    matches = list(_TOKEN_RE.finditer(value))
    proper_run: list[str] = []

    def flush_proper_run() -> None:
        if len(proper_run) >= 2:
            names.add(normalize_text(' '.join(proper_run)))
        proper_run.clear()

    for match in matches:
        raw_token = match.group(0)
        canonical = normalize_text(raw_token)
        if any(character.isdigit() for character in raw_token):
            flush_proper_run()
            continue
        if canonical.upper() in ticker_words:
            flush_proper_run()
            continue
        organization_match = _ORGANIZATION_SUFFIX_RE.search(raw_token)
        if organization_match:
            if organization_match.start() > 0:
                names.add(canonical)
            flush_proper_run()
            continue
        if _EN_ORGANIZATION_SUFFIX_RE.fullmatch(raw_token):
            if proper_run:
                names.add(normalize_text(' '.join((*proper_run, raw_token))))
            flush_proper_run()
            continue
        if (
            _PROPER_TOKEN_RE.fullmatch(raw_token)
            and canonical not in _ENTITY_STOP_WORDS
        ):
            proper_run.append(raw_token)
            continue
        flush_proper_run()
    flush_proper_run()
    return names


def extract_lexical_features(
    title: str | None,
    content: str | None = None,
) -> LexicalFeatures:
    """Extract deterministic token, structure, entity, and direction features."""

    raw_title = unicodedata.normalize('NFC', title or '')
    raw_content = unicodedata.normalize('NFC', content or '')
    raw_full = ' '.join(part for part in (raw_title, raw_content) if part)
    normalized_full = normalize_text(raw_full)
    normalized_title = normalize_text(raw_title)
    title_tokens = frozenset(_TOKEN_RE.findall(normalized_title))
    content_tokens = frozenset(_TOKEN_RE.findall(normalized_full))

    dates, date_spans = _date_features(raw_full)
    number_values: set[str] = set()
    number_spans: list[tuple[int, int, str]] = []
    for match in _NUMBER_RE.finditer(raw_full):
        if any(start <= match.start() < end for start, end in date_spans):
            continue
        normalized_number = _normalize_number(match.group(0))
        if normalized_number:
            number_values.add(normalized_number)
            number_spans.append((*match.span(), normalized_number))

    tickers = {
        normalize_text(match.group(0)).upper()
        for match in _TICKER_RE.finditer(raw_full)
    }
    directions: set[str] = set()
    if _POSITIVE_DIRECTION_RE.search(normalized_full):
        directions.add('positive')
    if _NEGATIVE_DIRECTION_RE.search(normalized_full):
        directions.add('negative')
    names = _name_org_tokens(raw_full, tickers)
    return LexicalFeatures(
        title_tokens=title_tokens,
        content_tokens=content_tokens,
        numeric_values=frozenset(number_values),
        dates=frozenset(dates),
        direction_terms=frozenset(directions),
        ticker_tokens=frozenset(tickers),
        name_org_tokens=frozenset(names),
        metric_values=_structured_values(raw_full, number_spans),
    )


def dice_score(left: Iterable[str], right: Iterable[str]) -> float:
    """Return bounded Dice overlap, treating empty sets as no evidence."""

    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return 2.0 * len(left_set & right_set) / (len(left_set) + len(right_set))


def _contradiction_reasons(
    left: LexicalFeatures, right: LexicalFeatures
) -> tuple[str, ...]:
    reasons: set[str] = set()
    left_by_metric: dict[tuple[str, str], set[str]] = {}
    right_by_metric: dict[tuple[str, str], set[str]] = {}
    for item in left.metric_values:
        left_by_metric.setdefault((item.metric, item.unit), set()).add(item.value)
    for item in right.metric_values:
        right_by_metric.setdefault((item.metric, item.unit), set()).add(item.value)
    for key in left_by_metric.keys() & right_by_metric.keys():
        if not left_by_metric[key] & right_by_metric[key]:
            reasons.add('numeric')
            break
    if left.dates and right.dates and not left.dates & right.dates:
        reasons.add('date')
    left_directions = left.direction_terms
    right_directions = right.direction_terms
    if (left_directions == {'positive'} and right_directions == {'negative'}) or (
        left_directions == {'negative'} and right_directions == {'positive'}
    ):
        reasons.add('direction')
    return tuple(sorted(reasons))


def score_similarity(
    title_left: str | None,
    content_left: str | None,
    title_right: str | None,
    content_right: str | None,
    *,
    dense_score: float = 0.0,
    parameters: SimilarityParameters | None = None,
) -> SimilarityResult:
    """Compute bounded lexical/dense similarity and independent veto reasons."""

    selected = parameters or SimilarityParameters()
    if isinstance(dense_score, bool) or not isinstance(dense_score, Real):
        raise TypeError('dense_score must be a real number')
    raw_dense = float(dense_score)
    if not math.isfinite(raw_dense):
        raise ValueError('dense_score must be finite')
    bounded_dense = max(0.0, min(1.0, raw_dense))
    left = extract_lexical_features(title_left, content_left)
    right = extract_lexical_features(title_right, content_right)
    title_dice = dice_score(left.title_tokens, right.title_tokens)
    full_dice = dice_score(left.full_tokens, right.full_tokens)
    numeric_date = dice_score(left.numeric_date_tokens, right.numeric_date_tokens)
    ticker_name_org = dice_score(
        left.ticker_tokens | left.name_org_tokens,
        right.ticker_tokens | right.name_org_tokens,
    )
    lexical = (
        title_dice * selected.title_weight
        + full_dice * selected.full_text_weight
        + numeric_date * selected.numeric_date_weight
        + ticker_name_org * selected.ticker_name_org_weight
    )
    combined = max(
        0.0,
        min(
            1.0,
            bounded_dense * selected.dense_weight + lexical * selected.lexical_weight,
        ),
    )
    reasons = _contradiction_reasons(left, right)
    return SimilarityResult(
        dense_score=bounded_dense,
        title_dice=title_dice,
        full_dice=full_dice,
        numeric_date_agreement=numeric_date,
        ticker_name_org_agreement=ticker_name_org,
        lexical_score=lexical,
        combined_score=combined,
        contradiction_veto=bool(reasons),
        contradiction_reasons=reasons,
    )


score_article_pair = score_similarity
compare_articles = score_similarity
compute_similarity = score_similarity
safe_cosine = safe_cosine_similarity


def has_contradiction(left: LexicalFeatures, right: LexicalFeatures) -> bool:
    """Return whether two extracted feature sets trigger an independent veto."""

    return bool(_contradiction_reasons(left, right))


def _candidate_field(article: Mapping[str, object] | object, *names: str) -> Any:
    if isinstance(article, Mapping):
        for name in names:
            if name in article:
                return article[name]
        return None
    for name in names:
        value = getattr(article, name, None)
        if value is not None:
            return value
    return None


def _coerce_candidate(
    article: ArticleCandidate | Mapping[str, object] | object,
) -> ArticleCandidate:
    if isinstance(article, ArticleCandidate):
        return article
    published_at = _candidate_field(article, 'published_at', 'publishedAt')
    if isinstance(published_at, str):
        try:
            published_at = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('published_at must be a valid datetime') from None
    return ArticleCandidate(
        processed_article_id=_candidate_field(
            article, 'processed_article_id', 'processedArticleId', 'id'
        ),
        canonical_title=_candidate_field(article, 'canonical_title', 'title'),
        source_summary=_candidate_field(article, 'source_summary', 'summary'),
        article_body_excerpt=_candidate_field(
            article, 'article_body_excerpt', 'body_excerpt', 'body'
        ),
        publisher_name=_candidate_field(article, 'publisher_name', 'publisher'),
        origin_link=_candidate_field(article, 'origin_link', 'originLink'),
        published_at=published_at,
        vector=_candidate_field(article, 'vector', 'embedding', 'embeddings'),
        exact_duplicate_count=_candidate_field(
            article, 'exact_duplicate_count', 'exactDuplicateCount'
        )
        or 0,
        is_cluster_representative=bool(
            _candidate_field(
                article, 'is_cluster_representative', 'isClusterRepresentative'
            )
            or False
        ),
    )


def _normalise_published_at(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('published_at must be timezone-aware')
    return value.astimezone(UTC)


def _article_sort_key(
    article: ArticleCandidate, normalized_times: Mapping[int, datetime | None]
) -> tuple[bool, float, int]:
    published_at = normalized_times[article.processed_article_id]
    if published_at is None:
        return (True, 0.0, article.processed_article_id)
    return (False, -published_at.timestamp(), article.processed_article_id)


def _pair_key(left_id: int, right_id: int) -> tuple[int, int]:
    return (left_id, right_id) if left_id < right_id else (right_id, left_id)


def _validate_vector(vector: tuple[Real, ...] | None) -> None:
    if not vector:
        raise ValueError('vector is required and must not be empty')
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError('vector values must be real numbers')
        try:
            numeric_value = float(value)
        except OverflowError, TypeError, ValueError:
            raise ValueError('vector values must be finite') from None
        if not math.isfinite(numeric_value):
            raise ValueError('vector values must be finite')


def _validate_threshold(threshold: float) -> float:
    if isinstance(threshold, bool) or not isinstance(threshold, Real):
        raise TypeError('threshold must be a real number')
    value = float(threshold)
    if not math.isfinite(value):
        raise ValueError('threshold must be finite')
    if not 0.0 <= value <= 1.0:
        raise ValueError('threshold must be between 0 and 1')
    return value


def _has_information(value: str | None) -> bool:
    return bool(value and value.strip())


def _completeness(article: ArticleCandidate, published_at: datetime | None) -> float:
    present = sum(
        (
            _has_information(article.source_summary),
            _has_information(article.article_body_excerpt),
            _has_information(article.publisher_name),
            _has_information(article.origin_link),
            published_at is not None,
        )
    )
    return present / 5.0


def _recency_values(
    candidates: Sequence[ArticleCandidate],
    normalized_times: Mapping[int, datetime | None],
) -> dict[int, float]:
    values = [
        published_at.timestamp()
        for article in candidates
        if (published_at := normalized_times[article.processed_article_id]) is not None
    ]
    if not values:
        return {article.processed_article_id: 0.0 for article in candidates}
    oldest = min(values)
    newest = max(values)
    span = newest - oldest
    recencies: dict[int, float] = {}
    for article in candidates:
        published_at = normalized_times[article.processed_article_id]
        if published_at is None:
            recencies[article.processed_article_id] = 0.0
        elif span == 0.0:
            recencies[article.processed_article_id] = 1.0
        else:
            recencies[article.processed_article_id] = (
                published_at.timestamp() - oldest
            ) / span
    return recencies


def _average_similarity(
    article: ArticleCandidate,
    members: Sequence[ArticleCandidate],
    pair_scores: Mapping[tuple[int, int], SimilarityResult],
) -> float:
    if len(members) == 1:
        return 1.0
    scores = [
        pair_scores[
            _pair_key(article.processed_article_id, other.processed_article_id)
        ].combined_score
        for other in members
        if other.processed_article_id != article.processed_article_id
    ]
    return math.fsum(scores) / len(scores)


def group_similar_articles(
    articles: Iterable[ArticleCandidate | Mapping[str, object] | object],
    *,
    threshold: float = 0.8,
    parameters: SimilarityParameters | None = None,
) -> SimilarityGroupingResult:
    """Group article candidates with deterministic complete-link first-fit.

    Candidates are sorted by UTC-normalized publication time descending and ID
    ascending.  Every unordered pair is scored once before any group is built;
    group admission then checks all existing members and contradiction vetoes.
    """

    selected_threshold = _validate_threshold(threshold)
    candidates = tuple(_coerce_candidate(article) for article in articles)
    ids = [article.processed_article_id for article in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError('processed_article_id values must be unique')
    normalized_times = {
        article.processed_article_id: _normalise_published_at(article.published_at)
        for article in candidates
    }
    for article in candidates:
        _validate_vector(article.vector)
    dimensions = {len(article.vector or ()) for article in candidates}
    if len(dimensions) > 1:
        raise ValueError('vectors must have the same dimension')

    ordered = tuple(
        sorted(candidates, key=lambda item: _article_sort_key(item, normalized_times))
    )
    recencies = _recency_values(ordered, normalized_times)
    pair_scores: dict[tuple[int, int], SimilarityResult] = {}
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            dense_score = safe_cosine_similarity(left.vector or (), right.vector or ())
            pair_scores[
                _pair_key(left.processed_article_id, right.processed_article_id)
            ] = score_similarity(
                left.canonical_title,
                left.source_summary or left.article_body_excerpt,
                right.canonical_title,
                right.source_summary or right.article_body_excerpt,
                dense_score=dense_score,
                parameters=parameters,
            )

    groups: list[list[ArticleCandidate]] = []
    for article in ordered:
        for members in groups:
            if all(
                (
                    pair := pair_scores[
                        _pair_key(
                            article.processed_article_id, member.processed_article_id
                        )
                    ]
                ).combined_score
                >= selected_threshold
                and not pair.contradiction_veto
                for member in members
            ):
                members.append(article)
                break
        else:
            groups.append([article])

    def representative(members: Sequence[ArticleCandidate]) -> ArticleCandidate:
        def key(article: ArticleCandidate) -> tuple[float, float, int]:
            average = (
                _average_similarity(article, members, pair_scores)
                if len(members) > 1
                else 0.0
            )
            completeness = _completeness(
                article, normalized_times[article.processed_article_id]
            )
            score = (
                average * 0.70
                + completeness * 0.20
                + recencies[article.processed_article_id] * 0.10
            )
            published_at = normalized_times[article.processed_article_id]
            timestamp = (
                published_at.timestamp() if published_at is not None else float('-inf')
            )
            return (score, timestamp, -article.processed_article_id)

        return max(members, key=key)

    ranked_groups = sorted(
        ((members, representative(members)) for members in groups),
        key=lambda item: _article_sort_key(item[1], normalized_times),
    )
    output_groups: list[SimilarityGroup] = []
    for group_rank, (members, representative_article) in enumerate(
        ranked_groups, start=1
    ):
        ordered_members = [
            representative_article,
            *(
                article
                for article in sorted(
                    members, key=lambda item: _article_sort_key(item, normalized_times)
                )
                if article.processed_article_id
                != representative_article.processed_article_id
            ),
        ]
        output_members = tuple(
            SimilarityGroupMember(
                processed_article_id=article.processed_article_id,
                similarity_score=_average_similarity(article, members, pair_scores),
                is_representative=article.processed_article_id
                == representative_article.processed_article_id,
                article_rank=article_rank,
            )
            for article_rank, article in enumerate(ordered_members, start=1)
        )
        output_groups.append(
            SimilarityGroup(
                group_rank=group_rank,
                representative_article_id=representative_article.processed_article_id,
                members=output_members,
            )
        )
    return SimilarityGroupingResult(groups=tuple(output_groups))


build_similarity_groups = group_similar_articles
build_article_similarity_groups = group_similar_articles
group_articles = group_similar_articles


__all__ = [
    'ArticleCandidate',
    'LexicalFeatures',
    'SimilarityArticle',
    'SimilarityGroup',
    'SimilarityGroupMember',
    'SimilarityGroupingResult',
    'SimilarityParameters',
    'SimilarityResult',
    'StructuredNumericValue',
    'build_article_similarity_groups',
    'build_similarity_groups',
    'compare_articles',
    'compute_similarity',
    'cosine_similarity',
    'dice_score',
    'extract_lexical_features',
    'group_articles',
    'group_similar_articles',
    'has_contradiction',
    'safe_cosine_similarity',
    'safe_cosine',
    'score_article_pair',
    'score_similarity',
]
