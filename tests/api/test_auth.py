from __future__ import annotations

from datetime import UTC, datetime, timezone

import pytest  # pyright: ignore[reportMissingImports]

from tests.support import (
    JWT_ALTERNATE_TEST_SECRET,
    JWT_ISSUER,
    JWT_TEST_SECRET,
    build_test_jwt_subject,
    load_module,
)

auth_module = load_module('app.api.deps.auth')
settings_module = load_module('app.core.settings')


@pytest.fixture(autouse=True)
def clear_settings_cache():
    settings_module.get_settings.cache_clear()
    yield
    settings_module.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def configure_jwt_auth_seam(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('STOCKAPP_APP_ENV', 'production')
    monkeypatch.setenv('STOCKAPP_CORS_ALLOWED_ORIGINS', '["http://localhost:5173"]')
    monkeypatch.setenv('STOCKAPP_JWT_SECRET', JWT_TEST_SECRET)
    monkeypatch.setenv('STOCKAPP_JWT_ALGORITHM', 'HS512')
    monkeypatch.setenv('STOCKAPP_JWT_ISSUER', JWT_ISSUER)
    monkeypatch.setenv('STOCKAPP_JWT_ACCESS_AUDIENCES', '["slcn-platform"]')


@pytest.mark.asyncio
async def test_get_current_user_accepts_valid_user_token(
    jwt_access_token: str, jwt_subject: str
):
    user = await auth_module.get_current_user(f'Bearer {jwt_access_token}')

    assert user.user_id == jwt_subject
    assert user.token == jwt_access_token
    assert user.username == 'stockapp-user'
    assert user.roles == ('USER',)


@pytest.mark.asyncio
async def test_get_current_user_accepts_valid_admin_token(jwt_access_token_factory):
    admin_subject = build_test_jwt_subject('ADMIN')
    admin_token = jwt_access_token_factory(
        subject=admin_subject,
        username='stockapp-admin',
        roles=['ADMIN'],
    )
    user = await auth_module.get_current_user(f'Bearer {admin_token}')

    assert user.user_id == admin_subject
    assert user.username == 'stockapp-admin'
    assert user.roles == ('ADMIN',)


@pytest.mark.asyncio
async def test_get_current_user_accepts_token_without_username_claims(
    jwt_access_token_factory,
):
    username_optional_token = jwt_access_token_factory(username=None)
    user = await auth_module.get_current_user(f'Bearer {username_optional_token}')

    assert user.user_id == build_test_jwt_subject('USER')
    assert user.username is None
    assert user.roles == ('USER',)


@pytest.mark.asyncio
async def test_get_current_user_rejects_missing_bearer_header():
    with pytest.raises(auth_module.UnauthorizedError) as exc_info:
        await auth_module.get_current_user(None)

    assert exc_info.value.code == 'AUTH_MISSING_BEARER_TOKEN'
    assert exc_info.value.message == 'Missing or invalid bearer token.'


@pytest.mark.asyncio
async def test_get_current_user_rejects_wrong_bearer_prefix(jwt_access_token: str):
    with pytest.raises(auth_module.UnauthorizedError) as exc_info:
        await auth_module.get_current_user(f'Token {jwt_access_token}')

    assert exc_info.value.code == 'AUTH_MISSING_BEARER_TOKEN'
    assert exc_info.value.message == 'Missing or invalid bearer token.'


@pytest.mark.asyncio
async def test_get_current_user_rejects_malformed_token():
    with pytest.raises(auth_module.UnauthorizedError) as exc_info:
        await auth_module.get_current_user('Bearer definitely-not-a-jwt')

    assert exc_info.value.code == 'AUTH_INVALID_TOKEN'
    assert exc_info.value.message == 'Access token is invalid.'


@pytest.mark.asyncio
async def test_get_current_user_rejects_invalid_signature(jwt_access_token_factory):
    forged_token = jwt_access_token_factory(secret=JWT_ALTERNATE_TEST_SECRET)
    with pytest.raises(auth_module.UnauthorizedError) as exc_info:
        await auth_module.get_current_user(f'Bearer {forged_token}')

    assert exc_info.value.code == 'AUTH_INVALID_TOKEN'
    assert exc_info.value.message == 'Access token is invalid.'


@pytest.mark.asyncio
async def test_get_current_user_rejects_expired_token(jwt_access_token_factory):
    expired_token = jwt_access_token_factory(
        expires_at=datetime(2026, 3, 18, 6, 4, 59, tzinfo=UTC),
    )
    with pytest.raises(auth_module.UnauthorizedError) as exc_info:
        await auth_module.get_current_user(f'Bearer {expired_token}')

    assert exc_info.value.code == 'AUTH_TOKEN_EXPIRED'
    assert exc_info.value.message == 'Access token has expired.'


@pytest.mark.asyncio
async def test_get_current_user_rejects_wrong_issuer(jwt_access_token_factory):
    wrong_issuer_token = jwt_access_token_factory(issuer='another-app')
    with pytest.raises(auth_module.UnauthorizedError) as exc_info:
        await auth_module.get_current_user(f'Bearer {wrong_issuer_token}')

    assert exc_info.value.code == 'AUTH_INVALID_TOKEN'
    assert exc_info.value.message == 'Access token is invalid.'


@pytest.mark.asyncio
async def test_get_current_user_rejects_wrong_audience(jwt_access_token_factory):
    wrong_audience_token = jwt_access_token_factory(audience=['another-platform'])
    with pytest.raises(auth_module.UnauthorizedError) as exc_info:
        await auth_module.get_current_user(f'Bearer {wrong_audience_token}')

    assert exc_info.value.code == 'AUTH_INVALID_TOKEN'
    assert exc_info.value.message == 'Access token is invalid.'


@pytest.mark.asyncio
async def test_get_current_user_rejects_refresh_token_as_access_token(
    jwt_refresh_token: str,
):
    with pytest.raises(auth_module.UnauthorizedError) as exc_info:
        await auth_module.get_current_user(f'Bearer {jwt_refresh_token}')

    assert exc_info.value.code == 'AUTH_INVALID_TOKEN'
    assert exc_info.value.message == 'Access token is invalid.'


@pytest.mark.asyncio
async def test_get_current_user_rejects_missing_subject(jwt_access_token_factory):
    missing_subject_token = jwt_access_token_factory(subject='')
    with pytest.raises(auth_module.UnauthorizedError) as exc_info:
        await auth_module.get_current_user(f'Bearer {missing_subject_token}')

    assert exc_info.value.code == 'AUTH_INVALID_TOKEN'
    assert exc_info.value.message == 'Access token is invalid.'


@pytest.mark.asyncio
@pytest.mark.parametrize('roles', [None, []], ids=['missing-roles', 'empty-roles'])
async def test_get_current_user_rejects_missing_or_empty_roles(
    jwt_access_token_factory, roles
):
    invalid_roles_token = jwt_access_token_factory(roles=roles)
    with pytest.raises(auth_module.UnauthorizedError) as exc_info:
        await auth_module.get_current_user(f'Bearer {invalid_roles_token}')

    assert exc_info.value.code == 'AUTH_INVALID_TOKEN'
    assert exc_info.value.message == 'Access token is invalid.'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'secret_value',
    [
        None,
        '',
    ],
    ids=['missing-config', 'empty-secret'],
)
async def test_get_current_user_rejects_missing_config(
    jwt_access_token_factory, monkeypatch, secret_value
):
    monkeypatch.delenv('STOCKAPP_JWT_SECRET', raising=False)
    monkeypatch.delenv('SLCN_JWT_SECRETKEY', raising=False)
    monkeypatch.delenv('jwt_secret', raising=False)
    if secret_value is not None:
        monkeypatch.setenv('STOCKAPP_JWT_SECRET', secret_value)

    settings_module.get_settings.cache_clear()
    token = jwt_access_token_factory()

    with pytest.raises(auth_module.UnauthorizedError) as exc_info:
        await auth_module.get_current_user(f'Bearer {token}')

    assert exc_info.value.code == 'AUTH_INVALID_TOKEN'
    assert exc_info.value.message == 'Access token is invalid.'


@pytest.mark.asyncio
async def test_get_current_user_rejects_invalid_algorithm_config(
    jwt_access_token_factory, monkeypatch
):
    monkeypatch.setenv('STOCKAPP_JWT_ALGORITHM', 'NOT-A-REAL-ALGO')
    settings_module.get_settings.cache_clear()
    token = jwt_access_token_factory()

    with pytest.raises(auth_module.UnauthorizedError) as exc_info:
        await auth_module.get_current_user(f'Bearer {token}')

    assert exc_info.value.code == 'AUTH_INVALID_TOKEN'
    assert exc_info.value.message == 'Access token is invalid.'
