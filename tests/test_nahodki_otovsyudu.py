"""Находка по квартире записывается отовсюду: любой чат, личка.

Заказчик выключил ленту в чате бригады не из-за находок: «она просто
заявки оттуда собирала, которые ещё и в обслуживании были, поэтому
отключили». Одно происшествие обсуждают в двух чатах — записи удваивались.

Но находки нужны: «не надо, чтобы она оттуда заявки какие-то тянула, а вот
информацию сохранить какую-то по находкам — вот это нужно и в чате, и в
этом, и в том, и в личке тоже».

Значит, разводим: лента — по настройке чата, находка — всегда. От
удвоения защищает не настройка, а проверка «дом, квартира и вид те же за
сутки» — это один выезд, а не два.
"""
import asyncio
import types

import pytest

from bot import db, houses
import bot.handlers as H

NAHODKA = 'Красных Мадьяр 14, 105 квартира, нашёл подмес'


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


def dom():
    return next(h for h in houses.HOUSES if h['address'] == 'Красных Мадьяр 14')


class Msg:
    def __init__(self, text, chat_id, chat_type):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(
            user_id=100 if chat_type == 'dialog' else None,
            chat_id=chat_id, chat_type=chat_type)
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')


def event(text, chat_id=7, chat_type='chat'):
    e = types.SimpleNamespace()
    e.message = Msg(text, chat_id, chat_type)
    e.bot = None
    return e


# ---------- Чат, где лента выключена ----------

async def test_v_lichnom_chate_nahodka_vsyo_ravno_zapisyvaetsya():
    """Ровно случай заказчика: чат бригады, лента выключена."""
    db.set_recording(7, False)

    e = event(NAHODKA)
    await H.on_text(e)
    await asyncio.sleep(0)          # ответ уходит фоновой задачей

    zametki = db.flat_notes(dom()['id'], 105)
    assert len(zametki) == 1
    assert 'подмес' in zametki[0]['text']


async def test_no_zayavku_v_lentu_ne_tyanet():
    """Из-за ленты запись и выключали — она остаётся выключенной."""
    db.set_recording(7, False)

    await H.on_text(event(NAHODKA))
    await asyncio.sleep(0)

    assert db.recent_chat_records() == [], 'сообщение не должно попасть в ленту'


async def test_v_rabochem_chate_vsyo_kak_bylo():
    db.set_recording(7, True)

    await H.on_text(event(NAHODKA))
    await asyncio.sleep(0)

    assert len(db.flat_notes(dom()['id'], 105)) == 1
    assert len(db.recent_chat_records()) == 1, 'лента рабочего чата ведётся'


async def test_odna_nahodka_iz_dvuh_chatov_ne_dvoitsya():
    """То же самое обсудили и в бригаде, и в «Обслуживании». Выезд один —
    и запись должна остаться одна."""
    db.set_recording(7, False)          # чат бригады
    db.set_recording(9, True)           # «Обслуживание»

    await H.on_text(event(NAHODKA, chat_id=7))
    await asyncio.sleep(0)
    await H.on_text(event(NAHODKA, chat_id=9))
    await asyncio.sleep(0)

    assert len(db.flat_notes(dom()['id'], 105)) == 1


# ---------- Личка ----------

async def test_v_lichke_nahodka_zapisyvaetsya():
    e = event(NAHODKA, chat_id=None, chat_type='dialog')
    await H.on_text(e)

    zametki = db.flat_notes(dom()['id'], 105)
    assert len(zametki) == 1
    assert 'кв. 105' in e.message.sent[-1]


async def test_v_lichke_vopros_pro_stoyak_ne_stal_nahodkoy():
    """«кв. 47» без находки — это вопрос «где стояк», а не отчёт."""
    e = event('Седова 65а/2 кв 47', chat_id=None, chat_type='dialog')
    await H.on_text(e)

    assert db.flat_notes(next(h for h in houses.HOUSES
                              if h['address'] == 'Седова 65а/2')['id']) == []
    assert 'стояк' in e.message.sent[-1].lower()


async def test_v_lichke_poisk_doma_ne_slomalsya():
    e = event('Красных Мадьяр 14', chat_id=None, chat_type='dialog')
    await H.on_text(e)

    assert 'Красных Мадьяр 14' in e.message.sent[-1]
    assert db.flat_notes(dom()['id']) == []
