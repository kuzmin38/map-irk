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


async def test_otchyot_peredayotsya_kratko_i_po_delu(monkeypatch):
    """Заказчик: «пусть выдаёт свою короткую формулировку, а не слово в слово»."""
    zadacha = {}

    async def fake_ask(prompt, **kw):
        zadacha['text'] = prompt
        zadacha['temp'] = kw.get('temperature')
        return 'Течь в офисе идёт сверху, не из-под угла. Доступ закрыт коробом.'

    monkeypatch.setattr(H.ai, 'ask', fake_ask)

    text = await H.short_summary(
        ['Вот тут вот течь не отсюда, а выше, вот оттуда, туда подлезть '
         'нечем, короб разбирать или менять вообще'], 'Трилиссера 22')

    assert 'Течь в офисе идёт сверху' in text
    assert 'подлезть' not in text, 'живая речь остаётся в базе, а не в чате'
    assert 'Трилиссера 22' in text


async def test_v_zadanii_zapret_dodumyvat(monkeypatch):
    """Пересказ — то самое место, где модель однажды дописала работы."""
    zadacha = {}

    async def fake_ask(prompt, **kw):
        zadacha['text'] = prompt
        zadacha['temp'] = kw.get('temperature')
        return 'коротко'

    monkeypatch.setattr(H.ai, 'ask', fake_ask)

    await H.short_summary(['подтапливает по стояку'], None)

    assert 'Не добавляй ни причин, ни работ' in zadacha['text']
    assert 'не превращай в «устранено»' in zadacha['text']
    assert 'Ругательства не воспроизводи' in zadacha['text']
    assert zadacha['temp'] == 0


async def test_v_zadanii_est_tri_voprosa(monkeypatch):
    """«Где, что и почему» — то, что просил заказчик."""
    zadacha = {}

    async def fake_ask(prompt, **kw):
        zadacha['text'] = prompt
        return 'коротко'

    monkeypatch.setattr(H.ai, 'ask', fake_ask)
    await H.short_summary(['что-то сказал'], None)

    assert 'где именно' in zadacha['text']
    assert 'что обнаружено' in zadacha['text']
    assert 'что предлагается' in zadacha['text']


async def test_seriya_uhodit_v_model_tselikom(monkeypatch):
    """Несколько роликов подряд — один пересказ, а не список кусков."""
    zadacha = {}

    async def fake_ask(prompt, **kw):
        zadacha['text'] = prompt
        return 'Течь по стояку, перекрыли, ищут причину выше.'

    monkeypatch.setattr(H.ai, 'ask', fake_ask)

    text = await H.short_summary(
        ['Подтапливает по стояку', 'Перекрыли, смотрим выше'], 'Трилиссера 8/5')

    assert 'Подтапливает по стояку' in zadacha['text']
    assert 'Перекрыли, смотрим выше' in zadacha['text']
    assert 'Течь по стояку, перекрыли' in text
    assert '2 видео' in text


async def test_doslovnoe_ostayotsya_dostupnym(monkeypatch):
    """Суть в чате, дословное в базе — иначе спорить будет нечем."""
    async def fake_ask(prompt, **kw):
        return 'Течь по стояку.'

    monkeypatch.setattr(H.ai, 'ask', fake_ask)

    text = await H.short_summary(['подтапливает'], 'Трилиссера 8/5')

    assert '/chat' in text


async def test_bez_modeli_pokazyvaem_skazannoe(monkeypatch):
    """ИИ отвалился — лучше сырая запись, чем молчание."""
    async def no_ai(prompt, **kw):
        return None

    monkeypatch.setattr(H.ai, 'ask', no_ai)

    text = await H.short_summary(['подтапливает по стояку'], 'Трилиссера 8/5')

    assert 'подтапливает по стояку' in text


# ---------- Брань ----------

async def test_bran_ne_popadaet_v_chat(monkeypatch):
    """Сантехники говорят как говорят. В отчёте этого быть не должно."""
    async def fake_ask(prompt, **kw):
        return 'Кран сорвало, бля, воду перекрыли'   # модель сорвалась

    monkeypatch.setattr(H.ai, 'ask', fake_ask)

    text = await H.short_summary(['...'], 'Седова 71')

    assert 'бля' not in text
    assert 'Кран сорвало' in text and 'воду перекрыли' in text


async def test_bran_ne_popadaet_v_bazu(monkeypatch):
    """Расшифровку читают в паспорте дома и в выгрузке руководителю."""
    monkeypatch.setattr(H, 'SERIES_WINDOW', 0.05)

    async def fake_transcribe(url):
        return 'Тут бля всё потекло нахуй, кран менять'
    monkeypatch.setattr(transcribe, 'transcribe_url', fake_transcribe)

    async def fake_ask(prompt, **kw):
        return 'Течь, требуется замена крана.'
    monkeypatch.setattr(H.ai, 'ask', fake_ask)

    dom = houses.detect_house('Седова 71')
    rid = db.add_chat_record(7, 'm1', 100, 'Андрей', 'Седова 71',
                             house_id=dom['id'], has_files=True)

    await H.transcribe_later(rid, 'http://x/v.mp4', bot=FakeBot(), chat_id=7, mid='m1')
    await asyncio.sleep(0.3)

    zapis = db.get_chat_record(rid)['transcript']
    assert 'бля' not in zapis and 'нахуй' not in zapis
    assert 'кран менять' in zapis, 'смысл сохранён'


def test_rabochie_slova_ne_stradayut():
    """«Требуется», «щебень», «сукно» — обычные слова, трогать нельзя."""
    from bot import mat

    for slovo in ['требуется замена', 'щебень завезли', 'сукно постелили',
                  'мандарины в офисе', 'хомут поставили']:
        assert mat.mask(slovo) == slovo


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


# ---------- Подпись к ролику: заказчик помечает каждое видео ----------

@pytest.mark.parametrize('podpis, adres', [
    ('8/5 Салон красоты «МИ Студия»', 'Трилиссера 8/5'),
    ('Салон красоты МИ Студия, подтапливает', 'Трилиссера 8/5'),
    ('Mi Studio', 'Трилиссера 8/5'),
    ('30 подвал, манометры', '4-я Советская 30'),
    ('26 парковка, тепловой узел', '4-я Советская 26'),
])
def test_metka_pod_video_uznayotsya(podpis, adres):
    """«Я специально промаркировываю все видео» — метку надо читать."""
    assert houses.detect_house(podpis)['address'] == adres


@pytest.mark.parametrize('ne_adres', [
    '30 манометров привезли',
    '8 задвижек забрали со склада',
    '22 трубы привезли',
    '2 часа ждали бригаду',
    'Четыре солнца, вентиль 32 мм лопнул',
])
def test_schyot_veschey_za_metku_ne_prinimaetsya(ne_adres):
    assert houses.detect_house(ne_adres) is None


async def test_adres_otdelnym_soobscheniem_pered_rolikom(monkeypatch):
    """Сначала «8/5 Салон красоты», потом видео — дом у обоих один."""
    monkeypatch.setattr(H, 'SERIES_WINDOW', 0.05)

    async def fake_transcribe(url):
        return 'Подтапливает по стояку, вода идёт сверху'
    monkeypatch.setattr(transcribe, 'transcribe_url', fake_transcribe)

    dom = houses.detect_house('8/5 Салон красоты «МИ Студия»')
    db.add_chat_record(7, 'm0', 100, 'Андрей', '8/5 Салон красоты «МИ Студия»',
                       house_id=dom['id'])
    rid = db.add_chat_record(7, 'm1', 100, 'Андрей', '', has_files=True)
    bot = FakeBot()

    await H.transcribe_later(rid, 'http://x/v.mp4', bot=bot, chat_id=7, mid='m1')
    await asyncio.sleep(0.3)

    assert db.get_chat_record(rid)['house_id'] == dom['id']
    assert 'Адрес не назвали' not in bot.sent[0]


async def test_chuzhoy_adres_iz_chata_ne_prilipaet(monkeypatch):
    """Дом берём у того же человека, а не у любого, кто писал в чат."""
    monkeypatch.setattr(H, 'SERIES_WINDOW', 0.05)

    async def fake_transcribe(url):
        return 'Подтапливает по стояку'
    monkeypatch.setattr(transcribe, 'transcribe_url', fake_transcribe)

    chuzhoy = houses.detect_house('Седова 71')
    db.add_chat_record(7, 'm0', 999, 'Константин', 'Седова 71 закрыли',
                       house_id=chuzhoy['id'])
    rid = db.add_chat_record(7, 'm1', 100, 'Андрей', '', has_files=True)
    bot = FakeBot()

    await H.transcribe_later(rid, 'http://x/v.mp4', bot=bot, chat_id=7, mid='m1')
    await asyncio.sleep(0.3)

    assert db.get_chat_record(rid)['house_id'] is None
    assert 'Адрес не назвали' in bot.sent[0]
