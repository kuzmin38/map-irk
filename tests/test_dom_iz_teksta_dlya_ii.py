"""Дом ищет код, а не модель — и в чате, и в личке.

В чате бригады Андрей написал: «По 14 дому по гулу; 25 завтра в 8 00
предоставят доступ, 33 гул отсутствует...». Люся переспросила, о каком
доме речь. Он: «Да капец ты тупишь. Написано же по 14 дому. У нас что их
10?» — и получил в ответ: «у нас в обслуживании нет дома с номером 14.
Есть дом Красных Мадьяр 14». Сама себе и возразила: дом с номером 14 у
нас ровно один, это он и есть.

В логе у всех трёх ответов одна пометка: «Ответ без обращения к данным» —
модель не вызвала ни одного инструмента. Запрет выдумывать в задании
стоит давно и не помогает. Поэтому дом определяет код и кладёт готовый
ответ модели прямо в вопрос.

Правку эту первый раз сделали только для лички — в чат она не попала, и
там всё повторилось слово в слово. Теперь путь общий.
"""
import types

import pytest

from bot import db
import bot.handlers as H

# Ровно то, что было написано в чате
IZ_CHATA = ('По 14 дому по гулу; 25 завтра в 8 00 предоставят доступ, 33 гул '
            'отсутствует, говорит что ниже, 49 не живут там. 41 мимо, 1 мимо.')


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


# ---------- Сам помощник ----------

def test_nomer_doma_iz_serediny_teksta():
    vopros = H.s_domom(IZ_CHATA)
    assert 'Красных Мадьяр 14' in vopros
    assert IZ_CHATA in vopros, 'сам текст должен дойти целиком'


def test_uprek_pro_tot_zhe_dom_tozhe_uznayotsya():
    """Вторую фразу она тоже не поняла — а дом в ней назван."""
    assert 'Красных Мадьяр 14' in H.s_domom('Да капец ты тупишь. Написано же '
                                            'по 14 дому. У нас что их 10?')


def test_bez_doma_tekst_ne_menyaetsya():
    vopros = 'какие сроки устранения течи по нормативам?'
    assert H.s_domom(vopros) == vopros


def test_pustoy_tekst_ne_lomaet():
    assert H.s_domom('') == ''


def test_podskazka_velit_ne_peresprashivat():
    """Переспрашивание адреса — это и был весь баг."""
    assert 'переспрашивать адрес не нужно' in H.s_domom(IZ_CHATA)


# ---------- Чат целиком ----------

class Msg:
    def __init__(self, text):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=None, chat_id=7, chat_type='chat')
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text)


def event(text):
    e = types.SimpleNamespace()
    e.message = Msg(text)
    e.bot = None
    return e


async def test_v_chate_model_poluchaet_uzhe_naydennyy_dom(monkeypatch):
    sprosili = {}

    async def fake_answer(uid, name, text, chat_id=None):
        sprosili['text'] = text
        return 'поняла'
    monkeypatch.setattr(H.agent, 'answer', fake_answer)

    await H.on_text(event('Люся, ' + IZ_CHATA))

    assert 'Красных Мадьяр 14' in sprosili['text'], 'иначе она снова спросит адрес'


async def test_v_chate_bez_doma_nichego_ne_pribavlyaetsya(monkeypatch):
    sprosili = {}

    async def fake_answer(uid, name, text, chat_id=None):
        sprosili['text'] = text
        return 'поняла'
    monkeypatch.setattr(H.agent, 'answer', fake_answer)

    await H.on_text(event('Люся, а какие сроки по нормативам на течь?'))

    assert 'определён кодом' not in sprosili['text']
