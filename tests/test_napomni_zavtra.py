"""«Люся, напомни завтра о работах» — и назавтра она действительно напоминает.

Заказчик попросил в рабочем чате напомнить о работах на следующий день.
Люся промолчала весь день: механизма напоминаний по просьбе в боте не было
вовсе, а ответить что-то вежливое модель может всегда.
"""
import types
from datetime import datetime, timedelta

import pytest

from bot import db, remind
import bot.handlers as H

PYATNITSA = datetime(2026, 8, 21, 19, 30, tzinfo=db.IRKUTSK_TZ)


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


# ---------- Разбор просьбы ----------

@pytest.mark.parametrize('prosba, kogda, o_chyom', [
    ('напомни завтра о работах на Седова 71', 'завтра в 09:00', 'работах на Седова 71'),
    ('напомни завтра в 9 про опрессовку', 'завтра в 09:00', 'опрессовку'),
    ('напомни завтра в 08:30 сдать показания', 'завтра в 08:30', 'сдать показания'),
    ('напомни в понедельник купить манометры', 'в понедельник в 09:00', 'купить манометры'),
    ('напомни через 2 часа перекрыть стояк', 'сегодня в 21:30', 'перекрыть стояк'),
    ('напомни 25 августа про поверку', 'во вторник в 09:00', 'поверку'),
    ('напомни утром проверить подвал', 'завтра в 09:00', 'проверить подвал'),
    ('напомни послезавтра всё проверить', 'послезавтра в 09:00', 'всё проверить'),
])
def test_prosba_razbiraetsya(prosba, kogda, o_chyom):
    when, text = remind.parse_reminder(prosba, PYATNITSA)

    assert remind.fmt_when(when, PYATNITSA) == kogda
    assert text == o_chyom


def test_obraschenie_ne_popadaet_v_tekst():
    _, text = remind.parse_reminder('Люся, напомни завтра о работах', PYATNITSA)

    assert text == 'работах'


@pytest.mark.parametrize('bez_sroka', [
    'напомни мне про это',
    'напомни, пожалуйста',
])
def test_bez_sroka_ne_ugadyvaem(bez_sroka):
    """Поставить не на тот день хуже, чем переспросить."""
    assert remind.parse_reminder(bez_sroka, PYATNITSA) is None


@pytest.mark.parametrize('ne_prosba', [
    'привезли задвижку 50',
    'Седова 71 хвс 1234',
    'напоминалка не работает',
])
def test_obychnye_soobscheniya_ne_trogaem(ne_prosba):
    assert remind.parse_reminder(ne_prosba, PYATNITSA) is None


# ---------- Чат ----------

class Msg:
    def __init__(self, text, chat_id=7):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(
            user_id=None if chat_id else 100, chat_id=chat_id,
            chat_type='chat' if chat_id else 'dialog')
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')


def event(text, chat_id=7):
    e = types.SimpleNamespace()
    e.message = Msg(text, chat_id)
    e.bot = None
    return e


async def test_prosba_v_chate_stavit_napominanie():
    e = event('Люся, напомни завтра в 8 про работы на Седова 71')

    vzyala = await H.handle_reminder(e, 'напомни завтра в 8 про работы на Седова 71', 100)

    assert vzyala is True
    spisok = db.list_reminders(100)
    assert len(spisok) == 1
    assert 'работы на Седова 71' in spisok[0]['text']
    assert spisok[0]['chat_id'] == 7, 'напомнить надо туда же, где просили'


async def test_lusya_govorit_kogda_i_kuda_napomnit():
    e = event('напомни завтра в 8 про опрессовку')

    await H.handle_reminder(e, 'напомни завтра в 8 про опрессовку', 100)

    otvet = e.message.sent[-1]
    assert 'завтра в 08:00' in otvet
    assert 'опрессовку' in otvet
    assert 'в чат' in otvet


async def test_v_lichke_napominaet_v_lichku():
    e = event('напомни завтра в 8 про опрессовку', chat_id=None)

    await H.handle_reminder(e, 'напомни завтра в 8 про опрессовку', 100)

    assert db.list_reminders(100)[0]['chat_id'] is None


async def test_bez_sroka_lusya_peresprashivaet():
    """Промолчать нельзя: человек уверен, что напоминание поставлено."""
    e = event('Люся, напомни мне про это')

    vzyala = await H.handle_reminder(e, 'напомни мне про это', 100)

    assert vzyala is True
    assert 'Скажите когда' in e.message.sent[-1]
    assert db.list_reminders(100) == []


async def test_proshedshee_vremya_ne_prinimaetsya():
    e = event('напомни сегодня в 00:01 про обход')

    await H.handle_reminder(e, 'напомни сегодня в 00:01 про обход', 100)

    assert 'прошло' in e.message.sent[-1]
    assert db.list_reminders(100) == []


# ---------- Доставка ----------

def test_srok_nastal_napominanie_v_ochered():
    vchera = (datetime.now(db.IRKUTSK_TZ) - timedelta(hours=1)).strftime('%d.%m.%Y %H:%M')
    db.add_reminder(100, 'Андрей', 'опрессовка', vchera, chat_id=7)

    pora = db.due_reminders()

    assert [r['text'] for r in pora] == ['опрессовка']


def test_budushchee_zhdyot_svoego_chasa():
    zavtra = (datetime.now(db.IRKUTSK_TZ) + timedelta(days=1)).strftime('%d.%m.%Y %H:%M')
    db.add_reminder(100, 'Андрей', 'опрессовка', zavtra)

    assert db.due_reminders() == []


async def test_napominanie_prihodit_v_tot_zhe_chat():
    from bot import reminders

    vchera = (datetime.now(db.IRKUTSK_TZ) - timedelta(minutes=5)).strftime('%d.%m.%Y %H:%M')
    db.add_reminder(100, 'Андрей', 'работы на Седова 71', vchera, chat_id=7)
    poslano = []

    class Bot:
        async def send_message(self, chat_id=None, user_id=None, text=None):
            poslano.append((chat_id, user_id, text))

    await reminders._send_asked(Bot())

    assert poslano[0][0] == 7, 'в чат, а не в личку'
    assert 'работы на Седова 71' in poslano[0][2]
    assert 'Андрей' not in poslano[0][2], 'кто просил — не афишируем'


async def test_odno_napominanie_odin_raz():
    from bot import reminders

    vchera = (datetime.now(db.IRKUTSK_TZ) - timedelta(minutes=5)).strftime('%d.%m.%Y %H:%M')
    db.add_reminder(100, 'Андрей', 'опрессовка', vchera, chat_id=7)

    class Bot:
        def __init__(self):
            self.n = 0

        async def send_message(self, chat_id=None, user_id=None, text=None):
            self.n += 1

    bot = Bot()
    await reminders._send_asked(bot)
    await reminders._send_asked(bot)

    assert bot.n == 1


def test_otmenyonnoe_ne_prihodit():
    vchera = (datetime.now(db.IRKUTSK_TZ) - timedelta(minutes=5)).strftime('%d.%m.%Y %H:%M')
    rid = db.add_reminder(100, 'Андрей', 'опрессовка', vchera, chat_id=7)

    db.cancel_reminder(rid)

    assert db.due_reminders() == []
