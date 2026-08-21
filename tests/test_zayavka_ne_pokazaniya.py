"""Сообщение о протечке — не показания счётчика.

Заказчик прислал в чат видео с подписью «Офис Корал Трэвэл 28 дом. Течь
с потолка. Предположительно канализация в кв. 3». Люся записала это как
показание 28 по офисному счётчику на 4-й Советской 28: слово «офис» она
принимала за вид прибора сама по себе, а число 28 — за показание.
"""
import types

import pytest

from bot import db, houses
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(H, 'DOCS_DIR', str(tmp_path / 'docs'))
    db.init()
    H.STATE.clear()


SLUCHAY = ('Офис Корал Трэвэл 28 дом. Течь с потолка. '
           'Предположительно канализация в кв. 3')


def test_soobschenie_o_techi_ne_pokazaniya():
    assert H.parse_readings(SLUCHAY) == ('', [])


@pytest.mark.parametrize('rasskaz', [
    'Офис Корал Трэвэл 28 дом',
    'В офисе 3 не работает свет',
    'Заехали в офис, там 2 человека ждут',
    'Домовой чат просит уборку в 5 подъезде',
])
def test_svyaznyy_tekst_za_pokazaniya_ne_prinimaetsya(rasskaz):
    """«Офис» и «домовой» — уточнение вида прибора, а не признак показаний."""
    assert H.parse_readings(rasskaz)[1] == []


@pytest.mark.parametrize('stroka, pary', [
    ('Седова 71 домовой 1234', [('hvs', 1234.0)]),
    ('Седова 71 офисный 567', [('hvs_office', 567.0)]),
    ('Седова 71 домовой 1234, офисный 567',
     [('hvs', 1234.0), ('hvs_office', 567.0)]),
    ('Седова 71 хвс 1234', [('hvs', 1234.0)]),
    ('Байкальская 126/1 показания хвс 456,7', [('hvs', 456.7)]),
])
def test_nastoyaschie_pokazaniya_rabotayut_kak_i_rabotali(stroka, pary):
    assert H.parse_readings(stroka)[1] == pary


# ---------- Сквозной путь ----------

class Msg:
    def __init__(self, text):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=None, chat_id=7, chat_type='chat')
        self.sent = []
        self.keyboards = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')
        self.keyboards.append(attachments)


def event(text):
    e = types.SimpleNamespace()
    e.message = Msg(text)
    e.bot = None
    e.callback = types.SimpleNamespace(
        user=types.SimpleNamespace(user_id=100, full_name='Андрей'))
    return e


@pytest.fixture
def schyotchik():
    dom = next(h for h in houses.ALL_HOUSES if h['address'] == '4-я Советская 28')
    return db.add_meter(dom['id'], 'hvs_office', 'Пульсар: 28', 'Андрей')


async def test_zayavka_v_chate_nichego_ne_zapisyvaet(schyotchik):
    db.add_reading(schyotchik, 6915.52, '2026-07', 1, 'Андрей')
    e = event(SLUCHAY)

    razobrano = await H.handle_readings(e, SLUCHAY, 100)

    assert razobrano is False, 'это заявка, пусть идёт своим путём'
    assert len(db.meter_readings(schyotchik)) == 1, 'ничего не записано'


async def test_pokazanie_menshe_proshlogo_ne_pishetsya_molcha(schyotchik):
    """Счётчики назад не крутятся: либо ошиблись цифрой, либо это не показание."""
    db.add_reading(schyotchik, 6915.52, '2026-07', 1, 'Андрей')
    text = '4-я Советская 28 офисный 28'
    e = event(text)

    await H.handle_readings(e, text, 100)

    assert len(db.meter_readings(schyotchik)) == 1, 'не записано без подтверждения'
    assert 'меньше прошлого' in e.message.sent[-1]
    payloads = [b.payload for row in e.message.keyboards[-1][0].payload.buttons
                for b in row]
    assert payloads[0] == f'mtyes:{schyotchik}:28'


async def test_podtverzhdyonnoe_pokazanie_zapisyvaetsya(schyotchik):
    db.add_reading(schyotchik, 6915.52, '2026-07', 1, 'Андрей')
    e = event('')

    await H.run_action(f'mtyes:{schyotchik}:28', e.message, 100, e)

    assert len(db.meter_readings(schyotchik)) == 2, 'человек подтвердил — пишем'


async def test_oshibochnoe_pokazanie_mozhno_udalit(schyotchik):
    db.add_reading(schyotchik, 6915.52, '2026-07', 1, 'Андрей')
    plohoe = db.add_reading(schyotchik, 28, '2026-08', 1, 'Андрей')

    e = event('')
    await H.run_action(f'mtdel2:{schyotchik}:{plohoe}', e.message, 100, e)

    ostalos = db.meter_readings(schyotchik)
    assert [r['value'] for r in ostalos] == [6915.52]


async def test_v_istorii_est_knopka_udaleniya(schyotchik):
    db.add_reading(schyotchik, 6915.52, '2026-07', 1, 'Андрей')
    e = event('')

    await H.run_action(f'mth:{schyotchik}', e.message, 100, e)

    payloads = [b.payload for row in e.message.keyboards[-1][0].payload.buttons
                for b in row]
    assert f'mtdel:{schyotchik}' in payloads
