"""Парковка 4-я Советская 26 — нежилое здание ЖК Четыре солнца.

Там стоят теплосчётчик и ХВС: в жилых домах тепло не снимают, а здесь
это как раз нужно, поэтому предупреждение про прямые договоры не должно
мешать.
"""
import pytest

from bot import db, houses
from bot import handlers as H

ADRES = '4-я Советская 26'


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


@pytest.fixture
def parkovka():
    return next(h for h in houses.HOUSES if h['address'] == ADRES)


def test_parkovka_v_spravochnike_i_v_rabote(parkovka):
    assert parkovka['kind'] == 'nonres'
    assert 'арковка' in parkovka['note']


def test_privyazana_k_chetyryom_solncam(parkovka):
    db.init()
    assert db.get_house_complex(parkovka['id']) == '4sun'


@pytest.mark.parametrize('zapros', [
    ADRES, '4 советская 26', 'двадцать шестой дом', '26', 'парковка',
])
def test_nahoditsya_raznymi_sposobami(zapros):
    found = houses.search(zapros)

    assert found and found[0]['address'] == ADRES


def test_teplo_i_hvs_zavodyatsya(parkovka):
    """Именно ради этих двух счётчиков парковку и заводили."""
    teplo = db.add_meter(parkovka['id'], 'heat', 'Теплосчётчик парковки', 'Андрей')
    hvs = db.add_meter(parkovka['id'], 'hvs', 'ХВС парковки', 'Андрей')

    vidy = {m['kind'] for m in db.list_meters(parkovka['id'])}
    assert vidy == {'heat', 'hvs'}
    assert db.get_meter(teplo)['status'] == db.METER_ACTIVE
    assert db.get_meter(hvs)['status'] == db.METER_ACTIVE


def test_pokazaniya_prinimayutsya_odnim_soobscheniem(parkovka):
    db.add_meter(parkovka['id'], 'heat', 'Теплосчётчик', 'Андрей')
    adres, pary = H.parse_readings(f'{ADRES} тепло 1890')

    assert houses.detect_house(adres)['address'] == ADRES
    assert pary == [('heat', 1890.0)]


def test_karta_otkryvaetsya(parkovka):
    """Координаты приблизительные, но ссылки работать обязаны."""
    gis, ya = houses.map_links(parkovka)

    assert gis.startswith('https://2gis.ru/') and str(parkovka['lat']) in gis
    assert ya.startswith('https://yandex.ru/maps/')


def test_spravochnik_ostalsya_soglasovannym():
    """Каждый адрес из списка активных существует, у каждого есть ЖК."""
    izvestnye = {houses._norm_addr(h['address']) for h in houses.ALL_HOUSES}
    aktivnye = houses.load_active()
    privyazka = houses.load_complex_map()

    assert not (aktivnye - izvestnye), 'в active_houses.txt адрес с опечаткой'
    assert not (set(privyazka) - izvestnye), 'в house_complex.txt адрес с опечаткой'
    assert not (aktivnye - set(privyazka)), 'дом в работе без привязки к ЖК'
