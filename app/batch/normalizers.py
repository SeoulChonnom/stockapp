from __future__ import annotations

import re
from hashlib import sha256
from html import unescape
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_HTML_TAG_RE = re.compile(r'<[^>]+>')
_WHITESPACE_RE = re.compile(r'\s+')
_TOKEN_RE = re.compile(r'[0-9A-Za-z가-힣]{2,}')
# Suffixes under which outlets register a third label. Everything the article
# corpus actually uses is here (co.kr dominates at 28k articles, with or.kr and
# go.kr present); the rest are common neighbours kept so one foreign syndication
# does not collapse to a bare public suffix.
_MULTI_LABEL_PUBLIC_SUFFIXES = frozenset(
    {
        'co.kr',
        'or.kr',
        'ne.kr',
        'go.kr',
        're.kr',
        'pe.kr',
        'co.jp',
        'co.uk',
        'com.au',
        'com.cn',
    }
)


def strip_html(value: str | None) -> str:
    if not value:
        return ''
    return unescape(_HTML_TAG_RE.sub('', value)).strip()


def normalize_whitespace(value: str | None) -> str:
    if not value:
        return ''
    return _WHITESPACE_RE.sub(' ', value).strip()


def normalize_title(value: str | None) -> str:
    return normalize_whitespace(strip_html(value))


def canonicalize_link(value: str | None) -> str:
    if not value:
        return ''
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip('/')
    query = parsed.query
    return urlunsplit((scheme, netloc, path, query, ''))


def build_dedupe_hash(title: str | None, origin_link: str | None) -> str:
    fingerprint = '|'.join([normalize_title(title), canonicalize_link(origin_link)])
    return sha256(fingerprint.encode('utf-8')).hexdigest()


def build_duplicate_key(title: str | None, publisher_name: str | None) -> str:
    """Group articles that are the same story from the same outlet.

    Deliberately not ``build_dedupe_hash``: that hash includes the URL and is
    the stored row identity, unique per ``(business_date, market_type)``, so
    it cannot be redefined without breaking existing rows on a rerun. It also
    means it never collapses anything -- an outlet publishing one story under
    several URLs produced one processed article per URL, which is why no
    exact duplicate has ever been merged and ``exact_duplicate_count`` has
    always been zero. Keying on the outlet instead of the URL merges those,
    while two outlets that happen to share a headline stay separate and reach
    similarity grouping as the distinct articles they are.
    """
    return '|'.join([normalize_title(title), publisher_name or ''])


def publisher_from_link(value: str | None) -> str | None:
    """Derive the publishing outlet from an article URL.

    The Naver news search API returns only title, link, originallink, pubDate
    and description -- there is no publisher field -- so the registrable
    domain of the article's own URL is the only publisher signal available.
    Section subdomains are dropped so ``biz.sbs.co.kr`` and ``news.sbs.co.kr``
    resolve to one outlet.
    """
    if not value:
        return None
    host = urlsplit(value.strip()).hostname
    if not host:
        return None
    host = host.strip('.').lower()
    labels = [label for label in host.split('.') if label]
    if len(labels) < 2 or all(label.isdigit() for label in labels):
        return None
    keep = 3 if '.'.join(labels[-2:]) in _MULTI_LABEL_PUBLIC_SUFFIXES else 2
    if len(labels) < keep:
        return None
    return '.'.join(labels[-keep:])


def excerpt_text(value: str | None, *, limit: int = 240) -> str:
    normalized = normalize_whitespace(strip_html(value))
    if not normalized:
        return ''
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + '…'


def tokenize_text(value: str | None) -> list[str]:
    normalized = normalize_whitespace(strip_html(value)).lower()
    seen: list[str] = []
    for token in _TOKEN_RE.findall(normalized):
        if token not in seen:
            seen.append(token)
    return seen


def metadata_string_list(
    metadata: dict[str, Any], key: str, *, fallback: list[str] | None = None
) -> list[str]:
    resolved_fallback = fallback if fallback is not None else []
    value = metadata.get(key)
    if not isinstance(value, list):
        return resolved_fallback
    strings = [item for item in value if isinstance(item, str)]
    return strings or resolved_fallback


def metadata_optional_string(
    metadata: dict[str, Any], key: str, *, fallback: str | None = None
) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) else fallback


__all__ = [
    'build_dedupe_hash',
    'build_duplicate_key',
    'canonicalize_link',
    'excerpt_text',
    'metadata_optional_string',
    'metadata_string_list',
    'normalize_title',
    'normalize_whitespace',
    'publisher_from_link',
    'strip_html',
    'tokenize_text',
]
