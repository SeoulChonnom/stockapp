import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

import jwt  # pyright: ignore[reportMissingImports]
from fastapi import Depends, Header  # pyright: ignore[reportMissingImports]
from jwt import (  # pyright: ignore[reportMissingImports]
    ExpiredSignatureError,
    InvalidTokenError,
)

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.settings import Settings, get_settings


@dataclass(slots=True)
class CurrentUser:
    user_id: str = ''
    token: str = ''
    username: str | None = None
    roles: tuple[str, ...] = ()


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    token = _extract_bearer_token(authorization)
    if token is None:
        raise UnauthorizedError(
            'AUTH_MISSING_BEARER_TOKEN',
            'Missing or invalid bearer token.',
        )

    claims = _decode_access_token(token, get_settings())
    return CurrentUser(
        user_id=_extract_subject(claims),
        token=token,
        username=_extract_username(claims),
        roles=_extract_roles(claims),
    )


def require_roles(*required_roles: str) -> Callable[..., Any]:
    normalized_required_roles = tuple(
        role.strip().upper() for role in required_roles if role.strip()
    )

    async def require_current_user(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        current_user_roles = _get_current_user_roles(current_user)
        if any(role in current_user_roles for role in normalized_required_roles):
            return current_user

        raise ForbiddenError(
            'AUTH_FORBIDDEN',
            'You do not have permission to access this resource.',
        )

    return require_current_user


def _get_current_user_roles(
    current_user: CurrentUser,
) -> tuple[str, ...]:
    return current_user.roles


def _extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None

    scheme, _, token = authorization.partition(' ')
    if scheme != 'Bearer':
        return None

    token = token.strip()
    return token or None


def _decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    secret = _get_jwt_secret(settings)

    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_access_audiences,
            leeway=0,
            options={'require': ['exp', 'iss', 'sub', 'aud']},
        )
    except ExpiredSignatureError as exc:
        raise UnauthorizedError(
            'AUTH_TOKEN_EXPIRED', 'Access token has expired.'
        ) from exc
    except InvalidTokenError as exc:
        raise UnauthorizedError(
            'AUTH_INVALID_TOKEN', 'Access token is invalid.'
        ) from exc

    token_type = claims.get('token_type')
    if token_type != 'access':
        raise UnauthorizedError('AUTH_INVALID_TOKEN', 'Access token is invalid.')

    return claims


def _get_jwt_secret(settings: Settings) -> bytes:
    secret = settings.jwt_secret
    if isinstance(secret, str):
        stripped_secret = secret.strip()
        if stripped_secret:
            try:
                return _decode_base64url_secret(stripped_secret)
            except ValueError as exc:
                raise UnauthorizedError(
                    'AUTH_INVALID_TOKEN', 'Access token is invalid.'
                ) from exc
    raise UnauthorizedError('AUTH_INVALID_TOKEN', 'Access token is invalid.')


def _decode_base64url_secret(secret: str) -> bytes:
    padding = '=' * (-len(secret) % 4)
    try:
        decoded_secret = base64.urlsafe_b64decode(f'{secret}{padding}')
    except (ValueError, binascii.Error) as exc:
        raise ValueError('Invalid base64url JWT secret.') from exc

    if not decoded_secret:
        raise ValueError('Invalid base64url JWT secret.')
    return decoded_secret


def _extract_subject(claims: dict[str, Any]) -> str:
    subject = claims.get('sub')
    if isinstance(subject, str) and subject.strip():
        return subject
    raise UnauthorizedError('AUTH_INVALID_TOKEN', 'Access token is invalid.')


def _extract_username(claims: dict[str, Any]) -> str | None:
    for claim_name in ('username', 'userName'):
        username = claims.get(claim_name)
        if isinstance(username, str) and username.strip():
            return username.strip()
    return None


def _extract_roles(claims: dict[str, Any]) -> tuple[str, ...]:
    raw_roles = claims.get('roles')
    if isinstance(raw_roles, str):
        return _normalize_roles([raw_roles])
    if isinstance(raw_roles, list):
        return _normalize_roles(raw_roles)
    raise UnauthorizedError('AUTH_INVALID_TOKEN', 'Access token is invalid.')


def _normalize_roles(raw_roles: list[Any]) -> tuple[str, ...]:
    normalized_roles: list[str] = []
    for raw_role in raw_roles:
        if not isinstance(raw_role, str):
            raise UnauthorizedError('AUTH_INVALID_TOKEN', 'Access token is invalid.')

        normalized_role = raw_role.strip().upper()
        if not normalized_role:
            raise UnauthorizedError('AUTH_INVALID_TOKEN', 'Access token is invalid.')
        normalized_roles.append(normalized_role)

    if not normalized_roles:
        raise UnauthorizedError('AUTH_INVALID_TOKEN', 'Access token is invalid.')
    return tuple(normalized_roles)
