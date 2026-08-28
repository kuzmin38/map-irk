"""Отчёт привязывается к дому только по настоящему ответу об адресе.

Костя написал в чат обычную рабочую реплику «Только что включил 65/3,4»,
а Люся вытащила оттуда номер дома, приклеила к ней чужой видеоотчёт и
объявила об этом: «Поняла: тот отчёт — Седова 65а/3. Привязала.»

Заказчик: «читает эти цифры и какие-то привязывает отчёты, как не надо.
Там вообще может просто упоминаться дом. Зачем она постоянно об этом
уведомляет? Пусть собирает, но только ключевое».
"""
import types
from datetime import datetime, timedelta

import pytest

from bot import db, houses
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


class Msg:
    def __init__(self, text, uid=100, name='Костя'):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=uid, full_name=name)
        self.recipient = types.SimpleNamespace(user_id=None, chat_id=7, chat_type='chat')
        self.sent = []
        self.link = None

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')


def event(text, uid=100, name='Костя'):
    e = types.SimpleNamespace()
    e.message = Msg(text, uid, name)
    e.bot = None
    return e


def otchyot_bez_doma(uid=100, name='Костя', minut_nazad=0):
    """Видеоотчёт в ленте, к которому дом не привязан."""
    rec = db.add_chat_record(chat_id=7, mid='v1', user_id=uid, user_name=name,
                             text='', has_files=True)
    if minut_nazad:
        kogda = (datetime.now(db.IRKUTSK_TZ)
                 - timedelta(minutes=minut_nazad)).strftime('%d.%m.%Y %H:%M')
        with db._conn() as c:
            c.execute('UPDATE chat_messages SET created_at = ? WHERE id = ?',
                      (kogda, rec))
    return rec


# ---------- Что считать ответом об адресе ----------

@pytest.mark.parametrize('otvet', [
    'Седова 71',
    'это Седова 71',
    'по Советской 30',
    'Трилиссера 8/5',
])
def test_golyy_adres_eto_otvet(otvet):
    dom = houses.detect_house(otvet)
    assert H.tolko_adres(otvet, dom) is True


@pytest.mark.parametrize('rabota', [
    'Только что включил 65/3,4',
    'Завтра поедем на Седова 71 менять кран',
    'На Трилиссера 8/5 всё сделали',
])
def test_rabochaya_replika_eto_ne_otvet(rabota):
    dom = houses.detect_house(rabota)
    assert dom is not None, 'дом в реплике действительно упомянут'
    assert H.tolko_adres(rabota, dom) is False


# ---------- Привязка ----------

def test_replika_kosti_nichego_ne_privyazyvaet():
    """Тот самый случай со скриншота."""
    rec = otchyot_bez_doma()
    text = 'Только что включил 65/3,4'
    e = event(text)

    H.attach_house_to_report(e, rec + 1, houses.detect_house(text), text)

    assert db.get_chat_record(rec)['house_id'] is None, 'отчёт остался как был'
    assert e.message.sent == [], 'и ничего не сказала'


def test_otvet_s_adresom_privyazyvaet_no_molcha():
    rec = otchyot_bez_doma()
    text = 'Седова 71'
    e = event(text)

    H.attach_house_to_report(e, rec + 1, houses.detect_house(text), text)

    dom = houses.detect_house('Седова 71')
    assert db.get_chat_record(rec)['house_id'] == dom['id']
    assert e.message.sent == [], 'служебное действие — не новость'


def test_staryy_otchyot_ne_tseplyaetsya():
    """Адрес называют сразу после ролика, а не через день."""
    rec = otchyot_bez_doma(minut_nazad=90)
    text = 'Седова 71'

    H.attach_house_to_report(event(text), rec + 1, houses.detect_house(text), text)

    assert db.get_chat_record(rec)['house_id'] is None


def test_chuzhoy_otchyot_ne_tseplyaetsya():
    """Адрес одного человека не приклеивается к ролику другого."""
    rec = otchyot_bez_doma(uid=555, name='Игорь')
    text = 'Седова 71'

    H.attach_house_to_report(event(text, uid=100), rec + 1,
                             houses.detect_house(text), text)

    assert db.get_chat_record(rec)['house_id'] is None


# ---------- Поправка человеком остаётся громкой ----------

def test_popravka_rabotaet_i_o_ney_soobschaetsya():
    """«Не 28, а 18б» — человек попросил явно, тут молчать нельзя."""
    dom28 = houses.detect_house('4-я Советская 28')
    rec = db.add_chat_record(chat_id=7, mid='v1', user_id=100, user_name='Костя',
                             text='', house_id=dom28['id'], has_files=True)
    text = 'не 28 дом, а Трилиссера 18б'
    e = event(text)

    popravleno = H.fix_report_house(e, rec + 1, text)

    assert popravleno is True
    assert db.get_chat_record(rec)['house_id'] == houses.detect_house('Трилиссера 18б')['id']


# ---------- Что попадает в ленту дома ----------

@pytest.mark.parametrize('text,klyuchevoe', [
    ('Только что включил 65/3,4', True),
    ('На 65а/2 поменяли задвижку на вводе', True),
    ('Промыли систему, опрессовали', True),
    ('Отлично, крутая УК))))', False),
    ('Добрый день! ГВС возобновили', False),   # новость, работы за ней нет
    ('Ну ниче пацаны, всем привет', False),
])
def test_v_lente_doma_tolko_raboty(text, klyuchevoe):
    rec = db.add_chat_record(chat_id=7, mid='m', user_id=100, user_name='Костя',
                             text=text)
    assert H.znachimo(db.get_chat_record(rec)) is klyuchevoe


def test_otchyot_i_avariya_vsegda_klyuchevye():
    s_faylom = db.add_chat_record(chat_id=7, mid='m', user_id=100, user_name='Костя',
                                  text='', has_files=True)
    avariya = db.add_chat_record(chat_id=7, mid='m', user_id=100, user_name='Костя',
                                 text='течь в подвале', is_issue=True)

    assert H.znachimo(db.get_chat_record(s_faylom)) is True
    assert H.znachimo(db.get_chat_record(avariya)) is True


async def test_ekran_doma_ne_pokazyvaet_boltovnyu():
    dom = houses.detect_house('Седова 71')
    db.add_chat_record(chat_id=7, mid='m', user_id=100, user_name='Костя',
                       text='Отлично, крутая УК))))', house_id=dom['id'])
    db.add_chat_record(chat_id=7, mid='m', user_id=100, user_name='Костя',
                       text='Поменял задвижку на вводе', house_id=dom['id'])

    msg = Msg('')
    await H.run_action(f"chat:{dom['id']}", msg, 100, event(''))

    otvet = msg.sent[-1]
    assert 'задвижку' in otvet
    assert 'крутая УК' not in otvet
    assert 'Ещё 1' in otvet, 'но человек видит, что разговор был'
