from __future__ import annotations

from tests.support import jsonable, load_module

pages_assembler_module = load_module('app.domains.pages.assembler')

assemble_daily_page_response = pages_assembler_module.assemble_daily_page_response


def test_daily_page_assembler_keeps_market_nested_article_links(
    sample_daily_page_payload,
):
    response = jsonable(assemble_daily_page_response(sample_daily_page_payload))

    assert response['pageId'] == 501
    assert response['markets'][0]['marketType'] == 'US'
    assert response['markets'][1]['marketType'] == 'KR'
    assert (
        response['markets'][0]['topClusters'][0]['representativeArticle'][
            'publisherName'
        ]
        == '매일경제'
    )
    assert (
        response['markets'][0]['articleLinks'][0]['clusterId']
        == '51f0d9a0-9fc5-4f15-a4f9-62856f128683'
    )
    assert len(response['markets'][0]['articleLinks']) == 2
    assert response['markets'][0]['articleLinks'][1]['publisherName'] == '연합뉴스'
    assert 'articleLinks' not in response


def test_daily_page_assembler_preserves_display_order(sample_daily_page_payload):
    response = jsonable(assemble_daily_page_response(sample_daily_page_payload))

    assert [market['marketType'] for market in response['markets']] == ['US', 'KR']
    assert response['markets'][1]['indices'][0]['indexCode'] == 'KS11'
    assert (
        response['markets'][1]['topClusters'][0]['title'] == '반도체와 자동차 동반 강세'
    )
