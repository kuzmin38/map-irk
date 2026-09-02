"""Голосовое в личку и объявление жильцам с голоса.

Заказчик наговорил Люсе в личку просьбу переложить его слова деловым
языком для домового чата — и не получил ничего. Расшифровка была заведена
только для рабочего чата: в личке сообщение без текста просто молча
пропускалось.
"""
import types

import pytest

from bot import announce, db, houses
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id=None, user_id=None, text=None, link=None):
        self.sent.append((chat_id, text))


class Msg:
    def __init__(self, text='', golos=False):
        att = None
        if golos:
            att = [types.SimpleNamespace(
                type='audio', payload=types.SimpleNamespace(url='https://x/a.ogg'))]
        self.body = types.SimpleNamespace(text=text, attachments=att, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=100, chat_id=None, chat_type='dialog')
        self.sent = []
        self.link = None

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')


def event(text='', golos=False, bot=None):
    e = types.SimpleNamespace()
    e.message = Msg(text, golos)
    e.bot = bot or Bot()
    e.callback = types.SimpleNamespace(
        user=types.SimpleNamespace(user_id=100, full_name='Андрей'))
    return e


# ---------- Голосовое в личке ----------

async def test_golosovoe_v_lichke_rasshifrovyvaetsya(monkeypatch):
    async def fake(url):
        return 'Перекрыл стояк по 105 квартире на 65а/3'

    monkeypatch.setattr(H.transcribe, 'transcribe_url', fake)
    monkeypatch.setattr(H, 'speech_url', lambda body: 'https://x/a.ogg')

    e = event(golos=True)
    await H.on_text(e)

    assert any('Услышала' in t for t in e.message.sent), 'показала, что разобрала'
    assert db.open_shutoffs(), 'и дальше работает как с обычным текстом'


async def test_neponyatnuyu_zapis_ne_pridumyvaet(monkeypatch):
    async def fake(url):
        return None

    monkeypatch.setattr(H.transcribe, 'transcribe_url', fake)
    monkeypatch.setattr(H, 'speech_url', lambda body: 'https://x/a.ogg')

    e = event(golos=True)
    await H.on_text(e)

    assert 'Не разобрала' in e.message.sent[-1]


async def test_stiker_po_prezhnemu_molchit(monkeypatch):
    monkeypatch.setattr(H, 'speech_url', lambda body: None)

    e = event()
    await H.on_text(e)

    assert e.message.sent == []


# ---------- Объявление жильцам ----------

@pytest.mark.parametrize('fraza', [
    'Люся, сделай объявление жильцам грамотным языком: перекрываем стояк',
    'напиши в домовой чат, что завтра с 10 до 14 не будет воды',
    'перепиши грамотным языком для жильцов: воды не будет до вечера',
])
def test_prosba_ob_obyavlenii_uznayotsya(fraza):
    assert announce.wants_announcement(fraza) is True


@pytest.mark.parametrize('fraza', [
    'перекрыл стояк по 105 квартире на 65а/3',
    'поехал на объект',
])
def test_obychnye_prosby_ne_trogaem(fraza):
    assert announce.wants_announcement(fraza) is False


def test_sama_prosba_v_obyavlenie_ne_popadaet():
    sut = announce.strip_trigger(
        'Люся, сделай объявление жильцам грамотным языком: '
        'перекрываем стояк на 65а/3 с 10 до 14')

    assert 'объявление' not in sut.lower()
    assert 'жильцам' not in sut.lower()
    assert 'Люся' not in sut
    assert '65а/3' in sut and '10 до 14' in sut, 'суть и цифры на месте'


async def test_obyavlenie_pokazyvaetsya_pered_otpravkoy(monkeypatch):
    dom = houses.detect_house('Седова 65а/3')
    db.bind_house_chat(9, dom['id'], by_name='Андрей')

    async def fake_ask(prompt, **kw):
        assert '65а/3' in prompt, 'слова человека уходят модели'
        return 'Уважаемые жильцы!\n\nЗавтра с 10:00 до 14:00 не будет воды.'

    monkeypatch.setattr(announce.ai, 'ask', fake_ask)

    text = 'сделай объявление жильцам: на 65а/3 завтра с 10 до 14 не будет воды'
    e = event(text)
    await H.handle_announcement(e, text, 100)

    assert 'Уважаемые жильцы' in e.message.sent[-1]
    assert e.bot.sent == [], 'без кнопки ничего не ушло'
    assert H.STATE[100]['mode'] == 'obyava'


async def test_knopka_otpravlyaet_v_chat_doma(monkeypatch):
    dom = houses.detect_house('Седова 65а/3')
    db.bind_house_chat(9, dom['id'], by_name='Андрей')

    async def fake_ask(prompt, **kw):
        return 'Уважаемые жильцы!\n\nЗавтра не будет воды.'

    monkeypatch.setattr(announce.ai, 'ask', fake_ask)
    text = 'сделай объявление жильцам: на 65а/3 завтра не будет воды'
    await H.handle_announcement(event(text), text, 100)

    bot = Bot()
    e = event(bot=bot)
    await H.run_action('obsend', e.message, 100, e)

    assert bot.sent == [(9, 'Уважаемые жильцы!\n\nЗавтра не будет воды.')]


async def test_bez_privyazki_otdayot_tekst_dlya_kopirovaniya(monkeypatch):
    async def fake_ask(prompt, **kw):
        return 'Уважаемые жильцы!\n\nЗавтра не будет воды.'

    monkeypatch.setattr(announce.ai, 'ask', fake_ask)

    text = 'сделай объявление жильцам: на 65а/3 завтра не будет воды'
    e = event(text)
    await H.handle_announcement(e, text, 100)

    otvet = e.message.sent[-1]
    assert 'Уважаемые жильцы' in otvet, 'текст всё равно отдан'
    assert '/дом' in otvet, 'и сказано, как привязать чат'


# ---------- Откуда берётся расшифровка ----------

class Vlozhenie:
    def __init__(self, tip='audio', url=None, transcription=None, urls=None):
        self.type = tip
        self.payload = types.SimpleNamespace(url=url) if url else None
        self.transcription = transcription
        self.urls = urls


def telo(*vlozheniya):
    return types.SimpleNamespace(text='', attachments=list(vlozheniya), mid='m')


def test_beryom_rasshifrovku_ot_max():
    """MAX расшифровывает голосовые сам — это быстрее, точнее и бесплатно."""
    body = telo(Vlozhenie(transcription='Перекрыл стояк на 65а/3'))

    assert H.speech_ready(body) == 'Перекрыл стояк на 65а/3'


def test_ssylka_na_video_lezhit_v_urls():
    """У видео ссылка не в payload.url, а в urls.mp4_*."""
    body = telo(Vlozhenie('video', urls=types.SimpleNamespace(
        mp4_1080=None, mp4_720=None, mp4_480='https://x/v.mp4',
        mp4_360=None, mp4_240=None, mp4_144=None, hls=None)))

    assert H.speech_url(body) == 'https://x/v.mp4'


def test_ssylka_na_golosovoe_v_payload():
    assert H.speech_url(telo(Vlozhenie(url='https://x/a.ogg'))) == 'https://x/a.ogg'


def test_kartinka_ne_rech():
    body = telo(Vlozhenie('image', url='https://x/p.jpg'))

    assert H.speech_url(body) is None
    assert H.speech_ready(body) is None


def test_v_log_vidno_chto_prishlo():
    """Иначе «не ответила на голосовое» неотличимо от «не получила его»."""
    opis = H.opisat_vlozheniya(telo(Vlozhenie('audio', transcription='привет')))

    assert 'audio' in opis and 'transcription' in opis
    assert H.opisat_vlozheniya(telo()) == 'вложений нет'


async def test_gotovuyu_rasshifrovku_ne_perevodim_zanovo(monkeypatch):
    zvali = []

    async def fake(url):
        zvali.append(url)
        return 'из модели'

    monkeypatch.setattr(H.transcribe, 'transcribe_url', fake)

    e = event()
    e.message.body.attachments = [Vlozhenie(transcription='Перекрыл стояк на 65а/3')]
    await H.on_text(e)

    assert zvali == [], 'модель не дёргаем, расшифровка уже есть'
    assert any('Услышала' in t for t in e.message.sent)
