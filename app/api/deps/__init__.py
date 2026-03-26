from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentUser, get_current_user
from app.api.deps.db import get_db_session

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
UserDep = Annotated[CurrentUser, Depends(get_current_user)]

__all__ = ["CurrentUser", "DbSession", "UserDep", "get_current_user", "get_db_session"]
