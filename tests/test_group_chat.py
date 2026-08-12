"""Поведение в групповом чате: молчит, пока не позвали по имени."""
import types

import pytest

from bot import db
import bot.handlers as H


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


class Msg:
    def __init__(self, text, chat_type):
        self.body = types.SimpleNamespace(text=text, attachments=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=None, chat_id=7, chat_type=chat_type)
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text)


def event(text, chat_type='chat'):
    e = types.SimpleNamespace()
    e.message = Msg(text, chat_type)
    e.bot = None
    return e


def test_is_group_detects_chat():
    assert H.is_group(event('привет', 'chat')) is True
    assert H.is_group(event('привет', 'channel')) is True
    assert H.is_group(event('привет', 'dialog')) is False


@pytest.mark.parametrize('text, expected', [
    ('Люся, что по нормативам ГВС?', 'что по нормативам ГВС?'),
    ('люся что по нормативам ГВС', 'что по нормативам ГВС'),
    ('@Люся привет', 'привет'),
    ('Люси вопрос', 'вопрос'),
    ('что по нормативам ГВС, Люся?', 'что по нормативам ГВС'),
])
def test_strip_address_recognises_call(text, expected):
    addressed, rest = H.strip_address(text)
    assert addressed is True
    assert rest == expected


@pytest.mark.parametrize('text', [
    'на Байкальской 237 течь в подвале',
    'Люстра в подъезде не горит',
    'закончили работы на Седова',
])
def test_strip_address_ignores_other_messages(text):
    addressed, _ = H.strip_address(text)
    assert addressed is False


async def test_group_message_without_call_is_ignored():
    e = event('на Байкальской 237 течь в подвале')
    await H.on_text(e)
    assert e.message.sent == []


async def test_group_message_with_call_gets_answer(monkeypatch):
    async def fake_answer(uid, name, text):
        return f'Ответ на: {text}'
    monkeypatch.setattr(H.agent, 'answer', fake_answer)
    e = event('Люся, что по нормативам ГВС?')
    await H.on_text(e)
    assert e.message.sent == ['Ответ на: что по нормативам ГВС?']


async def test_group_call_without_ai_gets_polite_reply(monkeypatch):
    async def no_ai(uid, name, text):
        return None
    monkeypatch.setattr(H.agent, 'answer', no_ai)
    e = event('Люся, что по нормативам ГВС?')
    await H.on_text(e)
    assert 'на связи' in e.message.sent[0]


async def test_private_chat_still_searches_houses():
    e = event('Байкальская 237', chat_type='dialog')
    await H.on_text(e)
    assert 'Байкальская 237' in e.message.sent[0]
