"""Показания и фото из рабочего чата попадают в учёт.

Сантехники скидывают показания прямо в чат, где Люся администратор.
Раньше это оседало «в ленте» текстом и в учёт счётчиков не попадало.
"""
import pytest

from bot import db, houses
from bot import handlers as H

ADRES = 'Седова 71'
CHAT_ID = -69324053039792


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(H, 'DOCS_DIR', str(tmp_path / 'docs'))
    db.init()


@pytest.fixture
def dom():
    d = next(h for h in houses.ALL_HOUSES if h['address'] == ADRES)
    db.add_meter(d['id'], 'hvs', 'ХВС подвал', 'Андрей')
    db.add_meter(d['id'], 'gvs', 'ГВС подвал', 'Андрей')
    return d


class FakeBody:
    def __init__(self, text):
        self.text = text
        self.attachments = []
        self.mid = 'm1'


class FakeRecipient:
    chat_type = 'chat'
    chat_id = CHAT_ID


class FakeSender:
    user_id = 5
    full_name = 'Константин Толщин'


class FakeMessage:
    def __init__(self, text):
        self.body = FakeBody(text)
        self.recipient = FakeRecipient()
        self.sender = FakeSender()
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text)


class FakeEvent:
    def __init__(self, text):
        self.message = FakeMessage(text)
        self.bot = None


async def test_pokazaniya_iz_chata_zapisyvayutsya(dom):
    e = FakeEvent('Седова 71 хвс 1234, гвс 567')

    obrabotano = await H.handle_readings(e, e.message.body.text, 5)

    assert obrabotano
    hvs = next(m for m in db.list_meters(dom['id']) if m['kind'] == 'hvs')
    assert db.meter_readings(hvs['id'])[0]['value'] == 1234
    assert 'Седова 71' in e.message.sent[0]


async def test_adres_beryotsya_iz_lenty_chata(dom):
    """В чате адрес называют один раз, дальше пишут показания подряд."""
    db.add_chat_record(chat_id=CHAT_ID, mid='0', user_id=5, user_name='К',
                       text='Седова 71 снимаю показания', house_id=dom['id'],
                       has_files=0, is_issue=0)
    e = FakeEvent('гвс 567')

    assert await H.handle_readings(e, 'гвс 567', 5)

    gvs = next(m for m in db.list_meters(dom['id']) if m['kind'] == 'gvs')
    assert db.meter_readings(gvs['id'])[0]['value'] == 567


async def test_bez_adresa_i_bez_lenty_prosit_adres(dom):
    e = FakeEvent('гвс 567')

    assert await H.handle_readings(e, 'гвс 567', 5)

    assert 'по какому дому' in e.message.sent[0]
    assert db.meter_readings(db.list_meters(dom['id'])[1]['id']) == []


@pytest.mark.parametrize('vopros', [
    'сколько было хвс 1234 в прошлом месяце?',
    'какой хвс 1234',
    'Люся, что там по гвс 567?',
])
async def test_voprosy_ne_zapisyvayutsya_kak_pokazaniya(dom, vopros):
    """«Сколько было хвс 1234?» — это вопрос, а не сдача показаний."""
    e = FakeEvent(vopros)

    assert await H.handle_readings(e, vopros, 5) is False

    hvs = next(m for m in db.list_meters(dom['id']) if m['kind'] == 'hvs')
    assert db.meter_readings(hvs['id']) == []


async def test_obychnoe_soobschenie_chata_ne_trogaem(dom):
    e = FakeEvent('на Седова 71 течь в подвале, приеду после обеда')

    assert await H.handle_readings(e, e.message.body.text, 5) is False
    assert e.message.sent == []


async def test_net_schyotchika_predlagaet_zavesti():
    d = next(h for h in houses.ALL_HOUSES if h['address'] == 'Седова 67')
    e = FakeEvent('Седова 67 хвс 100')

    assert await H.handle_readings(e, e.message.body.text, 5)

    assert 'нет счётчика' in e.message.sent[0]
