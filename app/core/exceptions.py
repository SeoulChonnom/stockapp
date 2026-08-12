import logging
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.public_diagnostics import sanitize_public_diagnostic
from app.core.response import ApiErrorDetail, build_error_body

logger = logging.getLogger(__name__)
_REQUEST_VALIDATION_ERROR_MESSAGE = 'Request validation failed.'


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    status_code: int
    details: dict[str, Any] | None = None
    """Optional structured context surfaced as ``error.details``.

    Trailing and defaulted so that every existing subclass and call site
    keeps constructing positionally exactly as before. ``None`` (not ``{}``)
    is the default because a dataclass field cannot take a mutable default,
    and because ``None`` is what tells the serializer to drop the key.
    """


class NotFoundError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code=code, message=message, status_code=404)


class ConflictError(AppError):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            status_code=409,
            details=details,
        )


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
        return JSONResponse(
            status_code=exc.status_code,
            content=build_error_body(
                ApiErrorDetail(
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                )
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        public_message = (
            sanitize_public_diagnostic(_REQUEST_VALIDATION_ERROR_MESSAGE)
            or _REQUEST_VALIDATION_ERROR_MESSAGE
        )
        return JSONResponse(
            status_code=422,
            content=build_error_body(
                ApiErrorDetail(
                    code='REQUEST_VALIDATION_ERROR',
                    message=public_message,
                )
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception('Unhandled exception while processing request.', exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=build_error_body(
                ApiErrorDetail(
                    code='INTERNAL_SERVER_ERROR',
                    message='Internal server error',
                )
            ),
        )


__all__ = [
    'AppError',
    'ConflictError',
    'ForbiddenError',
    'NotFoundError',
    'UnauthorizedError',
    'ValidationError',
    'register_exception_handlers',
]
