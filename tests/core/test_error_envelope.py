"""Serialization contract for the ``ApiError`` envelope.

``ApiErrorDetail.details`` is optional. The guarantee these tests protect is
that it is *absent from the JSON body* when unset -- not serialized as
``"details": null`` -- so every error response that existed before the field
was added stays byte-identical.
"""

from __future__ import annotations

import pytest

from tests.support import load_module

pytest.importorskip('fastapi')
from fastapi import FastAPI
from fastapi.testclient import TestClient

exceptions_module = load_module('app.core.exceptions')
response_module = load_module('app.core.response')

ApiErrorDetail = response_module.ApiErrorDetail
build_error_body = response_module.build_error_body
ConflictError = exceptions_module.ConflictError
NotFoundError = exceptions_module.NotFoundError


@pytest.fixture
def error_client() -> TestClient:
    app = FastAPI()
    exceptions_module.register_exception_handlers(app)

    @app.get('/plain-conflict')
    async def plain_conflict() -> None:
        raise ConflictError('SOMETHING_CONFLICTED', '충돌이 발생했습니다.')

    @app.get('/detailed-conflict')
    async def detailed_conflict() -> None:
        raise ConflictError(
            'BATCH_ALREADY_RUNNING',
            '동일 날짜의 배치가 이미 실행 중입니다.',
            {'jobId': 4242},
        )

    @app.get('/not-found')
    async def not_found() -> None:
        raise NotFoundError('THING_NOT_FOUND', '없습니다.')

    return TestClient(app, raise_server_exceptions=False)


def test_error_without_details_omits_the_key_entirely(error_client: TestClient):
    """No-regression proof: the body must not gain a "details": null key."""
    response = error_client.get('/plain-conflict')

    assert response.status_code == 409
    payload = response.json()
    assert payload['error'] == {
        'code': 'SOMETHING_CONFLICTED',
        'message': '충돌이 발생했습니다.',
    }
    assert 'details' not in payload['error']
    # Guard the raw bytes too, not just the parsed dict.
    assert 'details' not in response.text


def test_unrelated_error_type_also_omits_details(error_client: TestClient):
    response = error_client.get('/not-found')

    assert response.status_code == 404
    payload = response.json()
    assert payload['error'] == {
        'code': 'THING_NOT_FOUND',
        'message': '없습니다.',
    }
    assert 'details' not in response.text


def test_error_with_details_carries_them(error_client: TestClient):
    response = error_client.get('/detailed-conflict')

    assert response.status_code == 409
    payload = response.json()
    assert payload['success'] is False
    assert payload['error'] == {
        'code': 'BATCH_ALREADY_RUNNING',
        'message': '동일 날짜의 배치가 이미 실행 중입니다.',
        'details': {'jobId': 4242},
    }
    assert payload['meta']['requestId']


def test_validation_error_handler_omits_details(error_client: TestClient):
    """The 422 handler builds its own detail object; it must not leak nulls."""
    app = FastAPI()
    exceptions_module.register_exception_handlers(app)

    @app.get('/needs-query')
    async def needs_query(required: int) -> None:  # noqa: ARG001
        return None

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get('/needs-query')

    assert response.status_code == 422
    assert response.json()['error']['code'] == 'REQUEST_VALIDATION_ERROR'
    assert 'details' not in response.json()['error']


def test_validation_error_handler_does_not_echo_rejected_sensitive_input():
    app = FastAPI()
    exceptions_module.register_exception_handlers(app)

    @app.get('/needs-integer')
    async def needs_integer(value: int) -> None:  # noqa: ARG001
        return None

    sensitive_marker = 'secret-token-123'
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get('/needs-integer', params={'value': sensitive_marker})

    assert response.status_code == 422
    payload = response.json()
    assert payload['error'] == {
        'code': 'REQUEST_VALIDATION_ERROR',
        'message': 'Request validation failed.',
    }
    assert sensitive_marker not in response.text


def test_build_error_body_drops_none_details_directly():
    body = build_error_body(ApiErrorDetail(code='X', message='y'))

    assert body['error'] == {'code': 'X', 'message': 'y'}
    assert body['success'] is False


def test_build_error_body_keeps_supplied_details():
    body = build_error_body(ApiErrorDetail(code='X', message='y', details={'jobId': 1}))

    assert body['error']['details'] == {'jobId': 1}


@pytest.mark.parametrize(
    ('error_class_name', 'expected_status'),
    [
        ('NotFoundError', 404),
        ('ConflictError', 409),
        ('UnauthorizedError', 401),
        ('ForbiddenError', 403),
        ('ValidationError', 400),
    ],
)
def test_subclasses_still_construct_positionally(
    error_class_name: str, expected_status: int
):
    """Adding the slots dataclass field must not disturb the two-positional-arg
    signature every existing call site relies on."""
    error_class = getattr(exceptions_module, error_class_name)

    error = error_class('SOME_CODE', 'some message')

    assert error.code == 'SOME_CODE'
    assert error.message == 'some message'
    assert error.status_code == expected_status
    assert error.details is None


def test_conflict_error_accepts_positional_details():
    error = ConflictError('C', 'm', {'jobId': 5})

    assert error.details == {'jobId': 5}
