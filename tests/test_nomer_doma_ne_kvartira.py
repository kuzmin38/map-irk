"""«Седова 71 кв 105» — 105-я квартира, а не 71-я.

Нашлось на живой проверке: сообщение «Красных Мадьяр 14 кв 105 опять
подмес» Люся записала как находку в квартире 14. Слово «кв» стоит сразу
за номером дома, и «14 кв» прочиталось как «14-я квартира» — регулярка
съедала номер дома вместе со словом, и настоящей квартиры за ним уже не
видела.

Запятая случайно спасала («Красных Мадьяр 14, кв. 105» разбиралось
верно), поэтому баг и жил: в отчётах он всплывал редко. Цена ошибки
высокая — находка оседает в карточке чужой квартиры и через полгода
читается как факт.
"""
import pytest

from bot import flats, houses


def dom(address):
    return next(h for h in houses.HOUSES if h['address'] == address)


@pytest.mark.parametrize('text,adres,kvartira', [
    # Ровно то, на чём поймали
    ('Красных Мадьяр 14 кв 105 опять подмес', 'Красных Мадьяр 14', 105),
    ('Седова 71 кв 105 течь', 'Седова 71', 105),
    ('Седова 71 кв. 105 засор', 'Седова 71', 105),
    ('Трилиссера 22 кв 34 подмес', 'Трилиссера 22', 34),
    # Запятая спасала и раньше — пусть так и остаётся
    ('Красных Мадьяр 14, кв. 105, подмес', 'Красных Мадьяр 14', 105),
])
def test_nomer_doma_ne_stanovitsya_kvartiroy(text, adres, kvartira):
    assert flats.parse_flat(text, dom(adres)) == kvartira


@pytest.mark.parametrize('text,adres', [
    ('Седова 71, 71 квартира, подмес', 'Седова 71'),
    ('Седова 71 кв 71 подмес', 'Седова 71'),
])
def test_kvartira_s_nomerom_doma_zakonna(text, adres):
    """В доме 71 есть квартира 71 — вычёркивать её нельзя."""
    assert flats.parse_flat(text, dom(adres)) == 71


@pytest.mark.parametrize('text,adres,kvartira', [
    ('71/1 105 квартира', 'Седова 71/1', 105),
    ('Трилиссера 18б кв 5 засор', 'Трилиссера 18б', 5),
    ('Седова 65а/2 кв 47', 'Седова 65а/2', 47),
])
def test_korpusa_i_bukvy_kak_i_byli(text, adres, kvartira):
    """С корпусом и буквой номер дома на квартиру и не похож."""
    assert flats.parse_flat(text, dom(adres)) == kvartira


def test_bez_doma_razbor_ne_menyaetsya():
    assert flats.parse_flat('кв. 105 подмес') == 105
    assert flats.parse_flat('105 квартира') == 105


def test_nahodka_lozhitsya_v_tu_kvartiru():
    """Сквозь parse_note — так его и зовёт запись находки."""
    h = dom('Красных Мадьяр 14')
    assert flats.parse_note('Красных Мадьяр 14 кв 105 опять подмес', h) == (105, 'подмес')
