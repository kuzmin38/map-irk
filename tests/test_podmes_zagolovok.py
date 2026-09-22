"""«Подмес 28 - 123» — заголовок поквартирного обхода, номер дома без улицы.

Заказчик прислал Люсе в личку отчёт обхода: первая строка «Подмес 28 - 123»
(дом 28, начали с квартиры 123), дальше построчно результаты по квартирам
(«123 - нет дома», «105 - обнаружен незакрытый гиг душ» и т.д.). Дом 28 у
нас единственный («4-я Советская 28»), но Люся его не нашла и заявила, что
дома с номером 28 в списке нет вовсе — хотя инструмент find_house не
вызывала: ответила без обращения к данным. Заказчик: «28-й он вообще один
у нас, она должна без улицы его находить».
"""
import pytest

from bot import houses

REAL_TEXT = (
    'Подмес 28 - 123\n\n'
    '22 сентября 8:15\n'
    '123 - нет дома\n'
    '114 - нет дома\n'
    '105 - обнаружен незакрытый гиг душ. Наличие обратного клапана можно '
    'посмотреть только эндоскопом. Дал рекомендации собственнику, чтоб '
    'закрывал его. Собственник говорит, что иногда горячую воду нужно '
    'прогонять.\n'
    '96 - нет дома\n'
    '87 - нет дома\n'
    '78 - гиг душа нет, водогрея нет. Жалоб тоже нет.\n\n'
    'Нужно попасть в квартиры куда не попал.'
)


def test_tot_samyy_otchyot():
    h = houses.detect_house(REAL_TEXT)
    assert h and h['address'] == '4-я Советская 28'


@pytest.mark.parametrize('text', [
    'Подмес 28 - 123, обход квартир',
    'подмес 28-123',
    'ПОДМЕС 28 123',
])
def test_raznye_napisaniya_zagolovka(text):
    h = houses.detect_house(text)
    assert h and h['address'] == '4-я Советская 28'


def test_neodnoznachnyy_nomer_ne_ugadyvaetsya(monkeypatch):
    """Если номер не единственный — код не должен выбирать наугад."""
    original = next(h for h in houses.HOUSES if h['address'] == '4-я Советская 28')
    dvoynik = dict(original)
    dvoynik['id'] = 9999
    dvoynik['address'] = 'Пискунова 28'
    monkeypatch.setattr(houses, 'HOUSES', houses.HOUSES + [dvoynik])
    assert houses.detect_house('Подмес 28 - 123, обход') is None


def test_podmes_bez_nomera_nichego_ne_lomaet():
    assert houses.detect_house('Костя, нашёл подмес, что делать?') is None
