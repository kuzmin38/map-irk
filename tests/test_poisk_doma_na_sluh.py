"""Дом находится, как его ни назови.

Заказчик про «4-я Советская 30»: могу сказать «ЖК Четыре солнца, тридцатый
дом», могу «четвёртое советское тридцатый», могу просто «тридцатый дом» —
дом с таким номером у нас один.
"""
import pytest

from bot import houses

ADRES = '4-я Советская 30'


@pytest.mark.parametrize('zapros', [
    'ЖК четыре солнца, тридцатый дом',
    'жк четыре солнца тридцатый дом',
    'четыре солнца тридцатый дом',
    '4 солнца тридцатый дом',
    'четвёртое советское тридцатый дом',
    'четвертое советское тридцатый дом',
    'четвёртая советская тридцать',
    'тридцатый дом',
    'дом тридцать',
    '30',
    'советская 30',
    '4-я Советская 30',
])
def test_tridcatyy_dom_nahoditsya(zapros):
    found = houses.search(zapros)
    assert found, f'по запросу «{zapros}» не нашлось ничего'
    assert found[0]['address'] == ADRES


@pytest.mark.parametrize('rech, adres', [
    ('поставил манометры на Советской тридцать', ADRES),
    ('на четвёртой советской тридцать течь в подвале', ADRES),
    ('Байкальская двести тридцать семь, кв 47 топит', 'Байкальская 237'),
    ('на Пограничном 1-Г нет воды', 'Пограничный 1-Г'),
])
def test_dom_uznayotsya_v_zhivoy_rechi(rech, adres):
    h = houses.detect_house(rech)
    assert h and h['address'] == adres


@pytest.mark.parametrize('rech', [
    'приезжал два раза, ничего не нашли',
    'два дня не могли попасть в квартиру',
    'сделали за сорок минут',
])
def test_obychnyy_schyot_ne_prinimaetsya_za_adres(rech):
    assert houses.detect_house(rech) is None


def test_neodnoznachnyy_nomer_dayot_vybor():
    """Домов с номером 29 два — Люся не должна выбирать за человека."""
    found = houses.search('двадцать девятый дом')
    adresa = [h['address'] for h in found]
    assert 'Лебедева-Кумача 29' in adresa
    assert 'Розы Люксембург 29' in adresa


def test_korpusa_ne_putayutsya():
    """«Пограничный 1-Г» и «1-Д» — разные дома, а раньше оба сводились к «1-»."""
    assert houses._norm('Пограничный 1-Г') != houses._norm('Пограничный 1-Д')
    for adres in ('Пограничный 1-Г', 'Пограничный 1-Д', 'Пограничный 1-Е'):
        found = houses.search(adres)
        assert found[0]['address'] == adres, f'{adres} нашёлся как {found[0]["address"]}'


def test_privychnyy_poisk_ne_slomalsya():
    assert houses.search('байкальская 237')[0]['address'] == 'Байкальская 237'
    assert houses.search('Седова 65а/2')[0]['address'] == 'Седова 65а/2'
    assert houses.search('розы люксембург')
    assert houses.search('несуществующая улица 999') == []
