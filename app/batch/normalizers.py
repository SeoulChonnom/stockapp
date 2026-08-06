from __future__ import annotations

import re
from hashlib import sha256
from html import unescape
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_HTML_TAG_RE = re.compile(r'<[^>]+>')
_WHITESPACE_RE = re.compile(r'\s+')
_TOKEN_RE = re.compile(r'[0-9A-Za-z가-힣]{2,}')


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
    'canonicalize_link',
    'excerpt_text',
    'metadata_optional_string',
    'metadata_string_list',
    'normalize_title',
    'normalize_whitespace',
    'strip_html',
    'tokenize_text',
]
