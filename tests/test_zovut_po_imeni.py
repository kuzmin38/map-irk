"""Имя в любом месте фразы — это обращение к Люсе.

Маша написала в чате «Мне очень нравится Люся» — и Люся промолчала.
Обращение она узнавала только в начале фразы или в конце после запятой,
а имя посреди предложения пропускала. Со стороны это выглядит как
невоспитанность: человек похвалил, ему не ответили.
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


@pytest.mark.parametrize('fraza', [
    'Мне очень нравится Люся',
    'надо у Люси спросить про манометры',
    'позовите Люсю, она подскажет',
    'Люся, что по нормативам ГВС?',
    'что по нормативам ГВС, Люся?',
    'Умнеешь на глазах, Люся!',
    'спроси Люсе покажи заявки',
])
def test_imya_v_lyubom_meste_eto_obraschenie(fraza):
    addressed, _ = H.strip_address(fraza)
    assert addressed is True


@pytest.mark.parametrize('fraza', [
    'Люстра в подъезде не горит',
    'закончили работы на Седова',
    'Люсьен приехал за деньгами',
    'привезли задвижку 50',
])
def test_pohozhie_slova_ne_schitayutsya(fraza):
    """«Люстра» — не «Люся»: отвечать на каждое похожее слово нельзя."""
    addressed, _ = H.strip_address(fraza)
    assert addressed is False


def test_vopros_ostayotsya_bez_obrascheniya():
    """«Что по нормативам, Люся?» — модели уходит только вопрос."""
    assert H.strip_address('что по нормативам ГВС, Люся?')[1] == 'что по нормативам ГВС'


def test_pohvala_uhodit_tselikom():
    """Здесь имя — часть фразы, без него от похвалы ничего не остаётся."""
    assert H.strip_address('Мне очень нравится Люся')[1] == 'Мне очень нравится Люся'


def test_pohvala_bez_imeni_poluchaet_zhivoy_otvet():
    """Похвалили, не назвав по имени — отвечает своей репликой, без ИИ."""
    assert banter.pick('вот это помощница, толковая')


# ---------- Чат целиком ----------

class Msg:
    def __init__(self, text):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=300, full_name='Мария')
        self.recipient = types.SimpleNamespace(user_id=None, chat_id=7, chat_type='chat')
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text)


def event(text):
    e = types.SimpleNamespace()
    e.message = Msg(text)
    e.bot = None
    return e


async def test_kompliment_v_chate_ne_ostayotsya_bez_otveta(monkeypatch):
    sprosili = {}

    async def fake_answer(uid, name, text, chat_id=None):
        sprosili['text'] = text
        return 'Спасибо, Мария! ☺️'

    monkeypatch.setattr(H.agent, 'answer', fake_answer)
    e = event('Мне очень нравится Люся')

    await H.on_text(e)

    assert e.message.sent == ['Спасибо, Мария! ☺️']
    assert 'нравится' in sprosili['text'], 'модель видит, за что её похвалили'


async def test_rabochiy_razgovor_bez_imeni_ne_trogaem(monkeypatch):
    async def fake_answer(*a, **kw):
        return 'зачем-то ответила'

    monkeypatch.setattr(H.agent, 'answer', fake_answer)
    e = event('привезли задвижку 50, завтра поставим')

    await H.on_text(e)

    assert e.message.sent == []
