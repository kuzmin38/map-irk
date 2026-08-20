"""Личный разговор не должен всплывать в рабочем чате.

Случай из жизни: в личке заказчик заводил счётчик ВСХН-40 на 4-й Советской 30.
Через час в рабочем чате он спросил про адрес из видеоотчёта о лопнувшем
вентиле — и Люся ответила ему тем самым счётчиком. Память у неё была одна
на все чаты, а ленту рабочего чата она вообще не видела.
"""
import json
import types

import pytest

from bot import agent, db
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


CHAT = 7


def test_lichnaya_perepiska_ne_vidna_v_rabochem_chate():
    db.add_chat_message(100, 'user', 'счётчик ВСХН-40 № 17337502')
    db.add_chat_message(100, 'assistant', 'Записала: 4-я Советская 30, ВСХН-40')

    v_chate = db.recent_chat_history(100, chat_id=CHAT)

    assert v_chate == [], 'в чате Люся про личную переписку не помнит'


def test_rabochiy_chat_ne_vidno_v_lichke():
    db.add_chat_message(100, 'user', 'какой адрес?', chat_id=CHAT)
    db.add_chat_message(100, 'assistant', 'адрес в отчёте не назван', chat_id=CHAT)

    v_lichke = db.recent_chat_history(100)

    assert v_lichke == []


def test_pamyat_kazhdogo_chata_svoya():
    db.add_chat_message(100, 'user', 'личное', chat_id=None)
    db.add_chat_message(100, 'user', 'из чата 7', chat_id=7)
    db.add_chat_message(100, 'user', 'из чата 9', chat_id=9)

    assert [m['content'] for m in db.recent_chat_history(100)] == ['личное']
    assert [m['content'] for m in db.recent_chat_history(100, chat_id=7)] == ['из чата 7']
    assert [m['content'] for m in db.recent_chat_history(100, chat_id=9)] == ['из чата 9']


# ---------- Лента чата как источник ответа ----------

def test_lenta_chata_otdayotsya_s_rasshifrovkoy():
    rid = db.add_chat_record(CHAT, 'm1', 100, 'Виталя', None, has_files=True)
    db.set_chat_transcript(rid, 'Вентиль 32 мм лопнул, требуется замена')

    otvet = json.loads(agent._tool_chat_reports(CHAT))

    zapis = otvet['сообщения'][0]
    assert zapis['расшифровка'] == 'Вентиль 32 мм лопнул, требуется замена'
    assert zapis['кто'] == 'Виталя'
    assert zapis['дом'] is None, 'адреса в отчёте не было'


def test_pro_nenazvannyy_adres_skazano_pryamo():
    """Иначе модель подставит дом из прошлого разговора — так и вышло."""
    rid = db.add_chat_record(CHAT, 'm1', 100, 'Виталя', None, has_files=True)
    db.set_chat_transcript(rid, 'Вентиль лопнул')

    otvet = json.loads(agent._tool_chat_reports(CHAT))

    assert 'не назван' in otvet['подсказка']


def test_lenta_chuzhogo_chata_ne_vidna():
    db.add_chat_record(9, 'm1', 100, 'Кто-то', 'сообщение другого чата')

    otvet = json.loads(agent._tool_chat_reports(CHAT))

    assert otvet['сообщения'] == []


def test_v_lichke_lenty_net():
    otvet = json.loads(agent._tool_chat_reports(None))

    assert 'error' in otvet


def test_instrument_lenty_est_u_modeli():
    imena = [t['function']['name'] for t in agent.TOOLS]

    assert 'get_chat_reports' in imena
    assert 'get_chat_reports' in agent.TOOL_FUNCS


# ---------- Сквозной путь ----------

class Msg:
    def __init__(self, text):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=None, chat_id=CHAT, chat_type='chat')
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text)


def event(text):
    e = types.SimpleNamespace()
    e.message = Msg(text)
    e.bot = None
    return e


async def test_vopros_iz_chata_uhodit_k_agentu_s_nomerom_chata(monkeypatch):
    poluchennoe = {}

    async def fake_answer(uid, name, text, chat_id=None):
        poluchennoe['chat_id'] = chat_id
        return 'ответ'

    monkeypatch.setattr(H.agent, 'answer', fake_answer)

    await H.on_text(event('Люся, какой адрес в отчёте?'))

    assert poluchennoe['chat_id'] == CHAT, 'иначе Люся ответит по личной памяти'


async def test_instrument_lenty_beryot_tot_zhe_chat(monkeypatch):
    """Номер чата модель не называет — он берётся из разговора."""
    rid = db.add_chat_record(CHAT, 'm1', 100, 'Виталя', None, has_files=True)
    db.set_chat_transcript(rid, 'Вентиль 32 мм лопнул')
    vyzvano = {}

    async def fake_chat(messages, tools=None):
        if 'tool' not in vyzvano:
            vyzvano['tool'] = True
            return {'role': 'assistant', 'tool_calls': [
                {'id': '1', 'function': {'name': 'get_chat_reports', 'arguments': '{}'}}]}
        vyzvano['lenta'] = messages[-1]['content']
        return {'role': 'assistant', 'content': 'В отчёте адрес не назван — подскажи, какой дом?'}

    monkeypatch.setattr(agent.ai, 'enabled', lambda: True)
    monkeypatch.setattr(agent.ai, 'chat', fake_chat)

    otvet = await agent.answer(100, 'Андрей', 'какой адрес?', chat_id=CHAT)

    assert 'Вентиль 32 мм лопнул' in vyzvano['lenta']
    assert 'не назван' in otvet
