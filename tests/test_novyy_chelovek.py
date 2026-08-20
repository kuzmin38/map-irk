"""Люсе может написать любой, кому дали её имя.

Заказчик спросил, открыт ли доступ. Открыт: бот отвечает всякому, кто ему
напишет. Но пришедший остаётся «без роли» — работы поручать нельзя, — и
раньше об этом никто не узнавал.
"""
import types

import pytest

from bot import db
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, user_id=None, text=None, attachments=None):
        self.sent.append((user_id, text, attachments))


class Msg:
    def __init__(self, text, uid, name):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=uid, full_name=name)
        self.recipient = types.SimpleNamespace(user_id=uid, chat_id=None, chat_type='dialog')
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text)


def event(text, uid=200, name='Виталий Новиков'):
    e = types.SimpleNamespace()
    e.message = Msg(text, uid, name)
    e.bot = Bot()
    return e


def test_pervyy_prishedshiy_stanovitsya_adminom():
    assert db.upsert_user(1, 'Андрей') is True
    assert db.get_user(1)['role'] == 'admin'


def test_povtornoe_obraschenie_ne_schitaetsya_novym():
    db.upsert_user(1, 'Андрей')

    assert db.upsert_user(1, 'Андрей') is False


async def test_novichka_pokazyvayut_tem_kto_naznachaet_roli():
    db.upsert_user(1, 'Андрей')          # админ
    e = event('/start')

    await H.on_start(e)

    komu = [uid for uid, _, _ in e.bot.sent]
    assert komu == [1]
    assert 'Виталий Новиков' in e.bot.sent[0][1]
    assert 'без роли' in e.bot.sent[0][1]


async def test_v_uvedomlenii_est_knopka_naznacheniya():
    db.upsert_user(1, 'Андрей')
    e = event('/start')

    await H.on_start(e)

    markup = e.bot.sent[0][2]
    payloads = [b.payload for row in markup[0].payload.buttons for b in row]
    assert payloads == ['pplu:200']


async def test_rabochim_bez_prav_novichka_ne_shlyut():
    db.upsert_user(1, 'Андрей')
    db.upsert_user(2, 'Константин')
    db.set_user_role(2, 'plumber')
    e = event('/start')

    await H.on_start(e)

    assert [uid for uid, _, _ in e.bot.sent] == [1], 'сантехнику роли не раздавать'


async def test_starogo_znakomogo_ne_obyavlyayut():
    db.upsert_user(1, 'Андрей')
    db.upsert_user(200, 'Виталий Новиков')
    e = event('/start')

    await H.on_start(e)

    assert e.bot.sent == []


async def test_novichok_srazu_poluchaet_menyu():
    db.upsert_user(1, 'Андрей')
    e = event('/start')

    await H.on_start(e)

    assert e.message.sent, 'человеку ответили, а не только руководству'
