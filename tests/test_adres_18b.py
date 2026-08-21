"""Адрес с буквой корпуса: «18 б - 78» — это Трилиссера 18б, квартира 78.

Заказчик подписал видео «18 б - 78», а Люся сохранила отчёт на 4-ю
Советскую 28. Две ошибки сразу: букву корпуса через пробел она не
понимала, а не поняв — брала адрес из своего же сообщения, написанного
тремя часами раньше совсем по другому поводу.
"""
import asyncio
import types

import pytest

from bot import db, houses, transcribe
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.SERIES.clear()


@pytest.mark.parametrize('podpis', [
    '18 б - 78',
    '18б',
    '18 Б - 78, течь счётчика ХВС',
    'Трилиссера 18 б',
])
def test_bukva_korpusa_cherez_probel(podpis):
    assert houses.detect_house(podpis)['address'] == 'Трилиссера 18б'


def test_nomer_kvartiry_za_dom_ne_prinimaetsya():
    """«78 квартира» в расшифровке — не адрес: дома 78 у нас нет."""
    assert houses.detect_house('78 квартира, течёт счётчик ХВС') is None


def test_bukva_ne_sklеivaetsya_s_edinitsami():
    assert houses.detect_house('1234 м3 расход за месяц') is None


# ---------- Соседний адрес — только свежий ----------

class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id=None, text=None, link=None, attachments=None):
        self.sent.append(text)


def test_adres_iz_sosednego_soobscheniya_beryotsya():
    dom = houses.detect_house('Трилиссера 18б')
    db.add_chat_record(7, 'm0', 100, 'Андрей', '18 б - 78', house_id=dom['id'])

    assert db.recent_house_of(7, 100) == dom['id']


def test_staryy_adres_ne_podtyagivaetsya(monkeypatch):
    """Тот самый случай: адрес из сообщения трёхчасовой давности."""
    dom = houses.detect_house('4-я Советская 28')
    rid = db.add_chat_record(7, 'm0', 100, 'Андрей', 'Офис Корал Трэвэл 28 дом',
                             house_id=dom['id'])
    with db._conn() as c:
        c.execute("UPDATE chat_messages SET created_at = '01.01.2020 10:00' WHERE id = ?",
                  (rid,))

    assert db.recent_house_of(7, 100) is None


def test_chuzhoy_adres_ne_podtyagivaetsya():
    dom = houses.detect_house('Седова 71')
    db.add_chat_record(7, 'm0', 999, 'Константин', 'Седова 71', house_id=dom['id'])

    assert db.recent_house_of(7, 100) is None


async def test_video_s_podpisyu_18b_uhodit_na_trilissera(monkeypatch):
    monkeypatch.setattr(H, 'SERIES_WINDOW', 0.05)

    async def fake_transcribe(url):
        return '78 квартира. Течёт счётчик ХВС, промочило уже. Не первый день капает.'
    monkeypatch.setattr(transcribe, 'transcribe_url', fake_transcribe)

    # утром человек писал совсем про другой дом
    staroe = db.add_chat_record(7, 'm0', 100, 'Андрей', 'Офис Корал Трэвэл 28 дом',
                                house_id=houses.detect_house('4-я Советская 28')['id'])
    with db._conn() as c:
        c.execute("UPDATE chat_messages SET created_at = '01.01.2020 10:00' WHERE id = ?",
                  (staroe,))

    dom = houses.detect_house('18 б - 78')
    rid = db.add_chat_record(7, 'm1', 100, 'Андрей', '18 б - 78',
                             house_id=dom['id'], has_files=True)
    bot = FakeBot()

    await H.transcribe_later(rid, 'http://x/v.mp4', bot=bot, chat_id=7, mid='m1')
    await asyncio.sleep(0.3)

    assert db.get_chat_record(rid)['house_id'] == dom['id']
    assert bot.sent and 'Трилиссера 18б' in bot.sent[0]
    assert '4-я Советская' not in bot.sent[0]
