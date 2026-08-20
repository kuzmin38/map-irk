"""Люся умеет забыть переписку — иначе повторяет свои же старые ошибки.

В запрос к модели подмешиваются прошлые сообщения. Один раз она ответила,
что «4-я Советская 30 — это дом 28», и потом читала это как факт, даже
после того как причину в коде устранили.
"""
import pytest

from bot import db


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


def test_istoriya_i_profil_stirayutsya():
    db.add_chat_message(1, 'user', 'где 30 дом?')
    db.add_chat_message(1, 'assistant', 'Дом 28 — это 4-я Советская, 30')
    db.set_user_notes(1, 'бригадир, любит краткие ответы')

    zabyto = db.forget_user(1)

    assert zabyto == 2
    assert db.recent_chat_history(1) == []
    assert db.get_user_notes(1) == ''


def test_chuzhaya_pamyat_ne_stradaet():
    db.add_chat_message(1, 'user', 'моё сообщение')
    db.add_chat_message(2, 'user', 'чужое сообщение')

    db.forget_user(1)

    assert len(db.recent_chat_history(2)) == 1


def test_sbros_pustoy_pamyati_ne_lomaetsya():
    assert db.forget_user(999) == 0


def test_dannye_po_domam_ne_tragayutsya():
    """Сброс касается только разговора: приборы и заявки остаются."""
    from bot import houses

    dom = houses.ALL_HOUSES[0]
    db.add_meter(dom['id'], 'hvs', 'ХВС подвал', 'Андрей')
    db.add_chat_message(1, 'user', 'привет')

    db.forget_user(1)

    assert len(db.list_meters(dom['id'])) == 1
