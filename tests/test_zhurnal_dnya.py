"""«Что Люся насохраняла за вчерашний день» — одним экраном.

Каждая запись живёт в своей таблице: показания, заявки, работы, находки
по квартирам, опись, паспорта. Посмотреть всё сразу было негде —
приходилось обходить пять экранов.
"""
import types

import pytest

from bot import db, houses
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


class Msg:
    def __init__(self, text=''):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=100, chat_id=None, chat_type='dialog')
        self.sent = []
        self.knopki = []
        self.link = None

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')
        self.knopki.append(attachments)


def event():
    e = types.SimpleNamespace()
    e.message = Msg()
    e.bot = None
    e.callback = types.SimpleNamespace(
        user=types.SimpleNamespace(user_id=100, full_name='Андрей'))
    return e


SEGODNYA = None


@pytest.fixture
def den():
    return db.now()[:10]


def test_pustoy_den_govorit_pryamo(den):
    lines = H.journal_lines(den)

    assert 'ничего не записано' in '\n'.join(lines)


def test_v_zhurnal_popadaet_vsyo(den):
    dom = houses.detect_house('Седова 71')
    meter = db.add_meter(dom['id'], 'hvs', 'Ввод ХВС', 'Андрей')
    db.add_reading(meter, 1234.0, '2026-09', 100, 'Андрей')
    db.add_request(dom['id'], dom['address'], 'течь в подвале', 100, 'Андрей')
    db.add_work(dom['id'], 'Заменить задвижку', None, 'Андрей', 100)
    db.add_flat_note(dom['id'], 105, 'Нашёл подмес', kind='подме', author='Андрей')
    db.add_item('мотопомпа', 'подвал', dom['id'], user_name='Андрей')
    db.set_passport_field(dom['id'], 'rozliv', 'Нижний, сталь ДУ50', 'Андрей')
    db.add_shutoff(dom['id'], 105, 7, 16, [105], by_id=100, by_name='Андрей')
    db.add_reminder(100, 'Андрей', 'опрессовка', '05.09.2026 09:00')

    text = '\n'.join(H.journal_lines(den))

    for kusok in ('Показания', 'Заявки', 'Работы', 'Находки по квартирам',
                  'Перекрытия стояков', 'В опись', 'Паспорта домов',
                  'Заведены счётчики', 'Напоминания'):
        assert kusok in text, f'в журнале нет раздела: {kusok}'
    assert 'Седова 71' in text
    assert 'мотопомпа' in text
    assert 'кв. 105' in text


def test_chuzhoy_den_ne_popadaet(den):
    dom = houses.detect_house('Седова 71')
    db.add_item('мотопомпа', 'подвал', dom['id'], user_name='Андрей')
    with db._conn() as c:
        c.execute("UPDATE inventory SET created_at = '01.01.2026 10:00'")

    assert 'мотопомпа' not in '\n'.join(H.journal_lines(den))
    assert 'мотопомпа' in '\n'.join(H.journal_lines('01.01.2026'))


async def test_ekran_zhurnala_otkryvaetsya():
    dom = houses.detect_house('Седова 71')
    db.add_item('вышка-тура', 'склад', dom['id'], user_name='Андрей')

    msg = Msg()
    await H.run_action('jrnl', msg, 100, event())

    assert 'ЧТО ЗАПИСАНО' in msg.sent[-1]
    assert 'вышка-тура' in msg.sent[-1]


async def test_est_knopka_za_vchera():
    """Спрашивают обычно про вчера — кнопка должна быть под рукой."""
    msg = Msg()
    await H.run_action('jrnl', msg, 100, event())

    payloads = [b.payload for row in msg.knopki[-1][0].payload.buttons for b in row]

    assert 'jrnl:v' in payloads
    assert 'itogi' in payloads, 'и разбор по домам рядом'


async def test_vchera_beryot_vcherashniy_den():
    from datetime import datetime, timedelta

    dom = houses.detect_house('Седова 71')
    db.add_item('вчерашняя тура', 'склад', dom['id'], user_name='Андрей')
    vchera = (datetime.now(db.IRKUTSK_TZ) - timedelta(days=1)).strftime('%d.%m.%Y')
    with db._conn() as c:
        c.execute('UPDATE inventory SET created_at = ?', (vchera + ' 10:00',))

    msg = Msg()
    await H.run_action('jrnl:v', msg, 100, event())

    assert vchera in msg.sent[-1]
    assert 'вчерашняя тура' in msg.sent[-1]


def test_komanda_est_v_menyu():
    payload = next((p for name, _, p in H.QUICK_COMMANDS if name == 'журнал'), None)

    assert payload == 'jrnl'
