from __future__ import annotations

import pytest

pytest.importorskip('fastapi')

from tests.support import load_module

app_module = load_module('app.main')


def test_openapi_includes_read_routes():
    app = app_module.app
    schema = app.openapi()
    paths = set(schema['paths'].keys())

    assert '/stock/api/pages/daily/latest' in paths
    assert '/stock/api/pages/daily' in paths
    assert '/stock/api/pages/navigation' in paths
    assert '/stock/api/pages/archive' in paths
    assert '/stock/api/pages/{pageId}' in paths
    assert '/stock/api/news/clusters/{clusterId}' in paths
    assert '/stock/api/batch/market-daily' in paths
    assert '/stock/api/batch/jobs' in paths
    assert '/stock/api/batch/jobs/{jobId}' in paths
    assert '/stock/api/archive' not in paths


def test_openapi_read_route_methods_are_stable():
    app = app_module.app
    schema = app.openapi()

    expected_methods = {
        ('/stock/api/pages/daily/latest', 'get'),
        ('/stock/api/pages/daily', 'get'),
        ('/stock/api/pages/navigation', 'get'),
        ('/stock/api/pages/archive', 'get'),
        ('/stock/api/pages/{pageId}', 'get'),
        ('/stock/api/news/clusters/{clusterId}', 'get'),
        ('/stock/api/batch/market-daily', 'post'),
        ('/stock/api/batch/jobs', 'get'),
        ('/stock/api/batch/jobs/{jobId}', 'get'),
    }

    actual_methods = {
        (path, method)
        for path, methods in schema['paths'].items()
        for method in methods
        if path.startswith('/stock/api/')
    }

    assert expected_methods <= actual_methods
    assert ('/stock/api/archive', 'get') not in actual_methods


def test_openapi_documents_public_page_navigation_contract():
    schema = app_module.app.openapi()
    operation = schema['paths']['/stock/api/pages/navigation']['get']
    parameters = {
        parameter['name']: parameter
        for parameter in operation['parameters']
        if parameter['in'] == 'query'
    }

    assert parameters['businessDate'] == {
        'name': 'businessDate',
        'in': 'query',
        'required': True,
        'schema': {
            'type': 'string',
            'format': 'date',
            'title': 'Businessdate',
        },
    }

    response_schema = operation['responses']['200']['content']['application/json'][
        'schema'
    ]
    assert response_schema == {
        '$ref': '#/components/schemas/ApiSuccess_PageDateNavigationResponse_'
    }
    response_envelope = schema['components']['schemas'][
        'ApiSuccess_PageDateNavigationResponse_'
    ]
    assert response_envelope['properties']['data'] == {
        '$ref': '#/components/schemas/PageDateNavigationResponse'
    }

    response_schema = schema['components']['schemas']['PageDateNavigationResponse']
    assert response_schema['required'] == [
        'businessDate',
        'pageExists',
        'previousBusinessDate',
        'nextBusinessDate',
    ]
    assert response_schema['properties']['businessDate']['format'] == 'date'
    assert response_schema['properties']['previousBusinessDate']['anyOf'] == [
        {'type': 'string', 'format': 'date'},
        {'type': 'null'},
    ]
    assert response_schema['properties']['nextBusinessDate']['anyOf'] == [
        {'type': 'string', 'format': 'date'},
        {'type': 'null'},
    ]


def test_openapi_documents_navigation_validation_with_runtime_error_envelope():
    schema = app_module.app.openapi()
    validation_response = schema['paths']['/stock/api/pages/navigation']['get'][
        'responses'
    ]['422']

    assert 'REQUEST_VALIDATION_ERROR' in validation_response['description']
    assert validation_response['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/ApiError'
    }


def test_openapi_limits_archive_status_to_public_page_statuses():
    schema = app_module.app.openapi()
    operation = schema['paths']['/stock/api/pages/archive']['get']
    status_parameter = next(
        parameter
        for parameter in operation['parameters']
        if parameter['in'] == 'query' and parameter['name'] == 'status'
    )

    assert status_parameter['required'] is False
    assert status_parameter['schema']['anyOf'] == [
        {'$ref': '#/components/schemas/ArchiveStatus'},
        {'type': 'null'},
    ]
    assert schema['components']['schemas']['ArchiveStatus'] == {
        'type': 'string',
        'enum': ['READY', 'PARTIAL'],
    }


def _response_component(schema: dict, path: str, component_name: str) -> dict:
    operation = schema['paths'][path]['get']
    response_schema = operation['responses']['200']['content']['application/json'][
        'schema'
    ]
    assert response_schema == {
        '$ref': f'#/components/schemas/ApiSuccess_{component_name}_'
    }
    envelope = schema['components']['schemas'][f'ApiSuccess_{component_name}_']
    assert envelope['properties']['data'] == {
        '$ref': f'#/components/schemas/{component_name}'
    }
    return schema['components']['schemas'][component_name]


def test_openapi_links_daily_read_responses_to_the_b1_contract() -> None:
    schema = app_module.app.openapi()

    for path in (
        '/stock/api/pages/daily/latest',
        '/stock/api/pages/daily',
        '/stock/api/pages/{pageId}',
    ):
        _response_component(schema, path, 'DailyPageResponse')
    daily = schema['components']['schemas']['DailyPageResponse']
    assert daily['required'] == [
        'pageId',
        'businessDate',
        'versionNo',
        'pageTitle',
        'status',
        'generatedAt',
        'issues',
        'keyPoints',
        'markets',
        'metadata',
        'navigation',
        'versions',
    ]

    key_points = daily['properties']['keyPoints']['items']
    assert key_points['discriminator'] == {
        'propertyName': 'kind',
        'mapping': {
            'direction': '#/components/schemas/DirectionKeyPointResponse',
            'driver': '#/components/schemas/DriverKeyPointResponse',
            'watch': '#/components/schemas/WatchKeyPointResponse',
        },
    }
    assert [item['$ref'] for item in key_points['oneOf']] == [
        '#/components/schemas/DirectionKeyPointResponse',
        '#/components/schemas/DriverKeyPointResponse',
        '#/components/schemas/WatchKeyPointResponse',
    ]
    assert schema['components']['schemas']['DirectionKeyPointResponse']['properties'][
        'direction'
    ]['enum'] == ['UP', 'DOWN', 'MIXED', 'FLAT']
    for name in (
        'DirectionKeyPointResponse',
        'DriverKeyPointResponse',
        'WatchKeyPointResponse',
    ):
        assert schema['components']['schemas'][name]['additionalProperties'] is False
    assert (
        'direction'
        not in schema['components']['schemas']['DriverKeyPointResponse']['properties']
    )
    assert (
        'direction'
        not in schema['components']['schemas']['WatchKeyPointResponse']['properties']
    )

    market = schema['components']['schemas']['MarketSectionResponse']
    assert market['properties']['articleLinks']['items'] == {
        '$ref': '#/components/schemas/ArticleLinkResponse'
    }
    article_link = schema['components']['schemas']['ArticleLinkResponse']
    assert 'processedArticleId' in article_link['required']
    assert article_link['properties']['processedArticleId'] == {
        'type': 'integer',
        'title': 'Processedarticleid',
    }


def test_openapi_links_cluster_read_response_to_the_b2_and_grouping_contracts() -> None:
    schema = app_module.app.openapi()

    cluster = _response_component(
        schema,
        '/stock/api/news/clusters/{clusterId}',
        'ClusterDetailResponse',
    )
    assert cluster['required'] == [
        'clusterId',
        'businessDate',
        'marketType',
        'marketLabel',
        'title',
        'tags',
        'summary',
        'representativeArticle',
        'articles',
        'articleGrouping',
        'lastUpdatedAt',
        'articleCount',
    ]
    assert cluster['properties']['summary'] == {
        '$ref': '#/components/schemas/ClusterSummaryResponse'
    }
    assert cluster['properties']['articleGrouping'] == {
        '$ref': '#/components/schemas/ArticleGroupingResponse'
    }

    summary = schema['components']['schemas']['ClusterSummaryResponse']
    assert summary['required'] == [
        'analysisStatus',
        'analysisGeneratedAt',
        'analysisIssues',
        'conflictStatus',
        'sections',
    ]
    assert 'analysis' not in summary['properties']
    assert summary['properties']['analysisStatus']['enum'] == [
        'READY',
        'PARTIAL',
        'UNAVAILABLE',
    ]
    assert summary['properties']['conflictStatus']['enum'] == [
        'NOT_CHECKED',
        'NONE',
        'FOUND',
    ]
    assert summary['properties']['sections']['items'] == {
        '$ref': '#/components/schemas/AnalysisSectionResponse'
    }

    section = schema['components']['schemas']['AnalysisSectionResponse']
    assert section['properties']['kind']['enum'] == [
        'background',
        'impact',
        'related',
        'outlook',
    ]
    assert section['properties']['paragraphs']['items'] == {
        '$ref': '#/components/schemas/AnalysisParagraphResponse'
    }
    paragraph = schema['components']['schemas']['AnalysisParagraphResponse']
    assert paragraph['properties']['sentences']['items'] == {
        '$ref': '#/components/schemas/AnalysisSentenceResponse'
    }
    sentence = schema['components']['schemas']['AnalysisSentenceResponse']
    assert sentence['properties']['sourceArticleIds']['items'] == {'type': 'integer'}
    assert sentence['properties']['conflictingSourceArticleIds']['items'] == {
        'type': 'integer'
    }
    assert sentence['properties']['conflictStatus']['enum'] == [
        'NOT_CHECKED',
        'NONE',
        'FOUND',
    ]

    article = schema['components']['schemas']['ClusterArticleResponse']
    assert 'processedArticleId' in article['required']
    assert article['properties']['processedArticleId'] == {
        'type': 'integer',
        'title': 'Processedarticleid',
    }

    grouping = schema['components']['schemas']['ArticleGroupingResponse']
    assert grouping['required'] == ['status', 'generatedAt', 'issue']
    assert grouping['properties']['status']['enum'] == ['READY', 'UNAVAILABLE']
    assert grouping['properties']['issue']['anyOf'][0] == {
        '$ref': '#/components/schemas/ArticleGroupingIssueResponse'
    }
    grouping_issue = schema['components']['schemas']['ArticleGroupingIssueResponse']
    assert grouping_issue['properties']['code']['const'] == 'SIMILARITY_GROUPING_FAILED'
    assert grouping_issue['properties']['message']['const'] == (
        '유사 기사 묶음을 생성하지 못했습니다.'
    )
