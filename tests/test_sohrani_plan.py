"""«Люся, это план работ. Сохрани» — и она правда сохраняет.

Костя прислал в чат план работ по тепловым пунктам. Заказчик попросил
сохранить — Люся ответила, что не умеет, и была права: инструментов на
запись у неё нет. Заодно этот же план она приняла за показание счётчика
ГВС: в тексте было «насоса ГВС. 14», и число из другого предложения
уехало в учёт.
"""
import types

import pytest

from bot import db, houses, plan
import bot.handlers as H

PLAN = ('65/2 ремонт (протяжка) теплообменника отопления. '
        'Академия: 126/3 замена косого фильтра обратки отопления ⌀100, '
        'ремонт (замена прокладки корпуса) насоса отопления. '
        '126/1 ремонт (замена прокладки корпуса) насоса ГВС.')


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


# ---------- Больше не показания ----------

def test_plan_rabot_ne_pokazaniya():
    """«насоса ГВС. 14» — это дом 14 в следующем предложении, а не показание."""
    assert H.parse_readings(PLAN + ' 14, 65/4, 22 дом гидравлика держит')[1] == []


@pytest.mark.parametrize('stroka, pary', [
    ('Седова 71 хвс 1234', [('hvs', 1234.0)]),
    ('Седова 71 хвс 1234, гвс 567', [('hvs', 1234.0), ('gvs', 567.0)]),
    ('хвс — 1234', [('hvs', 1234.0)]),
    ('тепло 1890', [('heat', 1890.0)]),
])
def test_nastoyaschie_pokazaniya_rabotayut(stroka, pary):
    assert H.parse_readings(stroka)[1] == pary


def test_chislo_cherez_predlozhenie_ne_beryotsya():
    assert H.parse_readings('поменяли насос ГВС. 14 дом следующий')[1] == []


def test_chislo_daleko_ot_vida_ne_beryotsya():
    assert H.parse_readings('хвс в подвале у дальней стены 1234')[1] == []


# ---------- Разбор плана ----------

def test_plan_uznayotsya_po_vidu():
    assert plan.looks_like_plan(PLAN) is True


@pytest.mark.parametrize('ne_plan', [
    'привет всем',
    'буду в 14 на Седова',
    'ок',
])
def test_boltovnya_planom_ne_schitaetsya(ne_plan):
    assert plan.looks_like_plan(ne_plan) is False


async def test_punkty_razbirayutsya_i_privyazyvayutsya_k_domam(monkeypatch):
    async def fake_ask(prompt, **kw):
        return '''{"пункты": [
            {"адрес": "65/2", "работа": "Ремонт (протяжка) теплообменника отопления"},
            {"адрес": "126/3", "работа": "Замена косого фильтра обратки ⌀100"},
            {"адрес": "", "работа": "Осмотреть гидравлику"}
        ]}'''
    monkeypatch.setattr(plan.ai, 'ask', fake_ask)

    punkty = await plan.parse_plan(PLAN)

    assert len(punkty) == 3
    assert punkty[0]['house']['address'] == 'Седова 65а/2'
    assert punkty[1]['house']['address'] == 'Байкальская 126/3'
    assert punkty[2]['house'] is None, 'адрес не назван — так и оставляем'


async def test_pustaya_rabota_propuskaetsya(monkeypatch):
    async def fake_ask(prompt, **kw):
        return '{"пункты": [{"адрес": "65/2", "работа": ""}]}'
    monkeypatch.setattr(plan.ai, 'ask', fake_ask)

    assert await plan.parse_plan(PLAN) == []


async def test_v_zadanii_zapret_dodumyvat(monkeypatch):
    zadacha = {}

    async def fake_ask(prompt, **kw):
        zadacha['text'] = prompt
        zadacha['temp'] = kw.get('temperature')
        return '{"пункты": []}'
    monkeypatch.setattr(plan.ai, 'ask', fake_ask)

    await plan.parse_plan(PLAN)

    assert 'ничего не добавляй от себя' in zadacha['text']
    assert zadacha['temp'] == 0


# ---------- Сквозной путь ----------

class Msg:
    def __init__(self, text, quoted=None):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=None, chat_id=7, chat_type='chat')
        self.sent = []
        self.keyboards = []
        self.link = types.SimpleNamespace(
            type='reply', sender=types.SimpleNamespace(user_id=555),
            message=types.SimpleNamespace(text=quoted)) if quoted else None

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')
        self.keyboards.append(attachments)


def event(text, quoted=None):
    e = types.SimpleNamespace()
    e.message = Msg(text, quoted)
    e.bot = None
    e.callback = types.SimpleNamespace(
        user=types.SimpleNamespace(user_id=100, full_name='Андрей'))
    return e


@pytest.fixture
def razbor(monkeypatch):
    async def fake_ask(prompt, **kw):
        return '''{"пункты": [
            {"адрес": "65/2", "работа": "Ремонт теплообменника отопления"},
            {"адрес": "126/3", "работа": "Замена косого фильтра обратки"}
        ]}'''
    monkeypatch.setattr(plan.ai, 'ask', fake_ask)


async def test_sohrani_pokazyvaet_razbor_do_zapisi(razbor):
    e = event('Люся, это план работ по приёму тепловых пунктов. Сохрани', PLAN)

    vzyala = await H.handle_save_plan(e, 'это план работ. Сохрани', 100)

    assert vzyala is True
    assert 'Седова 65а/2' in e.message.sent[-1]
    assert db.list_works(open_only=False) == [], 'до подтверждения ничего не пишем'


async def test_podtverzhdenie_zapisyvaet_raboty(razbor):
    e = event('Сохрани', PLAN)
    await H.handle_save_plan(e, 'Сохрани', 100)

    c = event('')
    await H.run_action('plansave', c.message, 100, c)

    raboty = db.list_works(open_only=False)
    assert len(raboty) == 2
    assert {w['title'] for w in raboty} == {
        'Ремонт теплообменника отопления', 'Замена косого фильтра обратки'}
    assert all(w['status'] == db.WORK_PLAN for w in raboty)


async def test_otkaz_nichego_ne_pishet(razbor):
    e = event('Сохрани', PLAN)
    await H.handle_save_plan(e, 'Сохрани', 100)

    c = event('')
    await H.run_action('plancancel', c.message, 100, c)

    assert db.list_works(open_only=False) == []
    assert 100 not in H.STATE


async def test_bez_otveta_na_plan_ne_srabatyvaet(razbor):
    """«Сохрани» без ответа на сообщение с планом сохранять нечего."""
    e = event('Сохрани')

    assert await H.handle_save_plan(e, 'Сохрани', 100) is False


async def test_otvet_na_boltovnyu_ne_schitaetsya_planom(razbor):
    e = event('Сохрани', 'да, я тоже так думаю')

    assert await H.handle_save_plan(e, 'Сохрани', 100) is False


def test_bukvu_korpusa_v_rechi_opuskayut():
    """Звено говорит «65/2», в справочнике «Седова 65а/2» — это один дом."""
    assert houses.detect_house('65/2')['address'] == 'Седова 65а/2'
    assert houses.detect_house('65/4')['address'] == 'Седова 65а/4'
    assert houses.detect_house('65а/2')['address'] == 'Седова 65а/2'


def test_bez_bukvy_ischem_tolko_kogda_dom_odin():
    """Послабление не должно превращаться в угадывание."""
    assert houses.detect_house('привезли 8 задвижек') is None
    assert houses.detect_house('поставили 2/3 задвижек') is None
