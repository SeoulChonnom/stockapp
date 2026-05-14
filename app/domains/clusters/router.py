from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path

from app.api.deps import UserDep
from app.core.exceptions import NotFoundError
from app.core.response import ApiSuccess
from app.domains.clusters.service import ClustersService, get_clusters_service
from app.schemas.cluster import ClusterDetailResponse

router = APIRouter()
ClustersServiceDep = Annotated[ClustersService, Depends(get_clusters_service)]


@router.get('/clusters/{clusterId}', response_model=ApiSuccess[ClusterDetailResponse])
async def get_cluster_detail(
    _: UserDep,
    service: ClustersServiceDep,
    clusterId: Annotated[UUID, Path(alias='clusterId')],
) -> ApiSuccess[ClusterDetailResponse]:
    payload = await service.get_cluster_detail(str(clusterId))
    if payload is None:
        raise NotFoundError(
            'CLUSTER_NOT_FOUND', '요청한 뉴스 클러스터를 찾을 수 없습니다.'
        )
    return ApiSuccess(data=payload)


__all__ = ['router']
