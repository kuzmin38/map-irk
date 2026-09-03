"""Поиск записей с адресами, которых у нас нет.

В отчёте по Красных Мадьяр 14 появились заявки по Байкальской 237 — дому,
которого мы не обслуживаем. Причина была в инструкции: модель переписала
пример как услышанное. Инструкцию поправили, но записи с выдумкой
остались в ленте и в выгрузке — их надо найти и убрать.
"""
import types

import pytest

from bot import db, proverka
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


class Msg:
    def __init__(self):
        self.body = types.SimpleNamespace(text='', attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=100, chat_id=None, chat_type='dialog')
        self.sent = []
        self.link = None

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')


def event():
    e = types.SimpleNamespace()
    e.message = Msg()
    e.bot = None
    e.callback = types.SimpleNamespace(
        user=types.SimpleNamespace(user_id=100, full_name='Андрей'))
    return e


OTCHYOT = ('Поступили заявки:\n'
           '• Дом 14, офис "Ситтипарк": течь по ливневой канализации.\n'
           '• Байкальская 237, квартира 47: замена смесителя на кухне.\n'
           '• Ленина 5: замена стояка.')


# ---------- Что считать чужим ----------

def test_vydumannyy_adres_nahoditsya():
    """«Ленина 5» нет ни в работе, ни в справочнике — это дописала модель."""
    naydeno = proverka.chuzhie_adresa(OTCHYOT)

    assert ('Ленина 5', proverka.VYDUMKA) in naydeno


def test_chuzhoy_uchastok_otlichaetsya_ot_vydumki(monkeypatch):
    """«Байкальская 237» в справочнике есть, просто участок не наш."""
    from bot import houses

    nashi = [h for h in houses.ALL_HOUSES if h['address'] != 'Байкальская 237']
    monkeypatch.setattr(houses, 'HOUSES', nashi)
    monkeypatch.setattr(houses, 'HOUSES_BY_ID', {h['id']: h for h in nashi})

    naydeno = dict(proverka.chuzhie_adresa(OTCHYOT))

    assert naydeno.get('Байкальская 237') == proverka.NE_V_RABOTE
    assert naydeno.get('Ленина 5') == proverka.VYDUMKA


@pytest.mark.parametrize('text', [
    'Седова 71 хвс 1234, всё сделали',
    'Красных Мадьяр 14 · 3 видео',
    'На 4-я Советская 30 промыли систему',
    'Трилиссера 8/5 салон красоты',
    'Лебедева-Кумача 29 заменили задвижку',
])
def test_nashi_doma_ne_schitayutsya_chuzhimi(text):
    """Улица бывает из двух слов — «Мадьяр 14» без «Красных» не опознаётся."""
    assert proverka.chuzhie_adresa(text) == []


@pytest.mark.parametrize('text', [
    'квартира 47 подмес',
    'стояк 1 из 9',
    'Подъезд 2, этаж 5',
    'Показания 1234',
])
def test_ne_adresa_ne_lovim(text):
    assert proverka.chuzhie_adresa(text) == []


def test_pustoy_tekst_ne_lomaet():
    assert proverka.chuzhie_adresa('') == []
    assert proverka.chuzhie_adresa(None) == []


# ---------- Экран ----------

async def test_ekran_pokazyvaet_nahodki():
    db.upsert_user(100, 'Андрей')
    db.add_chat_record(chat_id=7, mid='m1', user_id=100, user_name='Андрей',
                       text='', has_files=True)
    with db._conn() as c:
        c.execute('UPDATE chat_messages SET transcript = ?', (OTCHYOT,))
    db.add_chat_record(chat_id=7, mid='m2', user_id=100, user_name='Костя',
                       text='Седова 71 хвс 1234')

    msg = Msg()
    await H.run_action('chk', msg, 100, event())

    otvet = msg.sent[-1]
    assert 'Ленина 5' in otvet
    assert 'нет в справочнике' in otvet
    assert 'Седова 71' not in otvet, 'наш дом в список не попадает'


async def test_chisto_govorit_pryamo():
    db.upsert_user(100, 'Андрей')
    db.add_chat_record(chat_id=7, mid='m', user_id=100, user_name='Костя',
                       text='Седова 71 хвс 1234')

    msg = Msg()
    await H.run_action('chk', msg, 100, event())

    assert 'не нашла' in msg.sent[-1]


async def test_zapis_udalyaetsya():
    db.upsert_user(100, 'Андрей')
    rec = db.add_chat_record(chat_id=7, mid='m1', user_id=100, user_name='Андрей',
                             text=OTCHYOT)

    msg = Msg()
    await H.run_action(f'chkdel:{rec}', msg, 100, event())

    assert db.get_chat_record(rec) is None
    assert 'Удалила' in msg.sent[-1]


async def test_bez_roli_proverka_nedostupna():
    db.upsert_user(100, 'Андрей')     # первый — админ
    db.upsert_user(200, 'Костя')      # без роли

    msg = Msg()
    await H.run_action('chk', msg, 200, event())

    assert 'для руководства' in msg.sent[-1]


def test_komanda_est_v_menyu():
    payload = next((p for name, _, p in H.QUICK_COMMANDS if name == 'проверка'), None)

    assert payload == 'chk'
