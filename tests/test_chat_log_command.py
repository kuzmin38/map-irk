"""Команда /chat: проверить, что Люся слышит чат, когда она молчит."""
import types

import pytest

from bot import db
import bot.handlers as H


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


class Event:
    def __init__(self):
        self.sent = []
        outer = self

        class Msg:
            body = types.SimpleNamespace(text='/chat', attachments=None, mid='m', markup=None)
            sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
            recipient = types.SimpleNamespace(user_id=100, chat_id=None, chat_type='dialog')

            async def answer(self, text=None, attachments=None):
                outer.sent.append(text)

        self.message = Msg()

    @property
    def text(self):
        return '\n'.join(self.sent)


async def call():
    e = Event()
    await H.on_chat_log(e)
    return e.text


async def test_pustaya_baza_podskazyvaet_pro_administratora():
    out = await call()
    assert 'пока пусто' in out
    assert 'администратором' in out


async def test_pokazyvaet_rasshifrovku_bez_privyazki_k_domu():
    rid = db.add_chat_record(7, 'm1', 100, 'Константин', None, has_files=True)
    db.set_chat_transcript(rid, 'перекрыли стояк, меняем участок')

    out = await call()
    assert 'дом не определён' in out
    assert 'перекрыли стояк' in out
    assert '🎙 расшифровано: 1' in out


async def test_vlozhenie_bez_rasshifrovki_vidno_otdelno():
    db.add_chat_record(7, 'm1', 100, 'Александр', None, has_files=True)

    out = await call()
    assert 'вложение, расшифровки нет' in out
    assert '🎙 расшифровано: 0' in out


async def test_avariynoe_pomecheno_i_dom_nazvan(monkeypatch):
    house = {'id': 3, 'address': 'Седова 71'}
    monkeypatch.setattr(H.houses, 'HOUSES_BY_ID', {3: house})
    db.add_chat_record(7, 'm1', 100, 'Андрей', 'течёт в подвале',
                       house_id=3, is_issue=True)

    out = await call()
    assert '🚨' in out
    assert 'Седова 71' in out
