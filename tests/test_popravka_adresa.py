"""Поправка адреса в чате должна менять запись, а не только ответ Люси.

Заказчик написал «Не 28 дом, а 18 б !!!». Люся ответила «Записала» — и это
была неправда: все её инструменты работают на чтение, изменить привязку
отчёта она не могла. Человек ушёл уверенный, что дело сделано.
"""
import asyncio
import types

import pytest

from bot import agent, db, houses
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


CHAT = 7


@pytest.mark.parametrize('popravka, adres', [
    ('Не 28 дом, а 18 б !!!', 'Трилиссера 18б'),
    ('Не 28, а 18б', 'Трилиссера 18б'),
    ('это не Седова 71, а Трилиссера 8/5', 'Трилиссера 8/5'),
    ('не тот адрес, а 4-я Советская 30', '4-я Советская 30'),
])
def test_popravka_uznayotsya(popravka, adres):
    assert H.parse_correction(popravka)['address'] == adres


@pytest.mark.parametrize('ne_popravka', [
    'не знаю, а ты?',
    'поменяли не кран, а вентиль',
    'сегодня не успеваем, а завтра с утра',
])
def test_obychnaya_rech_popravkoy_ne_schitaetsya(ne_popravka):
    assert H.parse_correction(ne_popravka) is None


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


def test_popravka_menyaet_dom_u_otchyota():
    """Тот самый случай целиком."""
    ne_tot = houses.detect_house('4-я Советская 28')
    otchyot = db.add_chat_record(CHAT, 'v1', 100, 'Андрей', '18 б - 78',
                                 house_id=ne_tot['id'], has_files=True)

    H.record_chat_message(event('Не 28 дом, а 18 б !!!'), 'Не 28 дом, а 18 б !!!')

    nuzhnyy = houses.detect_house('Трилиссера 18б')
    assert db.get_chat_record(otchyot)['house_id'] == nuzhnyy['id']


async def test_lusya_govorit_chto_imenno_pravila():
    ne_tot = houses.detect_house('4-я Советская 28')
    db.add_chat_record(CHAT, 'v1', 100, 'Андрей', '18 б - 78',
                       house_id=ne_tot['id'], has_files=True)
    e = event('Не 28 дом, а 18 б !!!')

    H.fix_report_house(e, 999, 'Не 28 дом, а 18 б !!!')
    await asyncio.sleep(0)

    otvet = e.message.sent[-1]
    assert 'Трилиссера 18б' in otvet and '4-я Советская 28' in otvet


def test_chuzhoy_otchyot_ne_pravitsya():
    """Правим то, что прислал сам поправляющий, а не сосед по чату."""
    ne_tot = houses.detect_house('4-я Советская 28')
    chuzhoy = db.add_chat_record(CHAT, 'v1', 999, 'Константин', 'видео',
                                 house_id=ne_tot['id'], has_files=True)

    H.record_chat_message(event('Не 28 дом, а 18 б'), 'Не 28 дом, а 18 б')

    assert db.get_chat_record(chuzhoy)['house_id'] == ne_tot['id']


def test_starye_otchyoty_ne_trogaem(monkeypatch):
    ne_tot = houses.detect_house('4-я Советская 28')
    staryy = db.add_chat_record(CHAT, 'v1', 100, 'Андрей', 'видео',
                                house_id=ne_tot['id'], has_files=True)
    with db._conn() as c:
        c.execute("UPDATE chat_messages SET created_at = '01.01.2020 10:00' WHERE id = ?",
                  (staryy,))

    H.record_chat_message(event('Не 28 дом, а 18 б'), 'Не 28 дом, а 18 б')

    assert db.get_chat_record(staryy)['house_id'] == ne_tot['id']


def test_v_podskazke_zapreschено_obeschat_zapis():
    """Пока инструментов на запись нет, обещать запись нельзя."""
    p = agent._build_prompt()

    assert 'ТОЛЬКО СМОТРЕТЬ' in p
    assert 'Никогда не пиши «записала»' in p


def test_u_agenta_deystvitelno_net_instrumentov_zapisi():
    """Если однажды появятся — надо будет снять и запрет в подсказке."""
    zapis = [n for n in agent.TOOL_FUNCS
             if n.startswith(('set_', 'add_', 'update_', 'delete_', 'create_'))]

    assert zapis == []
