"""Команда /отчет: статистика по рабочему чату с самого начала.

Заказчик: «Люся может выдать какой-то отчет за вот этот промежуток
времени, как мы начали работать по чату обслуживания — сколько там
заявок, где что, какая статистика».
"""
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
            body = types.SimpleNamespace(text='/отчет', attachments=None, mid='m', markup=None)
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
    await H.on_chat_report(e)
    return e.text


async def test_pustaya_lenta_ne_pokazyvaet_statistiku():
    out = await call()
    assert 'пока пусто' in out


async def test_schitaet_vsego_i_s_domom(monkeypatch):
    house = {'id': 3, 'address': 'Седова 71'}
    monkeypatch.setattr(H.houses, 'HOUSES_BY_ID', {3: house})
    db.add_chat_record(7, 'm1', 100, 'Виталя', 'течёт в подвале', house_id=3, is_issue=True)
    db.add_chat_record(7, 'm2', 100, 'Виталя', 'просто разговор')

    out = await call()
    assert 'Всего сообщений: 2' in out
    assert 'Привязано к домам: 1 из 1 домов' in out
    assert 'Похоже на аварийное: 1' in out


async def test_pokazyvaet_samye_aktivnye_doma(monkeypatch):
    doma = {3: {'id': 3, 'address': 'Седова 71'}, 4: {'id': 4, 'address': 'Трилиссера 8'}}
    monkeypatch.setattr(H.houses, 'HOUSES_BY_ID', doma)
    for i in range(3):
        db.add_chat_record(7, f'm{i}', 100, 'Виталя', 'течёт', house_id=3, is_issue=True)
    db.add_chat_record(7, 'm9', 100, 'Виталя', 'проверили', house_id=4)

    out = await call()
    assert 'Седова 71 — 3 сообщ., аварийных 3' in out
    assert 'Трилиссера 8 — 1 сообщ.' in out
    # первым в списке — тот, где сообщений больше
    assert out.index('Седова 71') < out.index('Трилиссера 8')


async def test_pokazyvaet_dату_pervoy_zapisi():
    db.add_chat_record(7, 'm1', 100, 'Виталя', 'первое сообщение')
    out = await call()
    day = db.now().split()[0]
    assert f'с {day} по сегодня' in out


async def test_pokazyvaet_itogi_pasporta(monkeypatch):
    house = {'id': 3, 'address': 'Седова 71'}
    monkeypatch.setattr(H.houses, 'HOUSES_BY_ID', {3: house})
    db.add_chat_record(7, 'm1', 100, 'Виталя', 'нашли причину', house_id=3)
    db.add_house_fact(3, '2027-09-01', 'пробку вырвало с насоса', 'находка')

    out = await call()
    assert 'В паспорта домов записано итогов: 1 по 1 домам' in out


async def test_bez_itogov_pasporta_stroka_ne_pokazyvaetsya():
    db.add_chat_record(7, 'm1', 100, 'Виталя', 'просто разговор')
    out = await call()
    assert 'паспорта домов' not in out
