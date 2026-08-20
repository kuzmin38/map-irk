"""Критичное — красным, недозаполненное — жёлтым.

Заказчик: скачок расхода и несостыковки красным, мелочи вроде счётчика
без номера — жёлтым. Логика одна на бота и приложение, иначе они начнут
показывать разное.
"""
import pytest

from bot import checks, db, houses
from bot import handlers as H
from bot import webapp


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


@pytest.fixture
def dom():
    return houses.HOUSES[0]


def urovni(found):
    return {f['level'] for f in found}


def texts(found):
    return ' | '.join(f['text'] for f in found)


def test_pokazanie_menshe_predyduschego_krasnoe(dom):
    m = db.add_meter(dom['id'], 'hvs', 'ХВС', 'А')
    db.update_meter(m, serial='1')
    db.add_reading(m, 1000, '2026-07', 1, 'А')
    db.add_reading(m, 900, '2026-08', 1, 'А')

    found = checks.house_findings(dom['id'], '2026-08')

    krasnye = [f for f in found if f['level'] == checks.RED]
    assert krasnye and 'меньше предыдущего' in krasnye[0]['text']


def test_skachok_rashoda_krasnyy(dom):
    m = db.add_meter(dom['id'], 'hvs', 'ХВС', 'А')
    db.update_meter(m, serial='1')
    for period, value in (('2026-06', 1000), ('2026-07', 1100), ('2026-08', 1500)):
        db.add_reading(m, value, period, 1, 'А')

    found = checks.house_findings(dom['id'], '2026-08')

    assert any(f['level'] == checks.RED and 'утечка' in f['text'] for f in found)


def test_rovnyy_rashod_ne_trevozhit(dom):
    m = db.add_meter(dom['id'], 'hvs', 'ХВС', 'А')
    db.update_meter(m, serial='1')
    db.set_passport_field(dom['id'], 'year', '1998', 'А')
    for period, value in (('2026-06', 1000), ('2026-07', 1100), ('2026-08', 1200)):
        db.add_reading(m, value, period, 1, 'А')

    found = checks.house_findings(dom['id'], '2026-08')

    assert checks.RED not in urovni(found), texts(found)


def test_schyotchik_bez_nomera_zhyoltyy(dom):
    db.add_meter(dom['id'], 'hvs', 'ХВС', 'А')

    found = checks.house_findings(dom['id'], '2026-08')

    assert any(f['level'] == checks.YELLOW and 'нет заводского номера' in f['text']
               for f in found)


def test_nesnyatoe_pokazanie_zhyoltoe(dom):
    m = db.add_meter(dom['id'], 'hvs', 'ХВС', 'А')
    db.update_meter(m, serial='1')

    found = checks.house_findings(dom['id'], '2026-08')

    assert any('за месяц не снято' in f['text'] for f in found)
    assert checks.RED not in urovni(found), 'это мелочь, не критика'


def test_prosrochennaya_poverka_krasnaya(dom):
    point = db.add_point(dom['id'], 'подача', 'ИТП', 'А')
    db.add_device(point, '04517', '2020-01-01', 1, 'А')

    found = checks.house_findings(dom['id'], '2026-08')

    assert any(f['level'] == checks.RED and 'просрочена' in f['text'] for f in found)


def test_prosrochennaya_rabota_krasnaya(dom):
    db.add_work(dom['id'], 'опрессовка', '2020-05-01', 'А', user_id=1)

    found = checks.house_findings(dom['id'], '2026-08')

    assert any(f['level'] == checks.RED and 'просрочена работа' in f['text']
               for f in found)


def test_uroven_doma_beryot_hudshee(dom):
    assert checks.house_level([]) is None
    assert checks.house_level([{'level': checks.YELLOW, 'text': ''}]) == checks.YELLOW
    assert checks.house_level([{'level': checks.YELLOW, 'text': ''},
                               {'level': checks.RED, 'text': ''}]) == checks.RED


def test_krasnoe_idyot_pervym(dom):
    m = db.add_meter(dom['id'], 'hvs', 'ХВС', 'А')
    db.add_reading(m, 1000, '2026-07', 1, 'А')
    db.add_reading(m, 900, '2026-08', 1, 'А')

    found = checks.house_findings(dom['id'], '2026-08')

    assert found[0]['level'] == checks.RED


def test_prilozhenie_otdayot_zamechaniya_i_uroven(dom):
    db.add_meter(dom['id'], 'hvs', 'ХВС', 'А')

    state = webapp.house_state(dom['id'])
    payload = webapp.build_payload()

    assert state['findings']
    nash = next(h for h in payload['houses'] if h['id'] == dom['id'])
    assert nash['level'] == checks.YELLOW


def test_bot_pokazyvaet_te_zhe_zamechaniya(dom):
    """Одна логика: иначе бот и приложение начнут расходиться."""
    m = db.add_meter(dom['id'], 'hvs', 'ХВС', 'А')
    db.add_reading(m, 1000, '2026-07', 1, 'А')
    db.add_reading(m, 900, H.current_period(), 1, 'А')

    text = H.house_card_text(dom)

    assert '❗' in text
    assert 'меньше предыдущего' in text


def test_chistyy_dom_bez_zamechaniy(dom):
    db.set_passport_field(dom['id'], 'year', '1998', 'А')

    assert checks.house_findings(dom['id'], '2026-08') == []
