"""«Личный» чат — Люся ничего оттуда не сохраняет.

Заказчик показал, что в чате «Сантехники» — внутренняя болтовня бригады
про перенос неаварийных заявок и уже сделанные работы — Люся выдаёт
«Видеоотчёт» и спрашивает адрес, как будто это официальный отчёт в
«Обслуживание». «Это для такого, ну, пользования личного нашего. Не
надо, чтобы она что-то оттуда сохраняла».

Команда /тихо для этого не годится — она выключает только живые реплики
(бантер), а не запись видео, находок и привязку к домам. Нужен отдельный
переключатель — /личный и /рабочий.
"""
import types

import pytest

from bot import db, houses
import bot.handlers as H

CHAT = 42


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


class Msg:
    def __init__(self, text, chat_id=CHAT, chat_type='chat', files=False):
        self.body = types.SimpleNamespace(
            text=text, attachments=([object()] if files else None),
            mid='m1', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Костя')
        self.recipient = types.SimpleNamespace(user_id=None, chat_id=chat_id,
                                               chat_type=chat_type)
        self.sent = []
        self.link = None

    async def answer(self, text=None, attachments=None):
        self.sent.append(text)


def event(text, chat_id=CHAT, chat_type='chat', files=False):
    e = types.SimpleNamespace()
    e.message = Msg(text, chat_id, chat_type, files)
    e.bot = None
    return e


def house_id(address):
    return next(h['id'] for h in houses.HOUSES if h['address'] == address)


# ── переключатель в базе ────────────────────────────────────────────────

def test_po_umolchaniyu_zapis_vklyuchena():
    assert db.recording_on(CHAT) is True


def test_lichnyy_vyklyuchaet_rabochiy_vklyuchaet():
    db.set_recording(CHAT, False)
    assert db.recording_on(CHAT) is False
    db.set_recording(CHAT, True)
    assert db.recording_on(CHAT) is True


def test_pereklyuchatel_svoy_dlya_kazhdogo_chata():
    db.set_recording(CHAT, False)
    assert db.recording_on(CHAT) is False
    assert db.recording_on(CHAT + 1) is True


# ── команды /личный и /рабочий ──────────────────────────────────────────

async def test_komanda_lichnyy_vyklyuchaet_zapis():
    await H.on_private_chat(event('/личный'))
    assert db.recording_on(CHAT) is False


async def test_komanda_rabochiy_vozvraschaet_zapis():
    db.set_recording(CHAT, False)
    await H.on_working_chat(event('/рабочий'))
    assert db.recording_on(CHAT) is True


async def test_lichnyy_v_lichke_ne_lomaetsya():
    """В личном разговоре с Люсей переключать нечего — ответ по-другому."""
    e = event('/личный', chat_type='dialog')
    await H.on_private_chat(e)
    assert 'рабочего чата' in e.message.sent[-1]


# ── сам эффект: /личный чат ничего не сохраняет ─────────────────────────

async def test_v_lichnom_chate_video_ne_rasshifrovyvaetsya():
    """Тот самый случай: сантехник просит перенести заявки, шлёт видео."""
    db.set_recording(CHAT, False)
    e = event('Сантехник просит перенести неаварийные заявки на завтра',
              files=True)

    await H.on_text(e)

    assert db.chat_stats_for_day(db.now()[:10])['total'] == 0
    assert e.message.sent == []


async def test_v_lichnom_chate_adres_ne_privyazyvaetsya():
    db.set_recording(CHAT, False)
    e = event('На Байкальской улице проводились работы в салоне красоты')

    await H.on_text(e)

    assert db.house_chat_records(house_id('Байкальская 237')) == []


async def test_posle_rabochiy_zapis_vozvraschaetsya():
    db.set_recording(CHAT, False)
    await H.on_text(event('на Байкальской 237 течь в подвале'))
    assert db.chat_stats_for_day(db.now()[:10])['total'] == 0

    db.set_recording(CHAT, True)
    await H.on_text(event('на Байкальской 237 течь в подвале'))
    assert db.chat_stats_for_day(db.now()[:10])['total'] == 1


async def test_drugoy_chat_ne_zatragivaetsya():
    """Выключили запись в «Сантехники» — «Обслуживание» это не касается."""
    db.set_recording(CHAT, False)
    await H.on_text(event('на Байкальской 237 течь в подвале', chat_id=CHAT + 1))
    assert db.house_chat_records(house_id('Байкальская 237'))


async def test_obraschenie_po_imeni_vsyo_ravno_otvechaet(monkeypatch):
    """Выключили запись — не выключили саму Люсю: позовут, ответит."""
    async def fake_answer(user_id, user_name, text, chat_id=None):
        return 'Отвечаю, раз позвали.'

    monkeypatch.setattr(H.agent, 'answer', fake_answer)
    db.set_recording(CHAT, False)

    e = event('Люся, какой сегодня день?')
    await H.on_text(e)

    assert e.message.sent
    assert db.chat_stats_for_day(db.now()[:10])['total'] == 0, \
        'ответила, но след в ленте чата не оставила'
