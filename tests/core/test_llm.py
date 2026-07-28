from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from tests.support import load_module

llm_module = load_module('app.core.llm')
settings_module = load_module('app.core.settings')


@pytest.mark.asyncio
async def test_gemini_json_client_returns_controlled_timeout(monkeypatch):
    class HangingModel:
        async def ainvoke(self, _messages):
            await asyncio.sleep(1)
            return SimpleNamespace(content='{"ok": true}')

    client = llm_module.GeminiJsonClient(
        settings_module.Settings(
            app_env='development',
            gemini_api_key='test-key',
            llm_timeout_seconds=0.01,
        )
    )
    monkeypatch.setattr(client, '_build_model', lambda: HangingModel())

    with pytest.raises(llm_module.LlmTimeoutError, match='timed out'):
        await client.invoke_json(system_prompt='system', user_prompt='user')


@pytest.mark.asyncio
async def test_gemini_json_client_parses_json_when_model_responds(monkeypatch):
    class RespondingModel:
        async def ainvoke(self, _messages):
            return SimpleNamespace(content='{"ok": true}')

    client = llm_module.GeminiJsonClient(
        settings_module.Settings(app_env='development', gemini_api_key='test-key')
    )
    monkeypatch.setattr(client, '_build_model', lambda: RespondingModel())

    assert await client.invoke_json(system_prompt='system', user_prompt='user') == {
        'ok': True
    }


@pytest.mark.asyncio
async def test_gemini_json_client_parses_structured_text_blocks(monkeypatch):
    class RespondingModel:
        async def ainvoke(self, _messages):
            return SimpleNamespace(
                content=[
                    {
                        'type': 'text',
                        'text': '```json\n{"ok": true}\n```',
                        'extras': {},
                    }
                ]
            )

    client = llm_module.GeminiJsonClient(
        settings_module.Settings(app_env='development', gemini_api_key='test-key')
    )
    monkeypatch.setattr(client, '_build_model', lambda: RespondingModel())

    assert await client.invoke_json(system_prompt='system', user_prompt='user') == {
        'ok': True
    }


@pytest.mark.asyncio
async def test_gemini_json_client_combines_text_blocks_from_ai_message(monkeypatch):
    class RespondingModel:
        async def ainvoke(self, _messages):
            return AIMessage(
                content=[
                    {'type': 'image', 'image_url': 'https://example.test/image.png'},
                    {'type': 'text', 'text': '{"ok":'},
                    {'type': 'text', 'text': ' true}'},
                ]
            )

    client = llm_module.GeminiJsonClient(
        settings_module.Settings(app_env='development', gemini_api_key='test-key')
    )
    monkeypatch.setattr(client, '_build_model', lambda: RespondingModel())

    assert await client.invoke_json(system_prompt='system', user_prompt='user') == {
        'ok': True
    }


@pytest.mark.parametrize(
    'content',
    [
        '',
        [],
        [{'type': 'text', 'text': ''}],
        [{'type': 'image', 'image_url': 'https://example.test/image.png'}],
    ],
    ids=[
        'empty-string',
        'empty-block-list',
        'empty-text-block',
        'unsupported-block',
    ],
)
def test_gemini_json_client_rejects_structured_content_without_text(content):
    with pytest.raises(ValueError, match='Expected text content'):
        llm_module.GeminiJsonClient._extract_text_content(content)


def test_gemini_json_client_exposes_configured_model_identity():
    client = llm_module.GeminiJsonClient(
        settings_module.Settings(
            app_env='development',
            llm_model='test-model',
            llm_concurrency_limit=3,
        )
    )

    assert client.model_name == 'test-model'
    assert client.concurrency_limit == 3
