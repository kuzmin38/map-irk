"""Стояк в доме, на который шахматки нет.

Из двадцати пяти жилых домов участка шахматки есть на восемнадцать.
На остальные семь — Байкальская 126/1–126/4, Седова 65а/8, Седова 67,
Трилиссера 8/6 — Люся раньше отвечала «нет шахматки или в ней нет
квартиры, напишите в чат сами». Один ответ на два разных случая, и в
обоих человек оставался без готового текста — ради которого всё и делалось.

Теперь случаи разведены: нет квартиры — подсказать диапазон; нет
шахматки — попросить список квартир и оформить текст с ним.
"""
import pytest

from bot import db, houses, risers, stoyak as stoyak_mod
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


class FakeMsg:
    def __init__(self, sent):
        self.sent = sent
        self.recipient = type('R', (), {'chat_id': -1})()
        self.body = type('B', (), {'mid': 'm1', 'attachments': None})()
        self.sender = type('S', (), {'full_name': 'Андрей Кузьмин',
                                     'user_id': 7})()

    async def answer(self, text, attachments=None, link=None, **kw):
        self.sent.append(text)


class FakeEvent:
    def __init__(self):
        self.sent = []
        self.message = FakeMsg(self.sent)
        self.from_user = type('U', (), {'user_id': 7, 'first_name': 'Андрей',
                                        'last_name': '', 'username': 'a'})()


@pytest.fixture
def otvety(monkeypatch):
    poslano = []

    async def fake_send(msg, text, kb=None, **kw):
        poslano.append(text)

    monkeypatch.setattr(H, 'send', fake_send)
    return poslano


def test_sem_domov_bez_shahmatki():
    """Список из шахматок, а не из головы — если добавят, тест это заметит."""
    net = [h['address'] for h in houses.HOUSES
           if h.get('kind') != 'nonres' and not risers.find_blocks(h['address'])]
    assert 'Байкальская 126/1' in net
    assert 'Трилиссера 8/6' in net
    assert '4-я Советская 30' not in net


def test_spisok_kvartir_razbiraetsya():
    assert stoyak_mod.spisok_kvartir('12, 21, 30, 39') == [12, 21, 30, 39]
    assert stoyak_mod.spisok_kvartir('кв 12 21 30') == [12, 21, 30]
    assert stoyak_mod.spisok_kvartir('12, 12, 21') == [12, 21], 'повторы убираем'
    assert stoyak_mod.spisok_kvartir('не помню') == []


async def test_bez_shahmatki_prosit_spisok(otvety):
    event = FakeEvent()
    ok = await H.handle_shutoff(
        event, 'перекрыл стояк по 40 квартире на Байкальской 126/3', 7)
    assert ok
    assert 'Шахматки на' in otvety[-1]
    assert 'Перечислите квартиры' in otvety[-1]
    assert H.STATE[7]['mode'] == 'stoyak_kvartiry'
    assert H.STATE[7]['kvartira'] == 40


async def test_spisok_prevrashchaetsya_v_dva_teksta(otvety):
    event = FakeEvent()
    await H.handle_shutoff(
        event, 'перекрыл стояк по 40 квартире на Байкальской 126/3', 7)
    otvety.clear()
    ok = await H.resume_stoyak_kvartiry(event, '13, 22, 31, 40, 49', 7,
                                        H.STATE[7])
    assert ok
    vse = '\n'.join(otvety)
    assert 'с ваших слов' in vse, 'откуда список — видно сразу'
    assert '13, 22, 31, 40, 49' in vse
    assert 'Перекрыт стояк' in vse       # текст бригаде
    assert 'Управляющая компания' in vse  # текст жильцам
    assert 7 not in H.STATE


async def test_nerazobrannyy_spisok_ne_teryaet_sostoyanie(otvety):
    event = FakeEvent()
    await H.handle_shutoff(
        event, 'перекрыл стояк по 40 квартире на Байкальской 126/3', 7)
    await H.resume_stoyak_kvartiry(event, 'да чёрт его знает', 7, H.STATE[7])
    assert 'Не разобрала номера' in otvety[-1]
    assert H.STATE[7]['mode'] == 'stoyak_kvartiry', 'спрашиваем ещё раз'


async def test_net_kvartiry_v_shahmatke_podskazyvaet_diapazon(otvety):
    """Здесь шахматка есть — значит, ошибка в номере, и это другой ответ."""
    event = FakeEvent()
    await H.handle_shutoff(
        event, 'перекрыл стояк по 999 квартире на 4-я Советская 30', 7)
    assert 'квартиры 999 нет' in otvety[-1]
    assert 'с 1 по 137' in otvety[-1]
    assert 7 not in H.STATE
