"""«Не разобрала запись» без единой строчки в логе — реальный случай.

Заказчик прислал голосовое в личку на улице, Люся дважды ответила «Не
разобрала запись», а в логе Railway не осталось ни ошибки, ни
предупреждения — только тишина. Разбирались по логам: OpenRouter ответил
200, но модель сама решила, что речи не разобрать (шум, тихий голос), и
вернула пустую строку — так и было задумано в PROMPT («Если речи нет —
ответь пустой строкой»). Это не баг кода, а честный ответ модели, но
раньше такой случай было не отличить в логе от того, что запрос вообще не
ушёл. Добавили строку в лог именно на этот случай.
"""
import types

import pytest

from bot import ai, transcribe


class FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    def __init__(self, resp):
        self._resp = resp

    def post(self, *a, **kw):
        return self._resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def ai_vklyuchen(monkeypatch, tmp_path):
    monkeypatch.setattr(ai, 'KIMI_API_KEY', 'test-key')
    # transcribe_file размер файла не проверяет через ai — нужен реальный файл
    f = tmp_path / 'audio.mp3'
    f.write_bytes(b'0' * 100)
    yield str(f)


async def test_pustoy_otvet_modeli_popadaet_v_log(monkeypatch, ai_vklyuchen, caplog):
    body = {'choices': [{'message': {'content': ''}}]}
    resp = FakeResp(200, body)
    monkeypatch.setattr(transcribe.aiohttp, 'ClientSession', lambda: FakeSession(resp))

    with caplog.at_level('INFO', logger='transcribe'):
        text = await transcribe.transcribe_file(ai_vklyuchen)

    assert text is None
    assert any('не расслышала' in r.message for r in caplog.records)


async def test_normalnyy_otvet_prohodit_kak_obychno(monkeypatch, ai_vklyuchen):
    body = {'choices': [{'message': {'content': 'Седова 71, засор в подвале'}}]}
    resp = FakeResp(200, body)
    monkeypatch.setattr(transcribe.aiohttp, 'ClientSession', lambda: FakeSession(resp))

    text = await transcribe.transcribe_file(ai_vklyuchen)

    assert text == 'Седова 71, засор в подвале'
