from copy import deepcopy

from tests.support import jsonable, load_module

assembler_module = load_module('app.domains.batches.assembler')


def test_batch_detail_assembler_redacts_legacy_provider_diagnostics(
    sample_batch_job_detail_payload,
) -> None:
    payload = deepcopy(sample_batch_job_detail_payload)
    raw = (
        'AI summary fallback for GLOBAL_HEADLINE: 429 RESOURCE_EXHAUSTED '
        'quota RetryInfo secret-token https://generativelanguage.googleapis.com'
    )
    payload['partialMessage'] = raw
    payload['errorMessage'] = raw
    payload['logSummary'] = raw

    response = jsonable(assembler_module.assemble_batch_job_detail_response(payload))
    serialized = repr(response)

    assert response['partialMessage'] == (
        'AI summary fallback for GLOBAL_HEADLINE: '
        'AI provider request failed; fallback content was used.'
    )
    assert '429' not in serialized
    assert 'RetryInfo' not in serialized
    assert 'secret-token' not in serialized
    assert 'googleapis.com' not in serialized


def test_batch_list_assembler_preserves_normal_partial_reason(
    sample_batch_job_list_payload,
) -> None:
    payload = deepcopy(sample_batch_job_list_payload)
    reason = (
        'Naver news pagination cap was reached before covering the persisted '
        "window for keyword '증시'."
    )
    payload['items'][0]['partialMessage'] = reason

    response = jsonable(assembler_module.assemble_batch_job_list_response(payload))

    assert response['items'][0]['partialMessage'] == reason
