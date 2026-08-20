"""Дома выбираются кнопками, а не набором адреса руками.

Заказчик: «даёт списком, не кликабельные; напишите адрес — пишу
„тридцатый“, ноль». Стоя в подвале с телефоном, адрес не набирают.
"""
import pytest

from bot import db, houses
from bot import handlers as H
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


def knopki(kb):
    """Плоский список кнопок клавиатуры."""
    return [b for row in kb.as_markup().payload.buttons for b in row]


def payloads(kb):
    return [b.payload for b in knopki(kb)]


def test_kazhdyy_dom_otdelnoy_knopkoy():
    hs = houses.HOUSES[:3]

    kb = H.house_buttons(InlineKeyboardBuilder(), hs)

    assert payloads(kb) == [f"h:{h['id']}" for h in hs]


def test_knopki_schyotchikov_vedut_v_schyotchiki_doma():
    hs = houses.HOUSES[:2]
    kb = H.house_buttons(InlineKeyboardBuilder(), hs, payload='mt', counts={})

    assert payloads(kb) == [f"mt:{h['id']}" for h in hs]


def test_dom_bez_schyotchikov_pomechen_plyusom():
    dom = houses.HOUSES[0]
    kb = H.house_buttons(InlineKeyboardBuilder(), [dom], payload='mt', counts={})

    text = knopki(kb)[0].text
    assert text.startswith('➕'), 'видно, где заводить'


def test_dom_so_schyotchikami_pokazyvaet_ih_chislo():
    dom = houses.HOUSES[0]
    kb = H.house_buttons(InlineKeyboardBuilder(), [dom], payload='mt',
                         counts={dom['id']: 2})

    text = knopki(kb)[0].text
    assert text.startswith('🧮') and '(2)' in text


def test_v_menyu_est_vhod_v_schyotchiki():
    """Раньше кнопка вела в сводку для руководства — сантехнику отказ."""
    assert 'mtpick' in payloads(H.main_menu_kb())


def test_dlinnyy_adres_ne_lomaet_knopku():
    dlinnyy = dict(houses.HOUSES[0], address='Очень длинное название улицы 123/456')
    kb = H.house_buttons(InlineKeyboardBuilder(), [dlinnyy])

    assert len(knopki(kb)[0].text) <= 40
