import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.batch.background import get_in_process_batch_scheduler
from app.batch.logging import configure_batch_logging, log_safe_exception
from app.core.exceptions import register_exception_handlers
from app.core.request_context import register_request_context_middleware
from app.core.settings import get_settings
from app.db.migration_runner import run_startup_migrations_async

BATCH_RUNTIME_LOGGER = logging.getLogger('app.batch.runtime')
DATABASE_MIGRATION_LOGGER = logging.getLogger('app.db.migrations')


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_batch_logging()
    settings = get_settings()
    try:
        await run_startup_migrations_async(settings)
    except Exception as exc:
        log_safe_exception(
            DATABASE_MIGRATION_LOGGER,
            logging.ERROR,
            'Database migration failed during startup.',
            exception=exc,
        )
        raise RuntimeError('Database migration failed during startup.') from None

    scheduler = get_in_process_batch_scheduler()
    BATCH_RUNTIME_LOGGER.info(
        'batch_runtime event=startup startup_recovery_enabled=%s',
        settings.batch_startup_recovery_enabled,
    )
    if settings.batch_startup_recovery_enabled:
        try:
            scheduler.start_drain()
        except Exception as exc:
            log_safe_exception(
                BATCH_RUNTIME_LOGGER,
                logging.ERROR,
                'Unable to start batch startup recovery.',
                exception=exc,
            )
    try:
        yield
    finally:
        await scheduler.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    settings.validate_for_app_startup()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )
    if settings.is_development and settings.cors_allowed_origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins_list,
            allow_credentials=True,
            allow_methods=['*'],
            allow_headers=['*'],
        )
    register_request_context_middleware(app)
    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
