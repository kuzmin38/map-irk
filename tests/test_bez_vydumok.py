"""Люся пересказывает отчёт словами сантехника, а не своими.

Заказчик сказал в видео «подтапливает по стояку». В чате появилось
«Устранена течь в потолке — заменена неисправная арматура на стояке ГВС
сверху». Модель достроила историю до складной: назвала причину, работы и
результат, которых не было. В рабочем чате такая выдумка опаснее длинного
текста — по ней принимают решения.
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


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id=None, text=None, link=None, attachments=None):
        self.sent.append(text)


async def test_korotkiy_otchyot_idyot_doslovno(monkeypatch):
    """Пересказывать одну фразу незачем — а испортить её можно."""
    zvali = {}

    async def fake_ask(prompt, **kw):
        zvali['да'] = True
        return 'Устранена течь в потолке, заменена арматура на стояке ГВС.'
    monkeypatch.setattr(H.ai, 'ask', fake_ask)

    text = await H.short_summary(['Подтапливает по стояку, разбираемся'],
                                 'Трилиссера 8/5')

    assert 'Подтапливает по стояку, разбираемся' in text
    assert 'арматура' not in text
    assert not zvali, 'модель тут вообще не нужна'


async def test_seriya_rolikov_idyot_shagami(monkeypatch):
    async def fake_ask(prompt, **kw):
        return 'выдумка'
    monkeypatch.setattr(H.ai, 'ask', fake_ask)

    text = await H.short_summary(
        ['Подтапливает по стояку', 'Перекрыли, смотрим выше'], 'Трилиссера 8/5')

    assert '• Подтапливает по стояку' in text
    assert '• Перекрыли, смотрим выше' in text
    assert 'выдумка' not in text


async def test_dlinnyy_otchyot_peresказ_pomechen(monkeypatch):
    """Длинную запись сокращаем — и честно говорим, что это пересказ."""
    async def fake_ask(prompt, **kw):
        return 'Подтапливает по стояку, причину ищут.'
    monkeypatch.setattr(H.ai, 'ask', fake_ask)
    dlinno = 'подтапливает по стояку и вот ещё что скажу ' * 20

    text = await H.short_summary([dlinno], 'Трилиссера 8/5')

    assert 'пересказ' in text
    assert '/chat' in text, 'дословное всегда можно поднять'


async def test_modeli_zapreshcheno_dodumyvat(monkeypatch):
    """Проверяем сам наказ: без него модель дописывает работы и причины."""
    zadacha = {}

    async def fake_ask(prompt, **kw):
        zadacha['text'] = prompt
        zadacha['temp'] = kw.get('temperature')
        return 'коротко'
    monkeypatch.setattr(H.ai, 'ask', fake_ask)

    await H.short_summary(['подтапливает по стояку ' * 40], None)

    assert 'Ничего не добавляй' in zadacha['text']
    assert 'не додумывай' in zadacha['text']
    assert zadacha['temp'] == 0


# ---------- Адрес из подписи к видео ----------

def test_nomer_s_korpusom_v_podpisi_uznayotsya():
    """Подпись «8/5 Салон красоты» — это адрес, других таких чисел не бывает."""
    assert houses.detect_house('8/5 Салон красоты «МИ Студия»')['address'] == 'Трилиссера 8/5'
    assert houses.detect_house('126/1 подвал')['address'] == 'Байкальская 126/1'


def test_podtaplivaet_schitaetsya_avariynym():
    """Слово из отчёта заказчика: раньше Люся не считала его аварийным."""
    assert H.ISSUE_WORDS.search('подтапливает по стояку')


def test_drobi_iz_zhizni_za_adres_ne_prinimayutsya():
    assert houses.detect_house('поставили 2/3 задвижек') is None
    assert houses.detect_house('приеду 8/5 числа') is None


async def test_adres_iz_podpisi_popadaet_v_otchyot(monkeypatch):
    """В речи адреса не было, зато он был в подписи к ролику."""
    monkeypatch.setattr(H, 'SERIES_WINDOW', 0.05)

    async def fake_transcribe(url):
        return 'Подтапливает по стояку, вода идёт сверху'
    monkeypatch.setattr(transcribe, 'transcribe_url', fake_transcribe)

    dom = houses.detect_house('8/5 Салон красоты «МИ Студия»')
    rid = db.add_chat_record(7, 'm1', 100, 'Андрей', '8/5 Салон красоты «МИ Студия»',
                             house_id=dom['id'], has_files=True)
    bot = FakeBot()

    await H.transcribe_later(rid, 'http://x/v.mp4', bot=bot, chat_id=7, mid='m1')
    await asyncio.sleep(0.3)

    assert db.get_chat_record(rid)['house_id'] == dom['id']
    assert bot.sent and 'Трилиссера 8/5' in bot.sent[0]
    assert 'Адрес не назвали' not in bot.sent[0]
