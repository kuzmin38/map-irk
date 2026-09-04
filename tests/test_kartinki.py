"""Люся читает присланные картинки.

Заказчик скинул шесть скриншотов таблицы жильцов и попросил список квартир
с телефонами для обзвона. Люся ответила «у меня нет доступа к номерам
телефонов жильцов» — номера лежали прямо в сообщении, она их не открывала.

Отдельно проверяется, что прочитанные персональные данные не попадают в
базу: оттуда они ушли бы в паспорт дома, в выгрузку инженеру и в отчёт
руководителю.
"""
import asyncio

import pytest

from bot import db, kartinki
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()
    H.KARTINKI.clear()


# В личке chat_id у Люси всегда None: личка и чат памятью не делятся
LICHKA = (None, 7)


class Payload:
    def __init__(self, url):
        self.url = url


class Attach:
    def __init__(self, url, tip='image'):
        self.type = tip
        self.payload = Payload(url)


class FakeMsg:
    def __init__(self, atts=None):
        self.recipient = type('R', (), {'chat_id': 500})()
        self.body = type('B', (), {'mid': 'm1', 'text': '',
                                   'attachments': atts or []})()
        self.sender = type('S', (), {'full_name': 'Андрей', 'user_id': 7})()


class FakeEvent:
    def __init__(self, atts=None):
        self.message = FakeMsg(atts)
        self.bot = None


@pytest.fixture
def otvety(monkeypatch):
    poslano = []

    async def fake_send(msg, text, kb=None, **kw):
        poslano.append(text)

    async def nichego(event):
        return None

    monkeypatch.setattr(H, 'send', fake_send)
    monkeypatch.setattr(H, 'pechataet', nichego)
    return poslano


# ── персональные данные ─────────────────────────────────────────────────

def test_uznayot_personalnye_dannye():
    assert kartinki.lichnye_dannye(
        'Квартира 3 — Андреевская Татьяна Викторовна — 8 908 658 12 11')
    assert kartinki.lichnye_dannye(
        'Лицевой счет №711003\nЛицевой счет №711012')


def test_rabochiy_tekst_za_personalnye_ne_schitaet():
    """Ложная тревога тут дорога: она гасит полезный ответ пометкой."""
    assert not kartinki.lichnye_dannye(
        'Течь по стояку ГВС в подвале, поставлен хомут')
    assert not kartinki.lichnye_dannye('Диспетчерская 48-78-05, доб. 1')


# ── чтение ──────────────────────────────────────────────────────────────

async def test_shest_skrinshotov_odin_otvet(monkeypatch, otvety):
    """Шесть кусков списка для обзвона по отдельности бесполезны."""
    zapros = {}

    async def fake_prochitat(urls, vopros=None):
        zapros['urls'] = list(urls)
        zapros['vopros'] = vopros
        return 'Квартира 3 — Андреевская Татьяна Викторовна — 89086581211'

    monkeypatch.setattr(H.kartinki_mod, 'prochitat', fake_prochitat)

    event = FakeEvent()
    for n in range(6):
        H.zapomnit_kartinki(LICHKA, [f'https://max/{n}.jpg'])
    ok = await H.handle_kartinki(
        event, 'Сделай мне список квартир с номерами телефонов', 7)

    assert ok
    assert len(zapros['urls']) == 6, 'все шесть уходят одним вопросом'
    assert 'список квартир' in zapros['vopros']
    assert len(otvety) == 1


async def test_personalnye_dannye_v_bazu_ne_pishem(monkeypatch, otvety):
    async def fake_prochitat(urls, vopros=None):
        return ('Квартира 3 — Андреевская Татьяна Викторовна — 89086581211\n'
                'Квартира 12 — Костанчук Наталья Сергеевна — 89501234567')

    monkeypatch.setattr(H.kartinki_mod, 'prochitat', fake_prochitat)
    H.zapomnit_kartinki(LICHKA, ['https://max/1.jpg'])
    await H.handle_kartinki(FakeEvent(), 'список для обзвона', 7)

    assert 'В базу это не записываю' in otvety[-1]
    assert '89086581211' in otvety[-1], 'самому спросившему список нужен целиком'
    zapisi = db.all_chat_records(limit=50)
    assert not any('89086581211' in (z['text'] or '') for z in zapisi)


async def test_rabochee_foto_bez_pometki(monkeypatch, otvety):
    async def fake_prochitat(urls, vopros=None):
        return 'На фото хомут на трубе ГВС, следов течи нет.'

    monkeypatch.setattr(H.kartinki_mod, 'prochitat', fake_prochitat)
    H.zapomnit_kartinki(LICHKA, ['https://max/1.jpg'])
    await H.handle_kartinki(FakeEvent(), 'что тут', 7)

    assert 'В базу это не записываю' not in otvety[-1]


async def test_ne_prochitalos_govorit_pryamo(monkeypatch, otvety):
    async def fake_prochitat(urls, vopros=None):
        return None

    monkeypatch.setattr(H.kartinki_mod, 'prochitat', fake_prochitat)
    H.zapomnit_kartinki(LICHKA, ['https://max/1.jpg'])
    await H.handle_kartinki(FakeEvent(), 'что тут', 7)

    assert 'Не смогла разобрать' in otvety[-1]


# ── когда вмешиваться, а когда нет ──────────────────────────────────────

async def test_bez_kartinok_ne_vmeshivaetsya(otvety):
    assert not await H.handle_kartinki(FakeEvent(), 'Седова 71', 7)
    assert otvety == []


async def test_posle_otveta_ne_perehvatyvaet_sleduyushchiy_vopros(
        monkeypatch, otvety):
    """Иначе после скриншотов все вопросы уходили бы в картинки."""
    async def fake_prochitat(urls, vopros=None):
        return 'Таблица жильцов.'

    monkeypatch.setattr(H.kartinki_mod, 'prochitat', fake_prochitat)
    H.zapomnit_kartinki(LICHKA, ['https://max/1.jpg'])
    await H.handle_kartinki(FakeEvent(), 'что тут', 7)

    assert not await H.handle_kartinki(FakeEvent(), 'Седова 71', 7)


async def test_yavnaya_ssylka_na_kartinku_vozvrashchaet_k_ney(
        monkeypatch, otvety):
    async def fake_prochitat(urls, vopros=None):
        return 'Таблица жильцов.'

    monkeypatch.setattr(H.kartinki_mod, 'prochitat', fake_prochitat)
    H.zapomnit_kartinki(LICHKA, ['https://max/1.jpg'])
    await H.handle_kartinki(FakeEvent(), 'что тут', 7)

    assert await H.handle_kartinki(FakeEvent(), 'а в этой таблице сколько строк', 7)


async def test_novaya_kartinka_sbrasyvaet_otvechennost(monkeypatch, otvety):
    async def fake_prochitat(urls, vopros=None):
        return 'Таблица жильцов.'

    monkeypatch.setattr(H.kartinki_mod, 'prochitat', fake_prochitat)
    H.zapomnit_kartinki(LICHKA, ['https://max/1.jpg'])
    await H.handle_kartinki(FakeEvent(), 'что тут', 7)

    event = FakeEvent([Attach('https://max/2.jpg')])
    assert await H.handle_kartinki(event, '', 7)


def test_ssylki_beryotsya_tolko_u_kartinok():
    body = type('B', (), {'attachments': [
        Attach('https://max/1.jpg'),
        Attach('https://max/audio.mp3', tip='audio'),
        Attach('https://max/2.jpg'),
    ]})()
    assert H.image_urls(body) == ['https://max/1.jpg', 'https://max/2.jpg']


async def test_pachka_zhdyot_tishiny(monkeypatch, otvety):
    """Скриншоты летят подряд — ответ должен быть один, после последнего."""
    schyotchik = {'n': 0}

    async def fake_prochitat(urls, vopros=None):
        schyotchik['n'] += 1
        return f'Картинок: {len(urls)}'

    monkeypatch.setattr(H.kartinki_mod, 'prochitat', fake_prochitat)
    monkeypatch.setattr(H, 'KARTINKI_TISHINA', 0.05)

    key = LICHKA
    event = FakeEvent()
    zadacha = asyncio.create_task(H.kartinki_bez_voprosa(event, key))
    for n in range(3):
        H.zapomnit_kartinki(key, [f'https://max/{n}.jpg'])
        await asyncio.sleep(0.03)
    await zadacha

    assert schyotchik['n'] == 1
    assert 'Картинок: 3' in otvety[-1]
