"""Сезонные работы: то, что делается каждый год в одно и то же время.

Заказчик: «на зиму нужно открывать краны на перемычке между ливнёвой и
домовой канализацией, на лето закрывать; виброставки просматривать. Такие
работы есть, нужно постоянно что-то открывать, закрывать, смотреть. Лучше
в плановые работы всё это вносить и чтобы напоминание было от Люси, в какое
число открыть ту задвижку».

Отдельного вида работ не заводили: сезонная запись — это правило, по
которому раз в год сама собой появляется обычная кампания с работами по
домам. Всё остальное — сроки, ответственные, «сдано», прогресс — уже было.
"""
from datetime import date

import pytest

from bot import db, houses, reminders, sezon
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


# ── разбор фразы ────────────────────────────────────────────────────────

def test_frazu_zakazchika_razbiraet():
    r = sezon.parse('сезонная работа: 15 октября открыть краны на перемычке '
                    'между ливнёвой и домовой канализацией')
    assert r['day'] == 15 and r['month'] == 10
    assert r['title'].startswith('Открыть краны на перемычке')
    assert 'октября' not in r['title'], 'дата в название не лезет'
    assert r['lead_days'] == 7


def test_data_tsiframi_i_srok_predupezhdeniya():
    r = sezon.parse('ежегодно 20.05 просмотреть виброставки, за две недели')
    assert (r['day'], r['month']) == (20, 5)
    assert r['lead_days'] == 14
    assert r['title'] == 'Просмотреть виброставки'


def test_ohvat_po_zhk():
    r = sezon.parse('каждый год 1 апреля закрыть перемычку по ЖК Квартал')
    assert r['complex_name'] == 'Квартал'
    assert 'Квартал' not in r['title']


def test_bez_chisla_data_pustaya():
    """Без числа напоминать не о чем — Люся спросит, а не придумает."""
    r = sezon.parse('сезонная работа: проверить задвижки')
    assert r['month'] is None


def test_trigger_ne_lovit_obychnye_frazy():
    assert not sezon.TRIGGER.match('перекрыл стояк по 105 квартире')
    assert not sezon.TRIGGER.match('в инвентарь: мотопомпа')
    assert sezon.TRIGGER.match('сезонная работа: 1 мая открыть краны')


# ── когда срабатывает ───────────────────────────────────────────────────

def zapis(**kw):
    pole = {'active': 1, 'month': 10, 'day': 15, 'lead_days': 7,
            'last_year': None}
    pole.update(kw)
    return pole


def test_srabatyvaet_za_nedelyu_do():
    assert sezon.pora(zapis(), date(2026, 10, 8))
    assert not sezon.pora(zapis(), date(2026, 10, 7))


def test_v_god_tolko_odin_raz():
    assert not sezon.pora(zapis(last_year=2026), date(2026, 10, 14))
    assert sezon.pora(zapis(last_year=2026), date(2027, 10, 14))


def test_priostanovlennaya_ne_srabatyvaet():
    assert not sezon.pora(zapis(active=0), date(2026, 10, 14))


def test_bez_daty_ne_srabatyvaet():
    assert not sezon.pora(zapis(month=None, day=None), date(2026, 10, 14))


def test_29_fevralya_v_nevisokosnyy_god():
    assert sezon.v_godu(2027, 2, 29) == date(2027, 2, 28)
    assert sezon.v_godu(2028, 2, 29) == date(2028, 2, 29)


# ── работы заводятся сами ───────────────────────────────────────────────

class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, user_id=None, chat_id=None, text=None, **kw):
        self.sent.append(text)


async def test_zavodit_raboty_po_vsem_domam():
    db.upsert_user(1, 'Андрей')
    db.set_user_role(1, 'admin')
    sid = db.add_seasonal('Открыть краны на перемычке', 10, 15, 7,
                          sezon.VSE_DOMA, 1, 'Андрей')
    bot = FakeBot()

    await reminders._check_seasonal(bot, date(2026, 10, 10))

    zhilyh = [h for h in houses.HOUSES if h.get('kind') != 'nonres']
    raboty = db.list_works(open_only=True, limit=200)
    nashi = [w for w in raboty if w['title'] == 'Открыть краны на перемычке']
    assert len(nashi) == len(zhilyh)
    assert all(w['deadline'] == '2026-10-15' for w in nashi)
    assert db.get_seasonal(sid)['last_year'] == 2026
    assert any('СЕЗОННАЯ РАБОТА' in t for t in bot.sent)


async def test_v_nezhiloy_dom_rabotu_ne_zavodit():
    """Открывать краны на перемычке в парковке никто не пойдёт."""
    db.add_seasonal('Открыть краны', 10, 15, 7, sezon.VSE_DOMA, 1, 'Андрей')
    await reminders._check_seasonal(FakeBot(), date(2026, 10, 10))

    parkovka = next(h['id'] for h in houses.HOUSES
                    if h['address'] == '4-я Советская 26')
    assert not [w for w in db.list_works(open_only=True, limit=200)
                if w['house_id'] == parkovka]


async def test_vtoroy_raz_za_god_ne_zavodit():
    db.add_seasonal('Открыть краны', 10, 15, 7, sezon.VSE_DOMA, 1, 'Андрей')
    bot = FakeBot()
    await reminders._check_seasonal(bot, date(2026, 10, 10))
    bylo = len(db.list_works(open_only=True, limit=200))

    await reminders._check_seasonal(bot, date(2026, 10, 11))

    assert len(db.list_works(open_only=True, limit=200)) == bylo


async def test_progress_schitaetsya_kak_u_lyubogo_zadaniya():
    """Ради этого и переиспользовали кампании: «17 из 25» бесплатно."""
    db.add_seasonal('Открыть краны', 10, 15, 7, sezon.VSE_DOMA, 1, 'Андрей')
    await reminders._check_seasonal(FakeBot(), date(2026, 10, 10))

    camp = db.list_campaigns()[0]
    sdelano, vsego = db.campaign_progress(camp['id'])
    assert sdelano == 0 and vsego > 10
    nasha = [w for w in db.list_works(open_only=True, limit=200)
             if w['campaign_id'] == camp['id']][0]
    db.update_work(nasha['id'], status=db.WORK_DONE)
    assert db.campaign_progress(camp['id'])[0] == 1


# ── запись из чата ──────────────────────────────────────────────────────

class FakeMsg:
    def __init__(self):
        self.recipient = type('R', (), {'chat_id': -1})()
        self.body = type('B', (), {'mid': 'm1', 'attachments': None})()
        self.sender = type('S', (), {'full_name': 'Андрей', 'user_id': 7})()


class FakeEvent:
    def __init__(self):
        self.message = FakeMsg()
        self.bot = FakeBot()


@pytest.fixture
def otvety(monkeypatch):
    poslano = []

    async def fake_send(msg, text, kb=None, **kw):
        poslano.append(text)

    monkeypatch.setattr(H, 'send', fake_send)
    return poslano


async def test_zapis_iz_chata(otvety):
    ok = await H.handle_seasonal(
        FakeEvent(), 'сезонная работа: 15 октября открыть краны на перемычке', 7)
    assert ok
    pravila = db.list_seasonal()
    assert len(pravila) == 1
    assert pravila[0]['month'] == 10 and pravila[0]['day'] == 15
    assert 'Записала' in otvety[-1] and '15 октября' in otvety[-1]


async def test_bez_chisla_perespashivaet(otvety):
    await H.handle_seasonal(FakeEvent(), 'сезонная работа: проверить задвижки', 7)
    assert 'какого числа' in otvety[-1]
    assert db.list_seasonal() == []
    assert H.STATE[7]['mode'] == 'sez_date'

    await H.resume_seasonal_date(FakeEvent(), '1 апреля', 7, H.STATE[7])
    pravila = db.list_seasonal()
    assert len(pravila) == 1
    assert (pravila[0]['day'], pravila[0]['month']) == (1, 4)
    assert pravila[0]['title'] == 'Проверить задвижки'


async def test_neznakomyy_zhk_ne_prohodit_molcha(otvety):
    await H.handle_seasonal(
        FakeEvent(), 'сезонная работа: 1 мая проверить по ЖК Ромашка', 7)
    assert 'не нашла' in otvety[-1]
    assert db.list_seasonal()[0]['complex_id'] == sezon.VSE_DOMA


async def test_znakomyy_zhk_zapisyvaetsya(otvety):
    await H.handle_seasonal(
        FakeEvent(), 'сезонная работа: 1 мая проверить по ЖК Квартал', 7)
    assert 'не нашла' not in otvety[-1]
    assert db.list_seasonal()[0]['complex_id'] == 'kvartal'
