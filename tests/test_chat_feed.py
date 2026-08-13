"""Тихий сбор сообщений рабочего чата и привязка их к домам."""
import types

import pytest

from bot import db, houses
import bot.handlers as H


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


class Msg:
    def __init__(self, text, chat_type='chat', files=False):
        self.body = types.SimpleNamespace(
            text=text, attachments=([object()] if files else None), mid='m1', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Константин')
        self.recipient = types.SimpleNamespace(user_id=None, chat_id=7, chat_type=chat_type)
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text)


def event(text, chat_type='chat', files=False):
    e = types.SimpleNamespace()
    e.message = Msg(text, chat_type, files)
    e.bot = None
    return e


def house_id(address):
    return next(h['id'] for h in houses.HOUSES if h['address'] == address)


async def test_group_message_is_recorded_silently():
    e = event('на Байкальской 237 течь в подвале')
    await H.on_text(e)
    assert e.message.sent == []                       # в чат не отвечает
    records = db.house_chat_records(house_id('Байкальская 237'))
    assert len(records) == 1
    assert records[0]['is_issue'] == 1                # распознала как аварийное
    assert records[0]['user_name'] == 'Константин'


async def test_message_without_address_is_recorded_without_house():
    e = event('всем доброе утро, выезжаем')
    await H.on_text(e)
    stats = db.chat_stats_for_day(db.now()[:10])
    assert stats['total'] == 1
    assert stats['with_house'] == 0
    assert stats['issues'] == 0


async def test_photo_report_is_recorded_with_file_flag():
    e = event('Трилиссера 8/4 всё сделали', files=True)
    await H.on_text(e)
    records = db.house_chat_records(house_id('Трилиссера 8/4'))
    assert records[0]['has_files'] == 1


async def test_private_messages_are_not_recorded():
    e = event('на Байкальской 237 течь', chat_type='dialog')
    await H.on_text(e)
    assert db.chat_stats_for_day(db.now()[:10])['total'] == 0


async def test_issue_words_detected():
    for text in ('нет воды в доме', 'прорвало стояк', 'засор канализации', 'срочно!'):
        assert H.ISSUE_WORDS.search(text), text
    for text in ('привет', 'закончили покраску'):
        assert not H.ISSUE_WORDS.search(text), text


async def test_house_feed_shown_in_card(monkeypatch):
    hid = house_id('Байкальская 237')
    await H.on_text(event('на Байкальской 237 течь в подвале'))

    class CB:
        def __init__(self):
            self.callback = types.SimpleNamespace(
                payload=f'chat:{hid}', callback_id='x',
                user=types.SimpleNamespace(user_id=100, full_name='А'))
            self.message = Msg('', 'dialog')
            self.bot = types.SimpleNamespace()

            async def ack(**kw):
                pass
            self.bot.send_callback = ack

    c = CB()
    await H.on_callback(c)
    assert 'Из рабочего чата' in c.message.sent[0]
    assert 'течь в подвале' in c.message.sent[0]
