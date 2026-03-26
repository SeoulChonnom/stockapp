from dataclasses import dataclass
from typing import Annotated

from fastapi import Header, HTTPException, status


@dataclass(slots=True)
class CurrentUser:
    user_id: str
    token: str

async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token.",
        )
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token.",
        )
    return CurrentUser(user_id="stub-user", token=token)
