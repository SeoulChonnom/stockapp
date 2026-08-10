import pytest

from app.core import error_diagnostics
from app.core.error_diagnostics import build_step_error_diagnostics, mask_error_log
from app.core.settings import Settings


def _settings() -> Settings:
    return Settings(
        database_url='postgresql://db-user:db-password@example.com/stockapp',
        jwt_secret='jwt-secret-value',
        auth_stub_token='auth-stub-token',
        naver_client_secret='naver-secret-value',
        gemini_api_key='gemini-api-key',
    )


def test_build_step_error_diagnostics_preserves_frames_and_safe_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(error_diagnostics, 'get_settings', lambda: _settings())
    try:
        raise RuntimeError('ordinary failure')
    except RuntimeError as exc:
        result = build_step_error_diagnostics(
            exc,
            public_message='배치 단계 실행 중 오류가 발생했습니다.',
        )

    assert result.error_message == '배치 단계 실행 중 오류가 발생했습니다.'
    assert 'RuntimeError: ordinary failure' in result.error_log
    assert 'test_build_step_error_diagnostics' in result.error_log


def test_mask_error_log_redacts_configured_sensitive_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(error_diagnostics, 'get_settings', lambda: _settings())
    secrets = (
        'db-user',
        'db-password',
        'jwt-secret-value',
        'auth-stub-token',
        'naver-secret-value',
        'gemini-api-key',
    )

    masked = mask_error_log(' | '.join(secrets))

    assert masked is not None
    for secret in secrets:
        assert secret not in masked
    assert '[REDACTED]' in masked


@pytest.mark.parametrize(
    ('raw', 'secret'),
    [
        ('Authorization: Bearer provider-token', 'provider-token'),
        ('Authorization: Basic provider-basic-token', 'provider-basic-token'),
        ('password=provider-password', 'provider-password'),
        ('credential=provider-credential', 'provider-credential'),
        ('credentials=provider-credentials', 'provider-credentials'),
        ('authorization=provider-authorization', 'provider-authorization'),
        ('{"api_key": "provider-key"}', 'provider-key'),
        ('postgresql://user:db-pass@example/db', 'db-pass'),
        ('token=temporary-token', 'temporary-token'),
        (
            'token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature-part',
            'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature-part',
        ),
    ],
)
def test_mask_error_log_redacts_common_credentials(
    raw: str,
    secret: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(error_diagnostics, 'get_settings', lambda: _settings())

    masked = mask_error_log(raw)

    assert masked is not None
    assert secret not in masked
    assert '[REDACTED]' in masked


def test_build_step_error_diagnostics_preserves_exception_chaining_after_masking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(error_diagnostics, 'get_settings', lambda: _settings())

    class InnerError(Exception):
        pass

    class OuterError(Exception):
        pass

    try:
        try:
            raise InnerError('jwt-secret-value')
        except InnerError as exc:
            raise OuterError('outer failure') from exc
    except OuterError as exc:
        result = build_step_error_diagnostics(exc, public_message='Batch failed.')

    assert 'InnerError: [REDACTED]' in result.error_log
    assert 'OuterError: outer failure' in result.error_log
    assert 'jwt-secret-value' not in result.error_log


def test_mask_error_log_preserves_none() -> None:
    assert mask_error_log(None) is None


def test_mask_error_log_truncates_large_output_from_both_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(error_diagnostics, 'get_settings', lambda: _settings())
    raw = 'START-' + ('x' * (32 * 1024)) + '-END'

    masked = mask_error_log(raw)

    assert masked is not None
    assert len(masked) <= 32 * 1024
    assert masked.startswith('START-')
    assert masked.endswith('-END')
    assert '[TRUNCATED]' in masked


def test_build_step_error_diagnostics_retains_final_exception_prefix_when_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(error_diagnostics, 'get_settings', lambda: _settings())
    message_prefix = 'failure-prefix:'
    try:
        try:
            raise ValueError('inner failure:' + ('x' * (32 * 1024)))
        except ValueError as exc:
            raise RuntimeError(message_prefix + ('x' * (32 * 1024))) from exc
    except RuntimeError as exc:
        result = build_step_error_diagnostics(exc, public_message='Batch failed.')

    assert len(result.error_log) <= 32 * 1024
    assert '[TRUNCATED]' in result.error_log
    assert f'RuntimeError: {message_prefix}' in result.error_log
