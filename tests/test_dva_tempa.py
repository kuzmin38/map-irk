"""Два темпа: быстрые рефлексы днём и медленный разбор вечером.

Заказчик: «надо тебе мониторить чат, а не Люсе. Как её сделать такой же
умной?» Ответ оказался не в модели: днём она видела одно сообщение в
отрыве от разговора, а разбирать день целиком было некому.
"""
import json

import pytest

from bot import agent, db, houses, razbor


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


def zapis(text, name='Костя', house=None, transcript=None):
    return db.add_chat_record(chat_id=7, mid='m', user_id=100, user_name=name,
                              text=text, house_id=house)


# ---------- Контекст: что вокруг ----------

def test_lenta_chata_vidna_agentu():
    dom = houses.detect_house('Седова 71')
    zapis('Поменял задвижку', 'Костя', dom['id'])
    zapis('Спасибо', 'Маша')

    blok = agent._chat_context_block(7)

    assert 'Костя' in blok and 'задвижку' in blok
    assert 'Седова 71' in blok, 'дом виден рядом с репликой'
    assert 'Маша' in blok


def test_bez_chata_konteksta_net():
    """В личке лента общего чата ни при чём."""
    assert agent._chat_context_block(None) == ''


def test_staroe_v_kontekst_ne_lezet():
    zapis('Давняя реплика')
    with db._conn() as c:
        c.execute("UPDATE chat_messages SET created_at = '01.01.2026 10:00'")

    assert db.chat_context(7) == []


async def test_lenta_uhodit_modeli(monkeypatch):
    zapis('Поменял задвижку на 71')
    vidno = {}

    async def fake_chat(messages, tools=None, **kw):
        vidno['system'] = messages[0]['content']
        return {'role': 'assistant', 'content': 'поняла'}

    monkeypatch.setattr(agent.ai, 'chat', fake_chat)
    monkeypatch.setattr(agent.ai, 'enabled', lambda: True)

    await agent.answer(100, 'Костя', 'что у нас нового', chat_id=7)

    assert 'ЧТО СЕЙЧАС В ЧАТЕ' in vidno['system']
    assert 'задвижку' in vidno['system']


# ---------- Разбор дня ----------

def test_lenta_za_den_sobiraetsya():
    dom = houses.detect_house('Седова 71')
    zapis('Поменял задвижку', 'Костя', dom['id'])
    den = db.now()[:10]

    lenta = razbor.lenta_za_den(den)

    assert 'Костя' in lenta and 'задвижку' in lenta
    assert 'Седова 71' in lenta


async def test_razbor_ne_zovyot_model_na_pustoy_lente(monkeypatch):
    zvali = []

    async def fake_ask(*a, **kw):
        zvali.append(1)
        return '{}'

    monkeypatch.setattr(razbor.ai, 'ask', fake_ask)

    itog = await razbor.razobrat_den(db.now()[:10])

    assert itog == {'дома': [], 'повисло': []}
    assert zvali == [], 'на пустой день денег не тратим'


async def test_razbor_kladyot_fakty_v_hroniku(monkeypatch):
    dom = houses.detect_house('Седова 71')
    for i in range(6):
        zapis(f'Работали на Седова 71, заменили участок розлива номер {i}',
              'Костя', dom['id'])
    den = db.now()[:10]

    async def fake_ask(prompt, **kw):
        assert 'Седова 71' in prompt, 'лента уходит модели'
        return json.dumps({'дома': [{'адрес': 'Седова 71', 'факты': [
            {'что': 'Заменён участок розлива', 'вид': 'работа'}]}],
            'повисло': ['Не закрыта заявка по 65а/2']}, ensure_ascii=False)

    monkeypatch.setattr(razbor.ai, 'ask', fake_ask)

    itog = await razbor.razobrat_den(den)
    zapisano = razbor.sohranit(den, itog)

    assert zapisano == 1
    fakty = db.house_facts(dom['id'])
    assert fakty[0]['text'] == 'Заменён участок розлива'
    assert fakty[0]['kind'] == 'работа'
    assert 'Не закрыта заявка' in razbor.svodka(den, itog)


def test_neopoznannyy_dom_propuskaem():
    """Лучше потерять факт, чем приписать его чужому дому."""
    itog = {'дома': [{'адрес': 'Улица которой нет 5',
                      'факты': [{'что': 'что-то было'}]}]}

    assert razbor.sohranit(db.now()[:10], itog) == 0


def test_den_razbirayut_odin_raz():
    dom = houses.detect_house('Седова 71')
    den = razbor._iso(db.now()[:10])
    assert db.day_already_parsed(den) is False

    db.add_house_fact(dom['id'], den, 'Что-то сделали')

    assert db.day_already_parsed(den) is True


def test_hronika_vidna_v_pasporte():
    import bot.handlers as H
    dom = houses.detect_house('Седова 71')
    db.add_house_fact(dom['id'], '2026-09-01', 'Заменён участок розлива', 'работа')

    text = H.passport_text(dom)

    assert 'ХРОНИКА ДОМА' in text
    assert 'Заменён участок розлива' in text
    assert '01.09' in text


# ---------- Разбор по команде ----------

import types


class Msg:
    def __init__(self, text=''):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=100, chat_id=None, chat_type='dialog')
        self.sent = []
        self.link = None

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')


def sobytie():
    e = types.SimpleNamespace()
    e.message = Msg()
    e.bot = None
    e.callback = types.SimpleNamespace(
        user=types.SimpleNamespace(user_id=100, full_name='Андрей'))
    return e


async def test_komanda_itogi_est_v_menyu():
    import bot.handlers as H
    payload = next((p for name, _, p in H.QUICK_COMMANDS if name == 'итоги'), None)
    assert payload == 'itogi'


async def test_razbor_po_komande_pishet_hroniku(monkeypatch):
    import bot.handlers as H
    dom = houses.detect_house('Седова 71')
    db.upsert_user(100, 'Андрей')          # первый пользователь — админ
    for i in range(6):
        zapis(f'На Седова 71 заменили участок розлива, кусок {i}', 'Костя', dom['id'])

    async def fake_ask(prompt, **kw):
        return json.dumps({'дома': [{'адрес': 'Седова 71', 'факты': [
            {'что': 'Заменён участок розлива', 'вид': 'работа'}]}]}, ensure_ascii=False)

    monkeypatch.setattr(razbor.ai, 'ask', fake_ask)

    msg = Msg()
    await H.run_action('itogi', msg, 100, sobytie())

    assert 'Заменён участок розлива' in msg.sent[-1]
    assert db.house_facts(dom['id'])[0]['text'] == 'Заменён участок розлива'


async def test_uzhe_razobrannyy_den_model_ne_dyorgaet(monkeypatch):
    import bot.handlers as H
    dom = houses.detect_house('Седова 71')
    db.upsert_user(100, 'Андрей')
    db.add_house_fact(dom['id'], razbor._iso(db.now()[:10]), 'Заменён розлив', 'работа')

    zvali = []

    async def fake_ask(*a, **kw):
        zvali.append(1)
        return '{}'

    monkeypatch.setattr(razbor.ai, 'ask', fake_ask)

    msg = Msg()
    await H.run_action('itogi', msg, 100, sobytie())

    assert zvali == [], 'второй раз за тот же день денег не тратим'
    assert 'Заменён розлив' in msg.sent[-1]


async def test_bez_roli_itogi_ne_sobirayutsya():
    import bot.handlers as H
    db.upsert_user(100, 'Андрей')          # админ
    db.upsert_user(200, 'Костя')           # без роли

    msg = Msg()
    await H.run_action('itogi', msg, 200, sobytie())

    assert 'для руководства' in msg.sent[-1]
