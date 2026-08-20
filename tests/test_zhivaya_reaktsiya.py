"""Люся иногда отзывается в рабочем чате не по делу.

Заказчик: «разрешим ей больше активничать в чате? Пусть разряжает
обстановку». Мера важнее остроумия: бот, вставляющий слово в каждое
сообщение, вылетает из чата первым.
"""
import types

import pytest

from bot import banter, db
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    banter.forget()
    H.STATE.clear()


CHAT = 7


@pytest.mark.parametrize('povod', [
    'Доброе утро, мужики',
    'спасибо, выручила',
    'всё, закрыли заявку, готово',
    'устал сегодня как собака',
    'на улице дубак',
])
def test_na_ponyatnyy_povod_otzyvaetsya(povod):
    assert banter.reply(CHAT, povod)
    banter.forget()


@pytest.mark.parametrize('obychnoe', [
    'привезли задвижку 50',
    'буду в 14 на Седова',
    'кто взял ключи от ИТП?',
    '',
])
def test_na_rabochie_soobscheniya_molchit(obychnoe):
    assert banter.reply(CHAT, obychnoe) is None


@pytest.mark.parametrize('avariya', [
    'на Седова 71 течь в подвале, срочно',
    'прорыв, всё, готово дело — топит',
    'засор в первом подъезде, спасибо соседям',
])
def test_pri_avarii_ne_shutit(avariya):
    """Там не до веселья, даже если в тексте есть «спасибо» или «готово»."""
    assert banter.reply(CHAT, avariya) is None


def test_ne_chastit():
    assert banter.reply(CHAT, 'Доброе утро')
    assert banter.reply(CHAT, 'спасибо') is None, 'полчаса паузы'


def test_cherez_polchasa_snova_mozhno():
    banter.reply(CHAT, 'Доброе утро', now=0)

    assert banter.reply(CHAT, 'спасибо', now=banter.PAUSE + 1)


def test_odin_povod_dvazhdy_podryad_ne_povtoryaet():
    """Две одинаковые шутки подряд хуже одной."""
    banter.reply(CHAT, 'спасибо', now=0)

    assert banter.reply(CHAT, 'спасибо большое', now=banter.PAUSE + 1) is None


def test_chaty_ne_meshayut_drug_drugu():
    assert banter.reply(7, 'Доброе утро')
    assert banter.reply(9, 'Доброе утро'), 'в другом чате своя пауза'


# ---------- Выключатель ----------

def test_po_umolchaniyu_razresheno():
    assert db.banter_on(CHAT) is True


def test_mozhno_zapretit_i_vernut():
    db.set_banter(CHAT, False)
    assert db.banter_on(CHAT) is False

    db.set_banter(CHAT, True)
    assert db.banter_on(CHAT) is True


def test_zapret_deystvuet_tolko_na_svoy_chat():
    db.set_banter(CHAT, False)
    assert db.banter_on(9) is True


# ---------- Чат целиком ----------

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


async def test_v_chate_otzyvaetsya_bez_obrascheniya_po_imeni():
    e = event('Доброе утро, мужики')

    await H.on_text(e)

    assert e.message.sent, 'по имени не звали, но повод понятный'


async def test_rabochee_soobschenie_ostavlyaet_v_pokoe():
    e = event('привезли задвижку 50')

    await H.on_text(e)

    assert e.message.sent == []


async def test_komanda_tiho_vyklyuchaet_reakcii():
    await H.on_quiet(event('/тихо'))
    assert db.banter_on(CHAT) is False

    e = event('Доброе утро, мужики')
    await H.on_text(e)

    assert e.message.sent == [], 'сказали молчать — молчит'


async def test_komanda_boltay_vozvraschaet():
    db.set_banter(CHAT, False)

    await H.on_banter_on(event('/болтай'))
    e = event('Доброе утро, мужики')
    await H.on_text(e)

    assert e.message.sent
