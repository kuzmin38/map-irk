import json

import pytest

from bot import agent, db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    monkeypatch.setattr(agent.ai, 'enabled', lambda: True)
    monkeypatch.setattr(agent, '_update_profile', _noop_update_profile)


async def _noop_update_profile(*args, **kwargs):
    pass


async def test_answer_calls_tool_and_returns_final_text(monkeypatch):
    calls = []

    async def fake_chat(messages, tools=None, max_tokens=900, temperature=0.4):
        calls.append(messages)
        if len(calls) == 1:
            return {
                'role': 'assistant', 'content': None,
                'tool_calls': [{'id': 'call_1', 'type': 'function', 'function': {
                    'name': 'find_house',
                    'arguments': json.dumps({'query': 'Байкальская 99'})}}],
            }
        return {'role': 'assistant', 'content': 'Байкальская 99 — наш дом.'}

    monkeypatch.setattr(agent.ai, 'chat', fake_chat)

    result = await agent.answer(1, 'Андрей', 'Байкальская 99 наш дом?')

    assert result == 'Байкальская 99 — наш дом.'
    assert len(calls) == 2
    assert calls[1][-1]['role'] == 'tool'
    assert calls[1][-1]['tool_call_id'] == 'call_1'
    tool_result = json.loads(calls[1][-1]['content'])
    assert any(h['address'] == 'Байкальская 99' for h in tool_result['found'])

    history = db.recent_chat_history(1, limit=10)
    assert history[-2] == {'role': 'user', 'content': 'Байкальская 99 наш дом?'}
    assert history[-1] == {'role': 'assistant', 'content': 'Байкальская 99 — наш дом.'}


async def test_answer_returns_none_when_ai_disabled(monkeypatch):
    monkeypatch.setattr(agent.ai, 'enabled', lambda: False)
    result = await agent.answer(1, 'Андрей', 'привет')
    assert result is None


async def test_answer_returns_none_after_max_rounds(monkeypatch):
    async def fake_chat(messages, tools=None, max_tokens=900, temperature=0.4):
        return {
            'role': 'assistant', 'content': None,
            'tool_calls': [{'id': 'call_x', 'type': 'function', 'function': {
                'name': 'get_directory', 'arguments': '{"section": "all"}'}}],
        }

    monkeypatch.setattr(agent.ai, 'chat', fake_chat)

    result = await agent.answer(1, 'Андрей', 'бесконечный вопрос')
    assert result is None


async def test_answer_uses_stored_profile_in_system_prompt(monkeypatch):
    db.set_user_notes(1, 'Часто спрашивает про Байкальскую 99.')
    captured = {}

    async def fake_chat(messages, tools=None, max_tokens=900, temperature=0.4):
        captured['system'] = messages[0]['content']
        return {'role': 'assistant', 'content': 'Ок.'}

    monkeypatch.setattr(agent.ai, 'chat', fake_chat)

    await agent.answer(1, 'Андрей', 'привет')
    assert 'Часто спрашивает про Байкальскую 99.' in captured['system']