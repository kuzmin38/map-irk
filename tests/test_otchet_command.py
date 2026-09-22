"""Команда /отчет: цифры по рабочему чату и разбор к ним.

Заказчик: «Люся может выдать какой-то отчет за вот этот промежуток
времени, как мы начали работать по чату обслуживания — сколько там
заявок, где что, какая статистика». Следом: «можно сделать, чтобы она
выдавала не только отчёт, но и анализ. Какие-то рекомендации тоже хорошо
бы было».

Цифры считает код — они должны быть верными и без ИИ. Разбор пишет
модель, но строго по тем же данным: рекомендация без опоры на строку
отчёта — выдумка, по ней поедет бригада.
"""
import types

import pytest

from bot import db
import bot.handlers as H


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


@pytest.fixture(autouse=True)
def bez_ii(monkeypatch):
    """По умолчанию модель молчит: цифры проверяем отдельно от разбора."""
    async def net(*a, **kw):
        return None
    monkeypatch.setattr(H.ai, 'ask', net)


class Event:
    def __init__(self):
        self.sent = []
        outer = self

        class Msg:
            body = types.SimpleNamespace(text='/отчет', attachments=None, mid='m', markup=None)
            sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
            recipient = types.SimpleNamespace(user_id=100, chat_id=None, chat_type='dialog')

            async def answer(self, text=None, attachments=None):
                outer.sent.append(text or '')

        self.message = Msg()
        self.bot = None

    @property
    def text(self):
        return '\n'.join(self.sent)


async def call():
    e = Event()
    await H.run_action('otchet', e.message, 100, e)
    return e.text


# ---------- Цифры ----------

async def test_pustaya_lenta_ne_pokazyvaet_statistiku():
    out = await call()
    assert 'считать нечего' in out


async def test_schitaet_vsego_i_s_domom(monkeypatch):
    house = {'id': 3, 'address': 'Седова 71'}
    monkeypatch.setattr(H.houses, 'HOUSES_BY_ID', {3: house})
    db.add_chat_record(7, 'm1', 100, 'Виталя', 'течёт в подвале', house_id=3, is_issue=True)
    db.add_chat_record(7, 'm2', 100, 'Виталя', 'просто разговор')

    out = await call()
    assert 'Всего сообщений: 2' in out
    assert 'Привязано к домам: 1 сообщ. по 1 домам' in out
    assert 'Похоже на аварийное: 1' in out


async def test_pokazyvaet_samye_aktivnye_doma(monkeypatch):
    doma = {3: {'id': 3, 'address': 'Седова 71'}, 4: {'id': 4, 'address': 'Трилиссера 8'}}
    monkeypatch.setattr(H.houses, 'HOUSES_BY_ID', doma)
    for i in range(3):
        db.add_chat_record(7, f'm{i}', 100, 'Виталя', 'течёт', house_id=3, is_issue=True)
    db.add_chat_record(7, 'm9', 100, 'Виталя', 'проверили', house_id=4)

    out = await call()
    assert 'Седова 71 — 3 сообщ., аварийных 3' in out
    assert 'Трилиссера 8 — 1 сообщ.' in out
    # первым в списке — тот, где сообщений больше
    assert out.index('Седова 71') < out.index('Трилиссера 8')


async def test_pokazyvaet_datu_pervoy_zapisi():
    db.add_chat_record(7, 'm1', 100, 'Виталя', 'первое сообщение')
    out = await call()
    day = db.now().split()[0]
    assert f'с {day} по сегодня' in out


async def test_pokazyvaet_itogi_pasporta(monkeypatch):
    house = {'id': 3, 'address': 'Седова 71'}
    monkeypatch.setattr(H.houses, 'HOUSES_BY_ID', {3: house})
    db.add_chat_record(7, 'm1', 100, 'Виталя', 'нашли причину', house_id=3)
    db.add_house_fact(3, '2027-09-01', 'пробку вырвало с насоса', 'находка')

    out = await call()
    assert 'В паспорта домов записано итогов: 1 по 1 домам' in out


async def test_bez_ii_tsifry_vsyo_ravno_prihodyat():
    """Модель недоступна — отчёт обязан остаться на месте."""
    db.add_chat_record(7, 'm1', 100, 'Виталя', 'просто разговор')
    out = await call()
    assert 'Всего сообщений: 1' in out
    assert 'ИИ сейчас недоступен' in out


# ---------- Разбор ----------

async def test_razbor_prihodit_posle_tsifr(monkeypatch):
    async def fake_ask(prompt, **kw):
        return 'Аварийное копится по одному дому. Посмотрел бы там розлив.'
    monkeypatch.setattr(H.ai, 'ask', fake_ask)
    db.add_chat_record(7, 'm1', 100, 'Виталя', 'течёт', is_issue=True)

    e = Event()
    await H.run_action('otchet', e.message, 100, e)

    assert 'Всего сообщений: 1' in e.sent[0], 'цифры идут первыми'
    assert 'розлив' in e.sent[-1]
    # Люся о себе в женском роде — модель сбивается на мужской
    assert 'Посмотрела бы' in e.sent[-1]


async def test_razboru_otdayut_nahodki_po_kvartiram(monkeypatch):
    """Рекомендация берётся из повторов, а их видно только по находкам."""
    house = {'id': 3, 'address': 'Седова 71'}
    monkeypatch.setattr(H.houses, 'HOUSES_BY_ID', {3: house})
    db.add_chat_record(7, 'm1', 100, 'Виталя', 'обход', house_id=3)
    db.add_flat_note(3, 105, 'нашёл подмес', kind='подмес', author='Виталя')

    uvidela = {}

    async def fake_ask(prompt, **kw):
        uvidela['prompt'] = prompt
        return 'разбор'
    monkeypatch.setattr(H.ai, 'ask', fake_ask)

    e = Event()
    await H.run_action('otchet', e.message, 100, e)

    assert 'кв. 105' in uvidela['prompt']
    assert 'подмес' in uvidela['prompt']


async def test_zabytyy_stoyak_vidno_i_bez_ii(monkeypatch):
    """Стояк на Седова 71 висел перекрытым с 3 сентября, а увидела его
    только модель. Про такое человек должен узнать сам: напоминание о
    стояке уходит один раз, через четыре часа, — пропустил и всё."""
    house = {'id': 3, 'address': 'Седова 71'}
    monkeypatch.setattr(H.houses, 'HOUSES_BY_ID', {3: house})
    db.add_chat_record(7, 'm1', 100, 'Виталя', 'перекрыл', house_id=3)
    db.add_shutoff(3, 105, riser=2, floor=5, flats=[35, 70, 105], by_name='Виталя')

    out = await call()

    assert 'Перекрыты и не открыты — 1' in out
    assert 'Седова 71, кв. 105' in out


async def test_razboru_otdayut_zabytye_stoyaki(monkeypatch):
    house = {'id': 3, 'address': 'Седова 71'}
    monkeypatch.setattr(H.houses, 'HOUSES_BY_ID', {3: house})
    db.add_chat_record(7, 'm1', 100, 'Виталя', 'перекрыл', house_id=3)
    db.add_shutoff(3, 105, riser=2, floor=5, flats=[35, 70, 105], by_name='Виталя')

    uvidela = {}

    async def fake_ask(prompt, **kw):
        uvidela['prompt'] = prompt
        return 'разбор'
    monkeypatch.setattr(H.ai, 'ask', fake_ask)

    e = Event()
    await H.run_action('otchet', e.message, 100, e)

    assert 'Перекрыты и не открыты' in uvidela['prompt']


def test_zadanie_zapreshchaet_obshchie_sovety():
    """«Усилить контроль» — не рекомендация, а способ ничего не сказать."""
    assert 'усилить контроль' in H.OTCHET_RAZBOR
    assert 'не придумывай' in H.OTCHET_RAZBOR


def test_zadanie_zapreshchaet_privetstvie():
    """Первый же разбор начался с «Добрый день!» — в переписке это лишняя
    строка, человек пришёл за делом."""
    assert 'Ни приветствий' in H.OTCHET_RAZBOR


def test_zadanie_zapreshchaet_otgovorku_pro_nehvatku_dannyh():
    """На 762 сообщениях она закончила «данных пока маловато» — это было
    моё же правило, и оно сработало против дела."""
    assert 'не оправдывайся нехваткой данных' in H.OTCHET_RAZBOR


def test_zadanie_ne_prosit_pereskazyvat_tsifry():
    assert 'заново перечислять' in H.OTCHET_RAZBOR


# ---------- Сама команда ----------

def test_komanda_est_v_bystrom_menyu():
    imena = [n for n, _, _ in H.QUICK_COMMANDS]
    assert 'отчет' in imena


def test_komandu_uznayut_i_s_yo():
    """Андрей набрал «/отчёт» — и Люся промолчала: буква другая."""
    assert 'отчёт' in H.ALIASES['отчет']
