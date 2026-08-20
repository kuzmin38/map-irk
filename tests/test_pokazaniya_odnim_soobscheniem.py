"""Показания счётчика сдаются одним сообщением, без блуждания по меню.

Сантехник стоит в подвале у прибора с телефоном. Раньше, чтобы записать
цифру, надо было пройти меню → дом → счётчики → выбрать счётчик → ввести.
Теперь достаточно написать «Седова 71 хвс 1234».
"""
import json

import pytest

from bot import agent, db, houses
from bot import handlers as H

ADRES = 'Седова 71'


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(H, 'DOCS_DIR', str(tmp_path / 'docs'))
    db.init()


@pytest.fixture
def dom():
    return next(h for h in houses.ALL_HOUSES if h['address'] == ADRES)


@pytest.mark.parametrize('soobschenie, adres, pary', [
    ('Седова 71 хвс 1234', 'Седова 71', [('hvs', 1234.0)]),
    ('Седова 71 хвс 1234, гвс 567', 'Седова 71', [('hvs', 1234.0), ('gvs', 567.0)]),
    ('Байкальская 126/1 гвс 456,7', 'Байкальская 126/1', [('gvs', 456.7)]),
    ('холодная 12,3 горячая 45', '', [('hvs', 12.3), ('gvs', 45.0)]),
    ('тепло 1890', '', [('heat', 1890.0)]),
])
def test_razbor_svobodnogo_teksta(soobschenie, adres, pary):
    assert H.parse_readings(soobschenie) == (adres, pary)


def test_nomer_doma_ne_putaetsya_s_pokazaniem():
    """«Седова 71 хвс 67»: 67 — показание, хотя дом Седова 67 существует."""
    adres, pary = H.parse_readings('Седова 71 хвс 67')

    assert adres == 'Седова 71', 'адрес отрезан до слова о счётчике'
    assert pary == [('hvs', 67.0)]
    assert houses.detect_house(adres)['address'] == 'Седова 71'


@pytest.mark.parametrize('ne_pokazanie', [
    'Седова 71', 'просто текст', '1234', 'на Седова 71 течь в подвале',
])
def test_obychnye_soobscheniya_ne_prinimayutsya_za_pokazaniya(ne_pokazanie):
    assert H.parse_readings(ne_pokazanie)[1] == []


def test_schyotchik_nahoditsya_po_vidu(dom):
    db.add_meter(dom['id'], 'hvs', 'ХВС подвал', 'Андрей')

    m = H.pick_meter(dom['id'], 'hvs')

    assert m['label'] == 'ХВС подвал'
    assert H.pick_meter(dom['id'], 'gvs') is None, 'чего нет — того нет'


def test_neskolko_schyotchikov_odnogo_vida_dayut_spisok(dom):
    """Выбирать за человека нельзя — вернём список, он ткнёт в нужный."""
    db.add_meter(dom['id'], 'hvs', 'ХВС первый ввод', 'Андрей')
    db.add_meter(dom['id'], 'hvs', 'ХВС второй ввод', 'Андрей')

    m = H.pick_meter(dom['id'], 'hvs')

    assert isinstance(m, list) and len(m) == 2


def test_pokazanie_zapisyvaetsya_i_schitaet_rashod(dom):
    m_id = db.add_meter(dom['id'], 'hvs', 'ХВС подвал', 'Андрей')
    db.add_reading(m_id, 1000, '2026-07', 1, 'Андрей')

    delta, warning = H.check_anomaly(m_id, 1234)

    assert delta == 234
    assert warning is None


def test_pokazanie_menshe_predyduschego_preduprezhdaet(dom):
    m_id = db.add_meter(dom['id'], 'hvs', 'ХВС подвал', 'Андрей')
    db.add_reading(m_id, 1000, '2026-07', 1, 'Андрей')

    delta, warning = H.check_anomaly(m_id, 900)

    assert delta < 0
    assert 'МЕНЬШЕ' in warning


def test_lusya_vidit_schyotchiki_instrumentom(dom):
    m_id = db.add_meter(dom['id'], 'hvs', 'ХВС подвал', 'Андрей Кузьмин')
    db.add_reading(m_id, 1000, '2026-07', 1, 'Андрей Кузьмин')
    db.add_reading(m_id, 1234, '2026-08', 1, 'Андрей Кузьмин')

    data = json.loads(agent._tool_get_meters(dom['id']))

    m = data['счётчики'][0]
    assert m['название'] == 'ХВС подвал'
    assert m['вид'] == 'ХВС'
    assert m['последнее_показание']['значение'] == 1234
    assert m['последнее_показание']['подал'] == 'Андрей Кузьмин'
    assert m['расход_за_период'] == 234


def test_instrument_soobschaet_chto_schyotchikov_net(dom):
    data = json.loads(agent._tool_get_meters(dom['id']))

    assert data['счётчики'] == []
    assert data['note'] == 'счётчиков не заведено'


def test_instrument_zaregistrirovan():
    assert 'get_meters' in agent.TOOL_FUNCS
    assert 'get_meters' in [t['function']['name'] for t in agent.TOOLS]


def test_foto_privyazyvaetsya_k_pokazaniyu(dom):
    """Снимок табло — подтверждение цифры, храним рядом с показанием."""
    m_id = db.add_meter(dom['id'], 'hvs', 'ХВС подвал', 'Андрей')
    r_id = db.add_reading(m_id, 1234, '2026-08', 1, 'Андрей')

    db.set_reading_photo(r_id, '/data/docs/readings/1.jpg')

    assert db.meter_readings(m_id)[0]['photo'] == '/data/docs/readings/1.jpg'
