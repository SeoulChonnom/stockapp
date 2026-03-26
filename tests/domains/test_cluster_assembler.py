from __future__ import annotations

from tests.support import jsonable, load_module

clusters_assembler_module = load_module("app.domains.clusters.assembler")

assemble_cluster_detail_response = clusters_assembler_module.assemble_cluster_detail_response


def test_cluster_assembler_returns_full_detail_contract(sample_cluster_detail_payload):
    response = jsonable(assemble_cluster_detail_response(sample_cluster_detail_payload))

    assert response["clusterId"] == sample_cluster_detail_payload["clusterId"]
    assert response["marketType"] == "US"
    assert response["summary"]["short"] == "반도체 업종 강세가 나스닥 상승을 견인했다."
    assert response["representativeArticle"]["publisherName"] == "매일경제"
    assert response["articles"][0]["title"] == "엔비디아 급등에 반도체 강세"
    assert response["articles"][-1]["title"] == "대형 기술주 재평가로 나스닥 반등"
