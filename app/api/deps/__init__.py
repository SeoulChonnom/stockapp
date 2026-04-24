from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentUser, get_current_user, require_roles
from app.api.deps.db import get_db_session

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
UserDep = Annotated[CurrentUser, Depends(require_roles("USER", "ADMIN"))]
AdminDep = Annotated[CurrentUser, Depends(require_roles("ADMIN"))]

__all__ = [
    "AdminDep",
    "CurrentUser",
    "DbSession",
    "UserDep",
    "get_current_user",
    "get_db_session",
    "require_roles",
]
