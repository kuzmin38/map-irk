"""Молчание в личке всегда объяснено в логе.

Люся «не отвечала» на сообщение, а в логах не было ни строчки: обработчик
отрабатывал и тихо выходил. Снаружи это неотличимо от «сообщение не дошло»,
поэтому каждый молчаливый выход теперь называет причину.
"""
import logging
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
    def __init__(self, text):
        self.body = types.SimpleNamespace(text=text, attachments=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=100, chat_id=7,
                                               chat_type='dialog')
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text)


def lichka(text):
    e = types.SimpleNamespace()
    e.message = Msg(text)
    e.bot = None
    return e


async def test_chuzhaya_komanda_ne_uhodit_v_tishinu(caplog):
    e = lichka('/version')
    with caplog.at_level(logging.INFO, logger='bot.handlers'):
        await H.on_text(e)

    assert e.message.sent == [], 'на чужую команду Люся по-прежнему молчит'
    assert 'не распознана' in caplog.text
    assert '/version' in caplog.text


async def test_soobschenie_bez_teksta_nazyvaet_prichinu(caplog):
    e = lichka('')
    with caplog.at_level(logging.INFO, logger='bot.handlers'):
        await H.on_text(e)

    assert e.message.sent == []
    assert 'без текста' in caplog.text


async def test_lichka_vidna_v_loge(caplog):
    with caplog.at_level(logging.INFO, logger='bot.handlers'):
        await H.on_text(lichka('/непонятно'))

    assert 'Личка от 100' in caplog.text


async def test_otvet_otmechaetsya_v_loge(caplog):
    msg = Msg('неважно')
    with caplog.at_level(logging.INFO, logger='bot.handlers'):
        await H.send(msg, 'короткий ответ')

    assert msg.sent == ['короткий ответ']
    assert 'Ответила: 14 симв., частей 1' in caplog.text


async def test_dlinnyy_otvet_schitaetsya_celikom(caplog):
    msg = Msg('неважно')
    long = '\n'.join(['строка'] * 1000)
    with caplog.at_level(logging.INFO, logger='bot.handlers'):
        await H.send(msg, long)

    assert len(msg.sent) > 1, 'длинный текст уходит частями'
    assert f'Ответила: {len(long)} симв.' in caplog.text, 'считаем весь ответ'
