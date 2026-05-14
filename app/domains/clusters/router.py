from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path

from app.api.deps import DbSession, UserDep
from app.core.response import ApiSuccess
from app.db.repositories.cluster_repo import ClusterRepository
from app.domains.clusters.assembler import assemble_cluster_detail_response
from app.domains.clusters.service import ClustersService
from app.schemas.cluster import ClusterDetailResponse

router = APIRouter()


def get_clusters_service(session: DbSession) -> ClustersService:
    return ClustersService(ClusterRepository(session))


ClustersServiceDep = Annotated[ClustersService, Depends(get_clusters_service)]


@router.get('/clusters/{clusterId}', response_model=ApiSuccess[ClusterDetailResponse])
async def get_cluster_detail(
    _: UserDep,
    service: ClustersServiceDep,
    clusterId: Annotated[UUID, Path(alias='clusterId')],
) -> ApiSuccess[ClusterDetailResponse]:
    payload = await service.get_cluster_detail(str(clusterId))
    return ApiSuccess(data=assemble_cluster_detail_response(payload))


__all__ = ['get_clusters_service', 'router']
