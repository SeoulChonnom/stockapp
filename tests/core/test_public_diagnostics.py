from app.core.public_diagnostics import (
    AI_PROVIDER_FAILURE_MESSAGE,
    sanitize_public_diagnostic,
)


def test_sanitize_public_diagnostic_redacts_legacy_provider_payload() -> None:
    raw = (
        'Cluster enrichment fallback for US cluster 1: '
        '429 RESOURCE_EXHAUSTED quota RetryInfo secret-token '
        'https://generativelanguage.googleapis.com'
    )

    sanitized = sanitize_public_diagnostic(raw)

    assert sanitized == (
        f'Cluster enrichment fallback for US cluster 1: {AI_PROVIDER_FAILURE_MESSAGE}'
    )
    assert '429' not in sanitized
    assert 'RetryInfo' not in sanitized
    assert 'secret-token' not in sanitized
    assert 'googleapis.com' not in sanitized


def test_sanitize_public_diagnostic_preserves_normal_naver_partial_reason() -> None:
    reason = (
        'Naver news pagination cap was reached before covering the persisted '
        "window for keyword '증시'."
    )

    assert sanitize_public_diagnostic(reason) == reason


def test_sanitize_public_diagnostic_hides_plain_legacy_provider_message() -> None:
    raw = 'AI summary fallback for MARKET_SUMMARY/US: provider timeout'

    assert sanitize_public_diagnostic(raw) == (
        f'AI summary fallback for MARKET_SUMMARY/US: {AI_PROVIDER_FAILURE_MESSAGE}'
    )
