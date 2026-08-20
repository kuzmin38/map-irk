"""Адрес, названный в видеоотчёте, должен доезжать до записи.

Заказчик: «адрес в видео был назван» — а Люся подписала отчёт просто
«🎙 Видеоотчёт», без дома. Разбор адреса в живой речи не понимал ни
«корпус», ни номер дома без улицы.
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


@pytest.mark.parametrize('rech, adres', [
    ('Байкальская 126 корпус 1, течь в подвале', 'Байкальская 126/1'),
    ('Байкальская сто двадцать шесть корпус два', 'Байкальская 126/2'),
    ('Трилиссера 8 к.3, меняем вентиль', 'Трилиссера 8/3'),
    ('Седова 65а корпус 2, квартира 47', 'Седова 65а/2'),
])
def test_korpus_v_zhivoy_rechi(rech, adres):
    """В справочнике «126/1», а вслух говорят «корпус один»."""
    assert houses.detect_house(rech)['address'] == adres


@pytest.mark.parametrize('rech', [
    'Тридцатый дом, вентиль 32 мм лопнул',
    'на тридцатом доме лопнул вентиль',
    'дом 30, требуется замена вентиля',
    'Четыре солнца, тридцатый дом',
])
def test_nomer_doma_bez_ulitsy(rech):
    """Номер среди наших домов единственный — улицу называть незачем."""
    assert houses.detect_house(rech)['address'] == '4-я Советская 30'


@pytest.mark.parametrize('rech', [
    'Вентиль 32 мм лопнул, требуется замена',
    'привезли 8 задвижек и 30 манометров',
    'поменяли 3 крана в подвале',
    'Четыре солнца, вентиль 32 мм лопнул',
])
def test_sluchaynye_chisla_za_adres_ne_prinimayutsya(rech):
    """«32 мм» — это диаметр, а не дом. Лучше спросить, чем угадать."""
    assert houses.detect_house(rech) is None


# ---------- Если адрес всё же не назвали ----------

async def test_bez_adresa_lusya_sprashivaet(monkeypatch):
    async def fake_ask(*a, **kw):
        return 'Лопнул вентиль 32 мм, требуется замена.'
    monkeypatch.setattr(H.ai, 'ask', fake_ask)

    text = await H.short_summary(['вентиль лопнул'], None)

    assert 'Адрес не назвали' in text


async def test_s_adresom_nichego_ne_sprashivaet(monkeypatch):
    async def fake_ask(*a, **kw):
        return 'Лопнул вентиль 32 мм.'
    monkeypatch.setattr(H.ai, 'ask', fake_ask)

    text = await H.short_summary(['вентиль лопнул'], '4-я Советская 30')

    assert 'Адрес не назвали' not in text
    assert '4-я Советская 30' in text


# ---------- Ответ на вопрос цепляется к отчёту ----------

CHAT = 7


class Msg:
    def __init__(self, text, files=False):
        self.body = types.SimpleNamespace(
            text=text, attachments=[1] if files else None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=None, chat_id=CHAT, chat_type='chat')
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text)


def event(text, files=False):
    e = types.SimpleNamespace()
    e.message = Msg(text, files)
    e.bot = None
    return e


def test_korotkiy_otvet_s_adresom_privyazyvaet_otchyot():
    otchyot = db.add_chat_record(CHAT, 'v1', 100, 'Виталя', None, has_files=True)

    H.record_chat_message(event('4-я Советская 30'), '4-я Советская 30')

    assert db.get_chat_record(otchyot)['house_id'] == \
        houses.detect_house('4-я Советская 30')['id']


def test_dlinnaya_fraza_chuzhuyu_zapis_ne_trogaet():
    """Это уже отдельное сообщение, а не ответ на вопрос об адресе."""
    otchyot = db.add_chat_record(CHAT, 'v1', 100, 'Виталя', None, has_files=True)
    text = 'завтра поеду на 4-я Советская 30 смотреть узел с утра'

    H.record_chat_message(event(text), text)

    assert db.get_chat_record(otchyot)['house_id'] is None


def test_otchyot_s_domom_ne_perepisyvaetsya():
    dom = houses.detect_house('Седова 71')
    otchyot = db.add_chat_record(CHAT, 'v1', 100, 'Виталя', None,
                                 house_id=dom['id'], has_files=True)

    H.record_chat_message(event('4-я Советская 30'), '4-я Советская 30')

    assert db.get_chat_record(otchyot)['house_id'] == dom['id']
