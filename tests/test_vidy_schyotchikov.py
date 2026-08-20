"""Виды счётчиков — те, что реально снимают.

Заказчик: ГВС-водомеры не учитываем совсем. Учитываем два холодных —
домовой и на офисы. Теплосчётчики нужны в нежилых (парковки), а в жилых
домах их не снимают: у жильцов прямые договоры со сбытовой компанией.
"""
import pytest

from bot import db, houses
from bot import handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(H, 'DOCS_DIR', str(tmp_path / 'docs'))
    db.init()


def test_zavodyatsya_tolko_nuzhnye_vidy():
    assert set(H.METER_KINDS) == {'hvs', 'hvs_office', 'heat', 'other'}
    assert 'gvs' not in H.METER_KINDS, 'ГВС завести нельзя'


def test_dva_holodnyh_razlichayutsya():
    assert 'дом' in H.METER_KINDS['hvs']
    assert 'офис' in H.METER_KINDS['hvs_office']


def test_staryy_gvs_vsyo_ravno_podpisan():
    """Записи, заведённые раньше, не должны показываться без названия."""
    assert 'gvs' in H.METER_LABELS
    assert 'ГВС' in H.METER_LABELS['gvs']


@pytest.mark.parametrize('tekst, vid', [
    ('Седова 71 хвс 1234', 'hvs'),
    ('Седова 71 холодная 1234', 'hvs'),
    ('Седова 71 домовой 1234', 'hvs'),
    ('Седова 71 хвс офис 567', 'hvs_office'),
    ('Седова 71 офисный 567', 'hvs_office'),
    ('Седова 71 тепло 1890', 'heat'),
    ('Седова 71 гкал 1890', 'heat'),
])
def test_vid_uznayotsya_iz_teksta(tekst, vid):
    assert H.parse_readings(tekst)[1] == [(vid, pytest.approx(
        float(tekst.split()[-1])))]


def test_dom_i_ofis_v_odnom_soobschenii():
    _, pary = H.parse_readings('Седова 71 хвс дом 100, хвс офис 200')

    assert pary == [('hvs', 100.0), ('hvs_office', 200.0)]


class FakeBody:
    def __init__(self, text):
        self.text, self.attachments, self.mid = text, [], 'm1'


class FakeRecipient:
    chat_type = 'dialog'
    chat_id = 1


class FakeSender:
    user_id = 5
    full_name = 'Андрей'


class FakeMessage:
    def __init__(self, text):
        self.body, self.recipient, self.sender = FakeBody(text), FakeRecipient(), FakeSender()
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text)


class FakeEvent:
    def __init__(self, text):
        self.message, self.bot = FakeMessage(text), None


async def test_pro_gvs_govorit_pryamo():
    """Не молчит и не предлагает завести — объясняет, что не учитываем."""
    e = FakeEvent('Седова 71 гвс 500')

    assert await H.handle_readings(e, e.message.body.text, 5)

    otvet = ' '.join(e.message.sent)
    assert 'не учитываем' in otvet
    assert 'Завести' not in otvet


async def test_pro_nuzhnyy_vid_predlagaet_zavesti():
    e = FakeEvent('Седова 71 хвс 1234')

    assert await H.handle_readings(e, e.message.body.text, 5)

    assert 'нет счётчика' in ' '.join(e.message.sent)


async def test_dom_i_ofis_pishutsya_v_raznye_schyotchiki():
    d = next(h for h in houses.ALL_HOUSES if h['address'] == 'Седова 71')
    dom_id = db.add_meter(d['id'], 'hvs', 'ХВС дом', 'Андрей')
    ofis_id = db.add_meter(d['id'], 'hvs_office', 'ХВС офисы', 'Андрей')
    e = FakeEvent('Седова 71 хвс 100, офис 200')

    await H.handle_readings(e, e.message.body.text, 5)

    assert db.meter_readings(dom_id)[0]['value'] == 100
    assert db.meter_readings(ofis_id)[0]['value'] == 200
