import logging
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.response import ApiError, ApiErrorDetail

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    status_code: int


class NotFoundError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code=code, message=message, status_code=404)


class ConflictError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code=code, message=message, status_code=409)


class UnauthorizedError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code=code, message=message, status_code=401)


class ForbiddenError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code=code, message=message, status_code=403)


class ValidationError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code=code, message=message, status_code=400)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        payload = ApiError(error=ApiErrorDetail(code=exc.code, message=exc.message))
        return JSONResponse(
            status_code=exc.status_code, content=payload.model_dump(mode='json')
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        payload = ApiError(
            error=ApiErrorDetail(
                code='REQUEST_VALIDATION_ERROR',
                message=str(exc),
            )
        )
        return JSONResponse(status_code=422, content=payload.model_dump(mode='json'))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception('Unhandled exception while processing request.', exc_info=exc)
        payload = ApiError(
            error=ApiErrorDetail(
                code='INTERNAL_SERVER_ERROR',
                message='Internal server error',
            )
        )
        return JSONResponse(status_code=500, content=payload.model_dump(mode='json'))


__all__ = [
    'AppError',
    'ConflictError',
    'ForbiddenError',
    'NotFoundError',
    'UnauthorizedError',
    'ValidationError',
    'register_exception_handlers',
]
