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


# ---------- Ответ на её сообщение ----------

def reply_event(text, na_chto='🎙 Трилиссера 8/5 · подтапливает по стояку',
                ot_kogo=555):
    """Сообщение, отправленное кнопкой «Ответить» на сообщение Люси."""
    e = event(text)
    e.message.link = types.SimpleNamespace(
        type='reply',
        sender=types.SimpleNamespace(user_id=ot_kogo),
        message=types.SimpleNamespace(text=na_chto))
    return e


@pytest.fixture
def lusya_id(monkeypatch):
    monkeypatch.setitem(H.BOT_ME, 'user_id', 555)
    return 555


def test_otvet_na_soobschenie_lusi_eto_obraschenie(lusya_id):
    assert H.replied_to_me(reply_event('а адрес какой?')) is True


def test_otvet_na_chuzhoe_soobschenie_ne_k_ney(lusya_id):
    assert H.replied_to_me(reply_event('ага', ot_kogo=999)) is False


def test_obychnoe_soobschenie_ne_otvet(lusya_id):
    assert H.replied_to_me(event('просто текст')) is False


async def test_na_otvet_bez_imeni_ona_vsyo_ravno_otvechaet(lusya_id, monkeypatch):
    """Нажали «Ответить» на её сообщение — значит, обращаются к ней."""
    async def fake_answer(uid, name, text, chat_id=None):
        return 'Трилиссера 8/5.'

    monkeypatch.setattr(H.agent, 'answer', fake_answer)
    e = reply_event('а адрес какой?')

    await H.on_text(e)

    assert e.message.sent == ['Трилиссера 8/5.']


async def test_v_otvete_ona_vidit_svoyo_soobschenie(lusya_id, monkeypatch):
    """Иначе «а адрес какой?» — вопрос ни о чём."""
    sprosili = {}

    async def fake_answer(uid, name, text, chat_id=None):
        sprosili['text'] = text
        return 'ответ'

    monkeypatch.setattr(H.agent, 'answer', fake_answer)

    await H.on_text(reply_event('а адрес какой?'))

    assert 'подтапливает по стояку' in sprosili['text']
    assert 'а адрес какой?' in sprosili['text']


async def test_bez_ii_obraschenie_vsyo_ravno_ne_ostayotsya_bez_otveta(lusya_id, monkeypatch):
    async def no_ai(*a, **kw):
        return None

    monkeypatch.setattr(H.agent, 'answer', no_ai)
    e = event('Мне очень нравится Люся')

    await H.on_text(e)

    assert e.message.sent, 'молчать в ответ на обращение нельзя'


# ---------- Люся знает, как она устроена ----------

def test_v_podskazke_skazano_kogda_ona_otvechaet():
    """Спросят «почему не отвечаешь» — пусть отвечает правдой, а не догадкой."""
    from bot import agent

    p = agent._build_prompt()

    assert 'зовут по имени' in p
    assert 'отвечают на твоё сообщение' in p
    assert 'задним числом не читаешь' in p
    assert '/тихо' in p and '/болтай' in p


def test_v_podskazke_skazano_k_komu_idti_s_dorabotkoy():
    from bot import agent

    assert 'Андрею Кузьмину' in agent._build_prompt()
