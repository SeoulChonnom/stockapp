from fastapi import APIRouter

from app.domains.archive.router import router as archive_router
from app.domains.clusters.router import router as clusters_router
from app.domains.pages.router import router as pages_router

api_router = APIRouter(prefix="/stock/api")
api_router.include_router(archive_router, prefix="/pages", tags=["archive"])
api_router.include_router(pages_router, prefix="/pages", tags=["pages"])
api_router.include_router(clusters_router, prefix="/news", tags=["news"])
