"""Git-managed classification rules for the active leaf theme catalog.

The database owns the theme hierarchy while this module owns the human-reviewed
phrase definitions used by the LLM prompt and deterministic fallback.  Loading
is deliberately strict: a malformed file or a catalog/rules mismatch must stop
the batch before it starts processing clusters.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

RULES_PATH = Path(__file__).with_name('theme_rules.yaml')
THEME_RULES_PATH = RULES_PATH

# Keep the catalog order from db/schema_postgresql.sql so downstream tie
# breaking can use the stable seed order without querying the database again.
CANONICAL_LEAF_CODES = (
    'MACRO_ECONOMIC_DATA_INFLATION',
    'MACRO_ECONOMIC_DATA_EMPLOYMENT_GROWTH',
    'MACRO_MONETARY_MARKETS_INTEREST_RATES_BONDS',
    'MACRO_MONETARY_MARKETS_LIQUIDITY',
    'MACRO_MONETARY_MARKETS_FX',
    'MACRO_POLICY_RISK_FISCAL_REGULATION',
    'MACRO_POLICY_RISK_GEOPOLITICS_TRADE',
    'SECTOR_SEMICONDUCTORS_MEMORY_HBM',
    'SECTOR_SEMICONDUCTORS_FOUNDRY_SYSTEM',
    'SECTOR_SEMICONDUCTORS_EQUIPMENT_MATERIALS',
    'SECTOR_AI_SOFTWARE_AI_INFRASTRUCTURE',
    'SECTOR_AI_SOFTWARE_CLOUD_PLATFORM',
    'SECTOR_FINANCIALS_BANKING',
    'SECTOR_FINANCIALS_SECURITIES_INSURANCE',
    'SECTOR_AUTOS_MOBILITY_AUTOMAKERS_COMPONENTS',
    'SECTOR_AUTOS_MOBILITY_EV_BATTERY',
    'SECTOR_BIO_HEALTHCARE_PHARMA_BIOTECH',
    'SECTOR_BIO_HEALTHCARE_MEDICAL_SERVICES',
    'SECTOR_CONSUMER_CONTENT_RETAIL_ECOMMERCE',
    'SECTOR_CONSUMER_CONTENT_BRANDS_MEDIA_GAMING',
    'SECTOR_INDUSTRIALS_INFRA_SHIPBUILDING_DEFENSE',
    'SECTOR_INDUSTRIALS_INFRA_CONSTRUCTION_POWER',
    'SECTOR_INDUSTRIALS_INFRA_TRANSPORT_LOGISTICS',
    'SECTOR_ENERGY_MATERIALS_OIL_GAS',
    'SECTOR_ENERGY_MATERIALS_STEEL_CHEMICALS',
    'CORPORATE_EVENT_PERFORMANCE_EARNINGS_GUIDANCE',
    'CORPORATE_EVENT_PERFORMANCE_ORDERS_CONTRACTS',
    'CORPORATE_EVENT_CAPITAL_ACTION_MNA',
    'CORPORATE_EVENT_CAPITAL_ACTION_IPO_CAPITAL_RAISE',
    'CORPORATE_EVENT_CAPITAL_ACTION_DIVIDEND_BUYBACK',
    'CORPORATE_EVENT_GOVERNANCE_MANAGEMENT',
    'MARKET_FLOW_INVESTOR_FOREIGN',
    'MARKET_FLOW_INVESTOR_INSTITUTIONAL',
    'MARKET_FLOW_INVESTOR_RETAIL',
    'MARKET_FLOW_POSITIONING_SHORT_SELLING',
    'MARKET_FLOW_POSITIONING_ETF_REBALANCING',
    'MARKET_FLOW_POSITIONING_VOLATILITY_SENTIMENT',
    'ALTERNATIVE_ASSET_COMMODITIES_ENERGY_PRICES',
    'ALTERNATIVE_ASSET_COMMODITIES_METALS_AGRICULTURE',
    'ALTERNATIVE_ASSET_DIGITAL_CRYPTO',
)

APPROVED_FALLBACK_CODES = frozenset(
    {
        'MACRO_ECONOMIC_DATA_INFLATION',
        'MACRO_ECONOMIC_DATA_EMPLOYMENT_GROWTH',
        'MACRO_MONETARY_MARKETS_INTEREST_RATES_BONDS',
        'MACRO_MONETARY_MARKETS_FX',
        'SECTOR_SEMICONDUCTORS_MEMORY_HBM',
        'SECTOR_SEMICONDUCTORS_FOUNDRY_SYSTEM',
        'SECTOR_SEMICONDUCTORS_EQUIPMENT_MATERIALS',
        'SECTOR_AUTOS_MOBILITY_AUTOMAKERS_COMPONENTS',
        'SECTOR_AUTOS_MOBILITY_EV_BATTERY',
        'SECTOR_BIO_HEALTHCARE_PHARMA_BIOTECH',
        'SECTOR_INDUSTRIALS_INFRA_SHIPBUILDING_DEFENSE',
        'CORPORATE_EVENT_PERFORMANCE_EARNINGS_GUIDANCE',
        'CORPORATE_EVENT_PERFORMANCE_ORDERS_CONTRACTS',
        'CORPORATE_EVENT_CAPITAL_ACTION_MNA',
        'CORPORATE_EVENT_CAPITAL_ACTION_IPO_CAPITAL_RAISE',
        'CORPORATE_EVENT_CAPITAL_ACTION_DIVIDEND_BUYBACK',
        'MARKET_FLOW_INVESTOR_FOREIGN',
        'MARKET_FLOW_INVESTOR_INSTITUTIONAL',
        'MARKET_FLOW_POSITIONING_SHORT_SELLING',
        'ALTERNATIVE_ASSET_DIGITAL_CRYPTO',
    }
)

# Readable aliases for callers that prefer the terminology from the design
# document or the shorthand used by the classifier task.
CANONICAL_THEME_CODES = CANONICAL_LEAF_CODES
APPROVED_FALLBACK_THEME_CODES = APPROVED_FALLBACK_CODES
CANONICAL_PARENT_CODES = frozenset(
    {
        'MACRO',
        'MACRO_ECONOMIC_DATA',
        'MACRO_MONETARY_MARKETS',
        'MACRO_POLICY_RISK',
        'SECTOR',
        'SECTOR_SEMICONDUCTORS',
        'SECTOR_AI_SOFTWARE',
        'SECTOR_FINANCIALS',
        'SECTOR_AUTOS_MOBILITY',
        'SECTOR_BIO_HEALTHCARE',
        'SECTOR_CONSUMER_CONTENT',
        'SECTOR_INDUSTRIALS_INFRA',
        'SECTOR_ENERGY_MATERIALS',
        'CORPORATE_EVENT',
        'CORPORATE_EVENT_PERFORMANCE',
        'CORPORATE_EVENT_CAPITAL_ACTION',
        'CORPORATE_EVENT_GOVERNANCE',
        'MARKET_FLOW',
        'MARKET_FLOW_INVESTOR',
        'MARKET_FLOW_POSITIONING',
        'ALTERNATIVE_ASSET',
        'ALTERNATIVE_ASSET_COMMODITIES',
        'ALTERNATIVE_ASSET_DIGITAL',
    }
)
THEME_PARENT_CODES = CANONICAL_PARENT_CODES
_RESERVED_CODE_SEGMENTS = frozenset({'GENERAL', 'OTHER', 'UNCLASSIFIED'})


class ThemeRuleValidationError(ValueError):
    """Raised when the Git rules file is malformed or disagrees with the catalog."""


ThemeRulesValidationError = ThemeRuleValidationError


@dataclass(frozen=True, slots=True)
class FallbackRule:
    """Typed deterministic fallback configuration for one leaf theme."""

    enabled: bool
    minimum_score: int
    strong_phrases: tuple[str, ...]
    supporting_term_groups: tuple[tuple[str, ...], ...]
    excluded_phrases: tuple[str, ...]

    @property
    def minimumScore(self) -> int:  # noqa: N802 - mirrors the YAML contract
        """Return the canonical camelCase minimum score."""
        return self.minimum_score

    @property
    def strongPhrases(self) -> tuple[str, ...]:  # noqa: N802
        """Return strong fallback phrases using the YAML field name."""
        return self.strong_phrases

    @property
    def supportingTermGroups(self) -> tuple[tuple[str, ...], ...]:  # noqa: N802
        """Return supporting term groups using the YAML field name."""
        return self.supporting_term_groups

    @property
    def excludedPhrases(self) -> tuple[str, ...]:  # noqa: N802
        """Return excluded fallback phrases using the YAML field name."""
        return self.excluded_phrases


@dataclass(frozen=True, slots=True)
class ThemeRule:
    """Frozen inclusion, exclusion, and fallback rules for one leaf code."""

    code: str
    inclusion_criteria: tuple[str, ...]
    exclusion_criteria: tuple[str, ...]
    fallback: FallbackRule

    @property
    def include(self) -> tuple[str, ...]:
        """Return inclusion phrases for classifier code using the task shorthand."""
        return self.inclusion_criteria

    @property
    def exclude(self) -> tuple[str, ...]:
        """Return exclusion phrases for classifier code using the task shorthand."""
        return self.exclusion_criteria

    @property
    def strong(self) -> tuple[str, ...]:
        """Return strong fallback phrases for classifier code."""
        return self.fallback.strong_phrases

    @property
    def fallback_enabled(self) -> bool:
        """Return whether deterministic fallback is enabled for this code."""
        return self.fallback.enabled

    @property
    def minimum_score(self) -> int:
        """Return the fallback score threshold for this code."""
        return self.fallback.minimum_score

    @property
    def strong_phrases(self) -> tuple[str, ...]:
        """Return strong fallback phrases for this code."""
        return self.fallback.strong_phrases

    @property
    def supporting_term_groups(self) -> tuple[tuple[str, ...], ...]:
        """Return supporting fallback term groups for this code."""
        return self.fallback.supporting_term_groups

    @property
    def excluded_phrases(self) -> tuple[str, ...]:
        """Return fallback exclusion phrases for this code."""
        return self.fallback.excluded_phrases

    @property
    def inclusionCriteria(self) -> tuple[str, ...]:  # noqa: N802
        """Return inclusion phrases using the canonical YAML field name."""
        return self.inclusion_criteria

    @property
    def exclusionCriteria(self) -> tuple[str, ...]:  # noqa: N802
        """Return exclusion phrases using the canonical YAML field name."""
        return self.exclusion_criteria

    @property
    def fallbackEnabled(self) -> bool:  # noqa: N802
        """Return fallback state using the task brief's shorthand name."""
        return self.fallback.enabled


@dataclass(frozen=True, slots=True)
class ThemeRuleCatalog(Mapping[str, ThemeRule]):
    """Read-only ordered mapping of every validated leaf theme rule."""

    rules: tuple[ThemeRule, ...]
    _by_code: Mapping[str, ThemeRule] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        by_code = {rule.code: rule for rule in self.rules}
        if len(by_code) != len(self.rules):
            raise ThemeRuleValidationError('duplicate rule codes in typed catalog')
        object.__setattr__(self, '_by_code', MappingProxyType(by_code))

    def __getitem__(self, code: str) -> ThemeRule:
        return self._by_code[code]

    def __iter__(self) -> Iterator[str]:
        return (rule.code for rule in self.rules)

    def __len__(self) -> int:
        return len(self.rules)

    @property
    def codes(self) -> tuple[str, ...]:
        """Return leaf codes in catalog seed order."""
        return tuple(rule.code for rule in self.rules)

    @property
    def by_code(self) -> Mapping[str, ThemeRule]:
        """Return the immutable code-to-rule view used by classifiers."""
        return self._by_code


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader variant that does not silently overwrite duplicate keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ThemeRuleValidationError(f'duplicate YAML key: {key!r}')
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_theme_rules(
    catalog_codes: Iterable[str] | None = None,
    *,
    rules_path: str | Path | None = None,
) -> ThemeRuleCatalog:
    """Load and cache the validated Git rules for the supplied leaf catalog.

    Args:
        catalog_codes: Active leaf codes read from the seeded catalog.  When
            omitted, the canonical 40-code seed is used.
        rules_path: Optional path used by tests or tooling.  The production
            default is resolved relative to this module, never the cwd.

    Raises:
        ThemeRuleValidationError: If parsing, schema validation, or catalog
            reconciliation fails.
    """
    expected_codes = _normalise_catalog_codes(catalog_codes)
    path = Path(rules_path) if rules_path is not None else RULES_PATH
    return _load_theme_rules_cached(expected_codes, str(path.resolve()))


def clear_theme_rules_cache() -> None:
    """Clear cached rule catalogs for tests or an explicit config reload."""
    _load_theme_rules_cached.cache_clear()


def _normalise_catalog_codes(
    catalog_codes: Iterable[str] | None,
) -> tuple[str, ...]:
    values = CANONICAL_LEAF_CODES if catalog_codes is None else tuple(catalog_codes)
    if isinstance(catalog_codes, str):
        raise ThemeRuleValidationError('catalog_codes must be an iterable of codes')
    if any(not isinstance(code, str) or not code.strip() for code in values):
        raise ThemeRuleValidationError('catalog_codes must contain non-blank strings')
    if len(set(values)) != len(values):
        raise ThemeRuleValidationError('catalog_codes contains duplicate codes')
    _reject_non_leaf_codes(values)
    return values


@lru_cache(maxsize=16)
def _load_theme_rules_cached(
    catalog_codes: tuple[str, ...],
    rules_path: str,
) -> ThemeRuleCatalog:
    path = Path(rules_path)
    try:
        raw = yaml.load(path.read_text(encoding='utf-8'), Loader=_UniqueKeyLoader)
    except FileNotFoundError as exc:
        raise ThemeRuleValidationError(f'rules file not found: {path}') from exc
    except OSError as exc:
        raise ThemeRuleValidationError(f'cannot read rules file: {path}') from exc
    except yaml.YAMLError as exc:
        raise ThemeRuleValidationError(f'invalid YAML in rules file: {path}') from exc

    return _build_catalog(raw, catalog_codes, path)


def _build_catalog(
    raw: Any,
    catalog_codes: tuple[str, ...],
    path: Path,
) -> ThemeRuleCatalog:
    if not isinstance(raw, Mapping):
        raise ThemeRuleValidationError(f'rules file must contain a mapping: {path}')

    actual_codes = set(raw)
    if any(not isinstance(code, str) for code in actual_codes):
        raise ThemeRuleValidationError('rule codes must be strings')
    _reject_reserved_codes(actual_codes)

    expected_codes = set(catalog_codes)
    missing = expected_codes - actual_codes
    extra = actual_codes - expected_codes
    if missing:
        formatted = ', '.join(sorted(missing))
        raise ThemeRuleValidationError(f'missing rule codes: {formatted}')
    if extra:
        formatted = ', '.join(sorted(extra))
        raise ThemeRuleValidationError(f'unknown/extra rule codes: {formatted}')

    rules_by_code = {code: _validate_rule(code, raw[code]) for code in catalog_codes}
    if expected_codes == set(CANONICAL_LEAF_CODES):
        enabled_codes = {
            code for code, rule in rules_by_code.items() if rule.fallback.enabled
        }
        if enabled_codes != APPROVED_FALLBACK_CODES:
            missing_enabled = APPROVED_FALLBACK_CODES - enabled_codes
            extra_enabled = enabled_codes - APPROVED_FALLBACK_CODES
            raise ThemeRuleValidationError(
                'fallback candidates disagree with approved catalog: '
                f'missing={sorted(missing_enabled)}, extra={sorted(extra_enabled)}'
            )

    return ThemeRuleCatalog(tuple(rules_by_code.values()))


def _reject_reserved_codes(codes: Iterable[Any]) -> None:
    for code in codes:
        if not isinstance(code, str):
            continue
        segments = set(code.upper().split('_'))
        if segments & _RESERVED_CODE_SEGMENTS:
            raise ThemeRuleValidationError(
                f'reserved fallback code is not allowed: {code}'
            )


def _reject_non_leaf_codes(codes: Iterable[Any]) -> None:
    for code in codes:
        if code in CANONICAL_PARENT_CODES:
            raise ThemeRuleValidationError(
                f'parent theme cannot have classification rules: {code}'
            )
    _reject_reserved_codes(codes)


def _validate_rule(code: str, raw_rule: Any) -> ThemeRule:
    if not isinstance(raw_rule, Mapping):
        raise ThemeRuleValidationError(f'rule {code} must be a mapping')
    expected_keys = {'inclusionCriteria', 'exclusionCriteria', 'fallback'}
    _reject_unknown_keys(raw_rule, expected_keys, f'rule {code}')

    inclusion = _validate_terms(
        raw_rule.get('inclusionCriteria'), code, 'inclusionCriteria'
    )
    exclusion = _validate_terms(
        raw_rule.get('exclusionCriteria'), code, 'exclusionCriteria'
    )
    _reject_overlap(inclusion, exclusion, code, 'inclusionCriteria/exclusionCriteria')

    raw_fallback = raw_rule.get('fallback')
    if not isinstance(raw_fallback, Mapping):
        raise ThemeRuleValidationError(f'fallback for {code} must be a mapping')
    fallback_keys = {
        'enabled',
        'minimumScore',
        'strongPhrases',
        'supportingTermGroups',
        'excludedPhrases',
    }
    _reject_unknown_keys(raw_fallback, fallback_keys, f'fallback for {code}')

    enabled = raw_fallback.get('enabled')
    if not isinstance(enabled, bool):
        raise ThemeRuleValidationError(f'enabled for {code} must be a boolean')
    minimum_score = raw_fallback.get('minimumScore')
    if isinstance(minimum_score, bool) or not isinstance(minimum_score, int):
        raise ThemeRuleValidationError(f'minimumScore for {code} must be an integer')
    if minimum_score < 0:
        raise ThemeRuleValidationError(f'minimumScore for {code} cannot be negative')

    strong_phrases = _validate_terms(
        raw_fallback.get('strongPhrases'), code, 'strongPhrases'
    )
    supporting_groups = _validate_term_groups(
        raw_fallback.get('supportingTermGroups'), code
    )
    _reject_duplicate_terms(
        strong_phrases + tuple(term for group in supporting_groups for term in group),
        code,
        'fallback evidence phrases',
    )
    excluded_phrases = _validate_terms(
        raw_fallback.get('excludedPhrases'), code, 'excludedPhrases'
    )
    positive_fallback_terms = strong_phrases + tuple(
        term for group in supporting_groups for term in group
    )
    _reject_overlap(
        positive_fallback_terms,
        excluded_phrases,
        code,
        'fallback positive/excluded phrases',
    )
    if enabled and minimum_score != 6:
        raise ThemeRuleValidationError(
            f'minimumScore for enabled fallback {code} must be 6'
        )
    if enabled and not strong_phrases:
        raise ThemeRuleValidationError(
            f'enabled fallback {code} requires strong evidence phrases'
        )

    return ThemeRule(
        code=code,
        inclusion_criteria=inclusion,
        exclusion_criteria=exclusion,
        fallback=FallbackRule(
            enabled=enabled,
            minimum_score=minimum_score,
            strong_phrases=strong_phrases,
            supporting_term_groups=supporting_groups,
            excluded_phrases=excluded_phrases,
        ),
    )


def _reject_unknown_keys(
    mapping: Mapping[Any, Any],
    expected_keys: set[str],
    context: str,
) -> None:
    actual_keys = set(mapping)
    unknown = actual_keys - expected_keys
    missing = expected_keys - actual_keys
    if unknown:
        raise ThemeRuleValidationError(
            f'unknown keys in {context}: {sorted(map(str, unknown))}'
        )
    if missing:
        raise ThemeRuleValidationError(f'missing keys in {context}: {sorted(missing)}')


def _validate_terms(raw_terms: Any, code: str, field_name: str) -> tuple[str, ...]:
    if not isinstance(raw_terms, list):
        raise ThemeRuleValidationError(f'{field_name} for {code} must be a list')
    terms: list[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        if not isinstance(term, str):
            raise ThemeRuleValidationError(
                f'{field_name} for {code} must contain strings'
            )
        cleaned = term.strip()
        if not cleaned:
            raise ThemeRuleValidationError(f'blank term in {field_name} for {code}')
        normalized = cleaned.casefold()
        if normalized in seen:
            raise ThemeRuleValidationError(
                f'duplicate term in {field_name} for {code}: {cleaned!r}'
            )
        seen.add(normalized)
        terms.append(cleaned)
    return tuple(terms)


def _reject_duplicate_terms(
    terms: Iterable[str],
    code: str,
    context: str,
) -> None:
    normalized_terms = [term.casefold() for term in terms]
    if len(set(normalized_terms)) != len(normalized_terms):
        duplicates = sorted(
            {term for term in normalized_terms if normalized_terms.count(term) > 1}
        )
        raise ThemeRuleValidationError(
            f'duplicate term in {context} for {code}: {duplicates}'
        )


def _validate_term_groups(
    raw_groups: Any,
    code: str,
) -> tuple[tuple[str, ...], ...]:
    if not isinstance(raw_groups, list):
        raise ThemeRuleValidationError(
            f'supportingTermGroups for {code} must be a list'
        )
    groups: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for index, raw_group in enumerate(raw_groups):
        if not isinstance(raw_group, list):
            raise ThemeRuleValidationError(
                f'supportingTermGroups[{index}] for {code} must be a list'
            )
        group = _validate_terms(raw_group, code, f'supportingTermGroups[{index}]')
        for term in group:
            normalized = term.casefold()
            if normalized in seen:
                raise ThemeRuleValidationError(
                    f'duplicate supporting term for {code}: {term!r}'
                )
            seen.add(normalized)
        groups.append(group)
    return tuple(groups)


def _reject_overlap(
    positive_terms: Iterable[str],
    negative_terms: Iterable[str],
    code: str,
    context: str,
) -> None:
    positive = {term.casefold() for term in positive_terms}
    negative = {term.casefold() for term in negative_terms}
    overlap = positive & negative
    if overlap:
        raise ThemeRuleValidationError(
            f'overlap in {context} for {code}: {sorted(overlap)}'
        )


# Compatibility aliases keep the Task 3 import surface obvious without making
# multiple loaders with subtly different cache or validation behavior.
load_theme_rule_catalog = load_theme_rules
load_rules = load_theme_rules


# Expose cache clearing on the loader just like functools.lru_cache does.  This
# is useful to tests that intentionally replace the YAML path between cases.
load_theme_rules.cache_clear = clear_theme_rules_cache  # type: ignore[attr-defined]
