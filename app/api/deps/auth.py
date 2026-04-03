from dataclasses import dataclass
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.core.settings import get_settings


@dataclass(slots=True)
class CurrentUser:
    user_id: str
    token: str


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    token = _extract_bearer_token(authorization)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token.",
        )

    settings = get_settings()
    if settings.is_development and token != settings.auth_stub_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token.",
        )

    return CurrentUser(user_id="stub-user", token=token)


def _extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme != "Bearer":
        return None

    token = token.strip()
    return token or None
