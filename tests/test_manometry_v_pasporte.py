"""Манометры видны в паспорте дома, и Люся про них знает.

Приборы учитывались в отдельном разделе «Техника»: в паспорте дома их не
было, а у агента не было инструмента — на прямой вопрос про манометр Люся
отвечала общими словами.
"""
import json

import pytest

from bot import agent, db, houses
from bot import handlers as H

ADRES = '4-я Советская 30'


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


@pytest.fixture
def dom():
    return next(h for h in houses.ALL_HOUSES if h['address'] == ADRES)


def postavit_manometr(dom, place, serial, poverka, tp='ИТП'):
    point_id = db.add_point(dom['id'], place, tp, 'Андрей Кузьмин')
    db.add_device(point_id, serial, poverka, 1, 'Андрей Кузьмин')
    return point_id


def test_pasport_pokazyvaet_manometry(dom):
    postavit_manometr(dom, 'домовой контур после теплообменника, подача', '04517', '2027-03-12')
    postavit_manometr(dom, 'домовой контур после теплообменника, обратка', '04518', '2027-03-12')

    text = H.passport_text(dom)

    assert 'Манометры (2)' in text
    assert '04517' in text and '04518' in text
    assert 'подача' in text and 'обратка' in text
    assert '12.03.2027' in text


def test_pasport_bez_priborov_govorit_pryamo(dom):
    assert 'не заведены' in H.passport_text(dom)


def test_kartochka_doma_schitaet_manometry(dom):
    postavit_manometr(dom, 'подача', '04517', '2027-03-12')

    assert 'Манометров: 1' in H.house_card_text(dom)


def test_prosrochennaya_poverka_vidna_na_kartochke(dom):
    postavit_manometr(dom, 'подача', '04517', '2020-01-01')

    assert 'поверка просрочена: 1' in H.house_card_text(dom)


def test_lusya_vidit_manometry_instrumentom(dom):
    postavit_manometr(dom, 'домовой контур после теплообменника, подача', '04517', '2027-03-12')

    data = json.loads(agent._tool_get_equipment(dom['id']))

    assert data['address'] == ADRES
    m = data['манометры'][0]
    assert m['прибор']['заводской_номер'] == '04517'
    assert m['прибор']['поверка_до'] == '2027-03-12'
    assert 'подача' in m['место'] and 'ИТП' in m['место']


def test_instrument_soobschaet_chto_priborov_net(dom):
    data = json.loads(agent._tool_get_equipment(dom['id']))

    assert data['манометры'] == []
    assert data['note'] == 'манометров не заведено'


def test_instrument_zaregistrirovan_u_agenta():
    assert 'get_equipment' in agent.TOOL_FUNCS
    names = [t['function']['name'] for t in agent.TOOLS]
    assert 'get_equipment' in names


def test_istoriya_zamen_vidna(dom):
    """Приборы сменяют друг друга — в паспорте это должно быть заметно."""
    point_id = postavit_manometr(dom, 'подача', '03288', '2025-01-01')
    db.add_device(point_id, '04517', '2027-03-12', 1, 'Андрей Кузьмин')

    data = json.loads(agent._tool_get_equipment(dom['id']))

    assert data['манометры'][0]['замен_за_всё_время'] == 2
    assert data['манометры'][0]['прибор']['заводской_номер'] == '04517', 'показываем текущий'
