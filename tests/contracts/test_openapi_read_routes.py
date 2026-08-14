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
    assert '/stock/api/pages/archive/themes' in paths
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
        ('/stock/api/pages/archive/themes', 'get'),
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


def test_openapi_documents_archive_theme_catalog_and_error_envelopes():
    schema = app_module.app.openapi()
    operation = schema['paths']['/stock/api/pages/archive/themes']['get']

    assert operation['responses']['200']['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/ApiSuccess_list_ThemeNodeResponse__'
    }
    theme_node = schema['components']['schemas']['ThemeNodeResponse']
    assert theme_node['required'] == ['code', 'label', 'description', 'children']
    assert theme_node['additionalProperties'] is False
    assert theme_node['properties']['children'] == {
        'items': {'$ref': '#/components/schemas/ThemeNodeResponse'},
        'type': 'array',
        'title': 'Children',
    }

    for status_code in ('401', '403', '500'):
        assert operation['responses'][status_code]['content']['application/json'][
            'schema'
        ] == {'$ref': '#/components/schemas/ApiError'}


def test_openapi_documents_correlated_archive_filter_contract():
    schema = app_module.app.openapi()
    operation = schema['paths']['/stock/api/pages/archive']['get']
    parameters = {
        parameter['name']: parameter
        for parameter in operation['parameters']
        if parameter['in'] == 'query'
    }

    theme = parameters['theme']
    assert theme['schema']['anyOf'][0] == {
        'type': 'array',
        'items': {'type': 'string'},
        'maxItems': 100,
    }
    assert 'trimmed and deduplicated' in theme['description']
    assert 'at most 10 distinct codes' in theme['description']

    market_type = parameters['marketType']['schema']['anyOf'][0]
    assert market_type == {'enum': ['US', 'KR'], 'type': 'string'}

    query = parameters['q']
    assert query['schema']['anyOf'][0] == {'type': 'string', 'maxLength': 1_000}
    assert 'NFC/casefold/collapsed whitespace' in query['description']
    assert '2–100 characters' in query['description']
    assert 'at most 10 tokens' in query['description']

    assert operation['responses']['422']['description'] == (
        'Invalid archive filters. Codes: REQUEST_VALIDATION_ERROR, INVALID_THEME.'
    )
    assert operation['responses']['422']['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/ApiError'
    }


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
    assert schema['components']['schemas']['DirectionKeyPointResponse']['required'] == [
        'kind',
        'label',
        'text',
        'direction',
    ]
    assert (
        schema['components']['schemas']['DirectionKeyPointResponse']['properties'][
            'kind'
        ]['const']
        == 'direction'
    )
    assert (
        schema['components']['schemas']['DirectionKeyPointResponse']['properties'][
            'label'
        ]['const']
        == '시장 방향'
    )
    assert schema['components']['schemas']['DriverKeyPointResponse']['required'] == [
        'kind',
        'label',
        'text',
    ]
    assert (
        schema['components']['schemas']['DriverKeyPointResponse']['properties']['kind'][
            'const'
        ]
        == 'driver'
    )
    assert (
        schema['components']['schemas']['DriverKeyPointResponse']['properties'][
            'label'
        ]['const']
        == '주요 원인'
    )
    assert schema['components']['schemas']['WatchKeyPointResponse']['required'] == [
        'kind',
        'label',
        'text',
    ]
    assert (
        schema['components']['schemas']['WatchKeyPointResponse']['properties']['kind'][
            'const'
        ]
        == 'watch'
    )
    assert (
        schema['components']['schemas']['WatchKeyPointResponse']['properties']['label'][
            'const'
        ]
        == '관전 포인트'
    )
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
    assert summary['additionalProperties'] is False
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
        'oneOf': [
            {'$ref': '#/components/schemas/BackgroundAnalysisSectionResponse'},
            {'$ref': '#/components/schemas/ImpactAnalysisSectionResponse'},
            {'$ref': '#/components/schemas/RelatedAnalysisSectionResponse'},
            {'$ref': '#/components/schemas/OutlookAnalysisSectionResponse'},
        ],
        'discriminator': {
            'propertyName': 'kind',
            'mapping': {
                'background': '#/components/schemas/BackgroundAnalysisSectionResponse',
                'impact': '#/components/schemas/ImpactAnalysisSectionResponse',
                'related': '#/components/schemas/RelatedAnalysisSectionResponse',
                'outlook': '#/components/schemas/OutlookAnalysisSectionResponse',
            },
        },
    }

    for name, kind, title in (
        ('BackgroundAnalysisSectionResponse', 'background', '발생 배경'),
        ('ImpactAnalysisSectionResponse', 'impact', '시장 영향'),
        ('RelatedAnalysisSectionResponse', 'related', '관련 업종·종목'),
        ('OutlookAnalysisSectionResponse', 'outlook', '향후 관전 포인트'),
    ):
        section = schema['components']['schemas'][name]
        assert section['required'] == ['kind', 'title', 'paragraphs']
        assert section['additionalProperties'] is False
        assert section['properties']['kind']['const'] == kind
        assert section['properties']['title']['const'] == title
        assert section['properties']['paragraphs']['minItems'] == 1
        assert section['properties']['paragraphs']['items'] == {
            '$ref': '#/components/schemas/AnalysisParagraphResponse'
        }

    paragraph = schema['components']['schemas']['AnalysisParagraphResponse']
    assert paragraph['required'] == ['sentences']
    assert paragraph['additionalProperties'] is False
    assert paragraph['properties']['sentences']['minItems'] == 1
    assert paragraph['properties']['sentences']['items'] == {
        '$ref': '#/components/schemas/AnalysisSentenceResponse'
    }
    sentence = schema['components']['schemas']['AnalysisSentenceResponse']
    assert sentence['required'] == [
        'text',
        'sourceArticleIds',
        'conflictStatus',
        'conflictingSourceArticleIds',
        'conflictNote',
    ]
    assert sentence['additionalProperties'] is False
    assert sentence['properties']['text']['minLength'] == 1
    assert sentence['properties']['sourceArticleIds']['items'] == {'type': 'integer'}
    assert sentence['properties']['conflictingSourceArticleIds']['items'] == {
        'type': 'integer'
    }
    assert sentence['properties']['conflictStatus']['enum'] == [
        'NOT_CHECKED',
        'NONE',
        'FOUND',
    ]

    issue = schema['components']['schemas']['AnalysisIssueResponse']
    assert issue['required'] == ['code', 'message']
    assert issue['additionalProperties'] is False
    assert issue['properties']['code']['enum'] == [
        'ANALYSIS_GENERATION_FAILED',
        'NO_GROUNDED_SENTENCES',
        'INVALID_SOURCE_REFERENCE',
        'CONFLICT_CHECK_FAILED',
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
