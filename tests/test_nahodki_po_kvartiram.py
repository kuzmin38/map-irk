"""Что уже находили за этой дверью.

Костя прислал видео с подписью «71/1 105 квартира» и сказал в кадре, что
нашёл подмес. Заказчик: «вот эту информацию нужно сохранить — в этой
квартире уже был обнаружен подмес, он может обнаружиться снова, опять
забудут краны перекрытия. Вот эта информация нужная».
"""
import types

import pytest

from bot import db, flats, houses
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


class Msg:
    def __init__(self, text):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Костя')
        self.recipient = types.SimpleNamespace(user_id=None, chat_id=7, chat_type='chat')
        self.sent = []
        self.link = None

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')


def event(text):
    e = types.SimpleNamespace()
    e.message = Msg(text)
    e.bot = None
    return e


DOM = 'Седова 71/1'


# ---------- Разбор ----------

@pytest.mark.parametrize('text,kv', [
    ('71/1 105квартира', 105),
    ('Седова 71/1, 105 квартира', 105),
    ('на 71/1 в 105 кв течь', 105),
    ('кв. 105 подмес', 105),
])
def test_nomer_kvartiry_uznayotsya(text, kv):
    assert flats.parse_flat(text, houses.detect_house(text)) == kv


def test_nomer_doma_ne_putaetsya_s_kvartiroy():
    """В «71/1 105 квартира» число 71 — это дом, а не квартира."""
    assert flats.parse_flat('71/1 105квартира', houses.detect_house('71/1')) == 105


def test_ploschad_ne_kvartira():
    assert flats.parse_flat('Площадь 100 кв.м', None) is None


@pytest.mark.parametrize('text,chto', [
    ('нашёл подмес воды', 'подмес'),
    ('течь под мойкой', 'течь'),
    ('кран не перекрывается', 'не перекрывается'),
    ('стояк засорён', 'засорён'),
])
def test_nahodka_uznayotsya(text, chto):
    assert flats.nahodka(text) == chto


@pytest.mark.parametrize('text', [
    'поменял смеситель',
    'был в 105 квартире, всё нормально',
])
def test_obychnaya_rabota_ne_nahodka(text):
    assert flats.nahodka(text) is None


def test_nuzhny_vse_tri_chasti():
    """Дом, квартира и находка — без любой из них не пишем ничего."""
    dom = houses.detect_house(DOM)
    assert flats.parse_note('71/1 105 квартира', dom) is None, 'находки нет'
    assert flats.parse_note('71/1 нашёл подмес', dom) is None, 'квартиры нет'
    assert flats.parse_note('105 квартира, подмес', dom) == (105, 'подмес')


# ---------- Запись ----------

def test_nahodka_lozhitsya_v_kartochku_kvartiry():
    dom = houses.detect_house(DOM)
    text = 'Седова 71/1, 105 квартира, нашёл подмес воды'

    otvet = H.zapisat_nahodku(1, dom, text, 100, 'Костя')

    zametki = db.flat_notes(dom['id'], 105)
    assert len(zametki) == 1
    assert zametki[0]['kind'] == flats.kind_of('подмес')
    assert 'подмес' in zametki[0]['text']
    assert zametki[0]['author'] == 'Костя'
    assert 'кв. 105' in otvet and 'подмес' in otvet


def test_povtornaya_nahodka_preduprezhdaet():
    """Ради этого всё и затевалось: сказать, что тут это уже было."""
    dom = houses.detect_house(DOM)
    db.add_flat_note(dom['id'], 105, 'Нашёл подмес воды', kind=flats.kind_of('подмес'),
                     author='Костя')
    # запись годичной давности, чтобы не сработала защита от дублей
    with db._conn() as c:
        c.execute("UPDATE flat_notes SET created_at = '12.03.2026 10:00'")

    otvet = H.zapisat_nahodku(2, dom, '105 квартира, снова подмес', 100, 'Игорь')

    assert 'уже находили' in otvet
    assert '12.03.2026' in otvet
    assert len(db.flat_notes(dom['id'], 105)) == 2


def test_podpis_i_golosovoe_ob_odnom_vyezde_ne_dvoyatsya():
    dom = houses.detect_house(DOM)
    H.zapisat_nahodku(1, dom, '105 квартира, подмес', 100, 'Костя')

    povtor = H.zapisat_nahodku(2, dom, '105 квартира, подмес воды', 100, 'Костя')

    assert povtor is None
    assert len(db.flat_notes(dom['id'], 105)) == 1


def test_bez_doma_nichego_ne_pishem():
    assert H.zapisat_nahodku(1, None, '105 квартира, подмес', 100, 'Костя') is None


# ---------- Где это видно ----------

def test_nahodka_vidna_v_kartochke_stoyaka():
    """Карточку стояка открывают перед выездом — предупредить надо там."""
    dom = houses.detect_house(DOM)
    db.add_flat_note(dom['id'], 105, 'Нашёл подмес воды', kind=flats.kind_of('подмес'), author='Костя')
    from bot import risers as risers_mod

    block, addr = risers_mod.find_block(DOM)
    if not block:
        pytest.skip('по этому дому нет таблицы стояков')
    text = H.riser_card_text(block, addr, 105, 1, 1, [])

    assert 'уже находили' in text
    assert 'подмес' in text


def test_nahodka_vidna_v_pasporte_doma():
    dom = houses.detect_house(DOM)
    db.add_flat_note(dom['id'], 105, 'Нашёл подмес воды', kind=flats.kind_of('подмес'), author='Костя')

    text = H.passport_text(dom)

    assert 'НАХОДКИ ПО КВАРТИРАМ' in text
    assert 'кв. 105' in text


async def test_ekran_po_kvartiram():
    dom = houses.detect_house(DOM)
    db.add_flat_note(dom['id'], 105, 'Нашёл подмес воды', kind=flats.kind_of('подмес'), author='Костя')
    db.add_flat_note(dom['id'], 12, 'Течь под мойкой', kind='течь', author='Игорь')

    msg = Msg('')
    await H.run_action(f"fl:{dom['id']}", msg, 100, event(''))

    otvet = msg.sent[-1]
    assert 'кв. 105' in otvet and 'подмес' in otvet
    assert 'кв. 12' in otvet and 'Течь' in otvet
