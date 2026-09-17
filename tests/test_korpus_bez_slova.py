"""«Седова 65 5» — номер корпуса без буквы и без слова «корпус».

Виталя наговорил видеоотчёт, расшифровка вышла: «Седова 65 5. Забилась
канализация.» Люся его не признала домом. Модель в пересказе сама
добавила слово «в» между числами — получилось «Седова 65, в 5» — и по
пересказу видно, что адрес там явно есть, а вопрос «какой дом» всё
равно задался: код искал точное совпадение с «65а/5», а в тексте не
было ни буквы «а», ни знака «/».

В живой речи букву корпуса и слово «корпус» часто вообще не произносят,
называют просто два числа подряд. Склеивать их можно, только если
результат — реальный адрес: так нельзя придумать корпус, которого нет.
"""
import pytest

from bot import houses


def dom(address):
    return next(h for h in houses.HOUSES if h['address'] == address)


def test_tot_samyy_sluchay():
    """Ровно та расшифровка, что пришла от Виталя."""
    h = houses.detect_house('Седова 65 5. Забилась канализация.')
    assert h and h['address'] == 'Седова 65а/5'


@pytest.mark.parametrize('text,adres', [
    ('Седова 65 5', 'Седова 65а/5'),
    ('Седова 65а 5', 'Седова 65а/5'),
    ('седова 65а 5 подвал топит', 'Седова 65а/5'),
    ('Трилиссера 8 3 засор', 'Трилиссера 8/3'),
    ('Байкальская 126 3 топит', 'Байкальская 126/3'),
])
def test_dva_chisla_podryad_sklеivayutsya(text, adres):
    h = houses.detect_house(text)
    assert h and h['address'] == adres


def test_nesushchestvuyushchiy_korpus_ne_pridumyvaetsya():
    """У Седова 65а корпуса 9 нет — и придумывать его нельзя."""
    assert houses.detect_house('Седова 65 9 авария') is None


@pytest.mark.parametrize('text', [
    'Седова 65а, квартира 5, течь',
    'Седова 65а квартира 5',
    'седова 65а 5 квартир поменяли счётчики',
    'седова 65а 5 человек ждут воду',
])
def test_slovo_mezhdu_chislami_lomaet_sklейку(text):
    """«Квартира» между числами или счёт после — это не корпус."""
    h = houses.detect_house(text)
    assert h is None or h['address'] != 'Седова 65а/5'


def test_polnyy_adres_po_prezhnemu_rabotaet():
    h = houses.detect_house('Седова 65а/5, забилась канализация')
    assert h and h['address'] == 'Седова 65а/5'


def test_sklein_korpus_ne_lomaet_prostoy_tekst():
    """Обычная речь без адреса не должна плодить ложных склеек."""
    assert houses._sklein_korpus('привезли 30 задвижек') == 'привезли 30 задвижек'
    assert houses._sklein_korpus('') == ''
