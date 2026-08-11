from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.core.openapi_responses import BASE_RESPONSES, error_response, merge_responses
from app.core.response import ApiError, ApiErrorDetail
from app.db.session import get_db_session

router = APIRouter(tags=['health'])

type DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]

_HEALTH_RESPONSES = merge_responses(
    BASE_RESPONSES,
    error_response(503, 'Database is unavailable. Codes: HEALTH_DATABASE_UNAVAILABLE.'),
)


@router.get('/health', response_model=None, responses=_HEALTH_RESPONSES)
async def health(db: DbSessionDep) -> dict[str, str] | JSONResponse:
    try:
        await db.execute(text('SELECT 1'))
    except SQLAlchemyError:
        payload = ApiError(
            error=ApiErrorDetail(
                code='HEALTH_DATABASE_UNAVAILABLE',
                message='Database is unavailable.',
            )
        )
        return JSONResponse(status_code=503, content=payload.model_dump(mode='json'))
    return {'status': 'ok'}
