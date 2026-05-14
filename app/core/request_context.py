from contextvars import ContextVar
from uuid import uuid4

from fastapi import FastAPI, Request

from app.core.settings import get_settings

request_id_context: ContextVar[str] = ContextVar('request_id', default='')


def get_request_id() -> str:
    request_id = request_id_context.get()
    if request_id:
        return request_id
    return f'req-{uuid4()}'


def register_request_context_middleware(app: FastAPI) -> None:
    settings = get_settings()

    @app.middleware('http')
    async def request_context_middleware(request: Request, call_next):  # type: ignore[override]
        incoming_request_id = request.headers.get(settings.request_id_header)
        request_id = incoming_request_id or f'req-{uuid4()}'
        token = request_id_context.set(request_id)
        try:
            response = await call_next(request)
            response.headers[settings.request_id_header] = request_id
            return response
        finally:
            request_id_context.reset(token)
