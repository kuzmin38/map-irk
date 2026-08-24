"""Паспорт дома заполняется из чата, а плановые работы видны в нём же.

Заказчик: «нужно заводить цифровые паспорта, там должны быть плановые
работы по тепловым пунктам, и туда сохранять информацию из чата. Если
дом непонятен — переспрашивать, если написан прямым текстом — сохранять
не спрашивая».
"""
import types

import pytest

from bot import db, houses, passport
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


@pytest.fixture
def razbor(monkeypatch):
    """Модель раскладывает сведения по разделам паспорта."""
    async def fake_ask(prompt, **kw):
        # В задании перечислены все разделы паспорта, поэтому смотрим на
        # приметы самого сообщения, а не на список полей
        if 'ду50' in prompt.lower():
            return '{"раздел": "rozliv", "текст": "Нижний, сталь ДУ50"}'
        if 'у мастера' in prompt.lower():
            return '{"раздел": "keys", "текст": "Ключ от подвала у мастера"}'
        return '{"раздел": "notes", "текст": "прочее"}'
    monkeypatch.setattr(passport.ai, 'ask', fake_ask)


# ---------- Распознавание просьбы ----------

@pytest.mark.parametrize('fraza', [
    'Розлив нижний, сталь ДУ50. В паспорт',
    'запиши в паспорт дома: ключ у мастера',
    'для паспорта: розлив верхний',
])
def test_prosba_v_pasport_uznayotsya(fraza):
    assert passport.wants_passport(fraza) is True


@pytest.mark.parametrize('fraza', [
    'поехал на объект',
    'паспорт готовности сдали',      # это про другое, но слова «в паспорт» нет
])
def test_obychnye_soobscheniya_ne_trogaem(fraza):
    assert passport.wants_passport(fraza) is False


def test_prosba_ne_popadaet_v_grafu():
    """В паспорте должны быть сведения, а не «Люся запиши в паспорт»."""
    chisto = passport.strip_trigger('Люся, запиши в паспорт: розлив нижний, сталь ДУ50')

    assert 'запиши' not in chisto.lower()
    assert 'паспорт' not in chisto.lower()
    assert 'Люся' not in chisto
    assert 'розлив нижний, сталь ДУ50' in chisto


# ---------- Запись ----------

class Msg:
    def __init__(self, text, quoted=None):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=None, chat_id=7, chat_type='chat')
        self.sent = []
        self.link = types.SimpleNamespace(
            type='reply', sender=types.SimpleNamespace(user_id=555),
            message=types.SimpleNamespace(text=quoted)) if quoted else None

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')


def event(text, quoted=None):
    e = types.SimpleNamespace()
    e.message = Msg(text, quoted)
    e.bot = None
    return e


async def test_dom_nazvan_pryamo_zapisyvaem_bez_voprosov(razbor):
    text = 'Седова 71 розлив нижний, сталь ДУ50. В паспорт'
    e = event(text)

    vzyala = await H.handle_passport_note(e, text, 100)

    assert vzyala is True
    dom = houses.detect_house('Седова 71')
    assert db.get_passport(dom['id'])['rozliv'] == 'Нижний, сталь ДУ50'
    assert 'Седова 71' in e.message.sent[-1]


async def test_dom_ne_nazvan_peresprashivaem(razbor):
    text = 'Розлив нижний, сталь ДУ50. В паспорт'
    e = event(text)

    await H.handle_passport_note(e, text, 100)

    assert 'По какому дому' in e.message.sent[-1]
    assert H.STATE[100]['mode'] == 'pass_house', 'ждём адрес'
    assert db.get_passport(houses.HOUSES[0]['id']) == {}


async def test_otvet_s_adresom_dopisyvaet_zapis(razbor):
    text = 'Розлив нижний, сталь ДУ50. В паспорт'
    await H.handle_passport_note(event(text), text, 100)

    otvet = event('Седова 71')
    await H.on_text(otvet)

    dom = houses.detect_house('Седова 71')
    assert db.get_passport(dom['id'])['rozliv'] == 'Нижний, сталь ДУ50'
    assert 100 not in H.STATE


async def test_zapis_dopisyvaetsya_a_ne_zatiraet(razbor):
    dom = houses.detect_house('Седова 71')
    db.set_passport_field(dom['id'], 'rozliv', 'Старая запись', 'Константин')

    text = 'Седова 71 розлив нижний, сталь ДУ50. В паспорт'
    await H.handle_passport_note(event(text), text, 100)

    znachenie = db.get_passport(dom['id'])['rozliv']
    assert 'Старая запись' in znachenie, 'чужую работу не затираем'
    assert 'Нижний, сталь ДУ50' in znachenie


async def test_svedeniya_iz_citaty(razbor):
    """Сведения прислали сообщением, а просьбу — ответом на него."""
    e = event('Люся, в паспорт', 'Седова 71: ключ от подвала у мастера')

    await H.handle_passport_note(e, 'Люся, в паспорт', 100)

    dom = houses.detect_house('Седова 71')
    assert db.get_passport(dom['id'])['keys'] == 'Ключ от подвала у мастера'


# ---------- Плановые работы в паспорте ----------

def test_plановye_raboty_vidny_v_pasporte():
    dom = houses.detect_house('Седова 71')
    db.add_work(dom['id'], 'Ремонт теплообменника отопления', '2026-09-01', 'Костя', 1)

    text = H.passport_text(dom)

    assert 'ПЛАНОВЫЕ РАБОТЫ' in text
    assert 'Ремонт теплообменника отопления' in text
    assert '01.09' in text


def test_bez_rabot_pasport_govorit_ob_etom():
    dom = houses.detect_house('Седова 71')

    assert 'Плановых работ нет' in H.passport_text(dom)


def test_sdannye_raboty_pasport_ne_zasoryayut():
    dom = houses.detect_house('Седова 71')
    w = db.add_work(dom['id'], 'Старая работа', None, 'Костя', 1)
    db.update_work(w, status=db.WORK_DONE)

    assert 'Старая работа' not in H.passport_text(dom)


async def test_otvet_v_chate_toze_rabotaet(razbor):
    """Спросили в чате — отвечают там же, коротким сообщением с адресом."""
    text = 'Розлив нижний, сталь ДУ50. В паспорт'
    await H.handle_passport_note(event(text), text, 100)

    otvet = event('Седова 71')
    await H.on_text(otvet)

    dom = houses.detect_house('Седова 71')
    assert db.get_passport(dom['id'])['rozliv'] == 'Нижний, сталь ДУ50'
    assert 100 not in H.STATE


async def test_neponyatnyy_adres_ne_sbrasyvaet_ozhidanie(razbor):
    """Ответили ерундой — переспросим, но сведения не потеряем."""
    text = 'Розлив нижний, сталь ДУ50. В паспорт'
    await H.handle_passport_note(event(text), text, 100)

    await H.on_text(event('да чёрт его знает'))

    assert H.STATE[100]['mode'] == 'pass_house', 'ждём адрес дальше'
    assert H.STATE[100]['text'] == 'Розлив нижний, сталь ДУ50'
