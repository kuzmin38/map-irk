"""«Перекрыл стояк» — чат сам узнаёт, кого отключили.

Заказчик: «перекрываю стояк по квартире, пишу Люсе в личку, а она находит
стояк по шахматке и пишет в чат обслуживания, что перекрыт стояк по такой-то
квартире, отключение воды по таким-то квартирам, перекрыл такой-то».
"""
import types

import pytest

from bot import db, houses, stoyak
import bot.handlers as H


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id=None, user_id=None, text=None, link=None):
        self.sent.append((chat_id, text))


class Msg:
    def __init__(self, text=''):
        self.body = types.SimpleNamespace(text=text, attachments=None, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=100, chat_id=None, chat_type='dialog')
        self.sent = []
        self.link = None

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')


def event(text='', bot=None):
    e = types.SimpleNamespace()
    e.message = Msg(text)
    e.bot = bot or Bot()
    e.callback = types.SimpleNamespace(
        user=types.SimpleNamespace(user_id=100, full_name='Андрей'))
    return e


# ---------- Разбор просьбы ----------

@pytest.mark.parametrize('fraza,kv', [
    ('перекрыл стояк по 105 квартире на 65а/3', 105),
    ('Перекрыл стояк Трилиссера 8/1 кв 4', 4),
    ('перекрыл стояк на Седова 71/1, 105', 105),
])
def test_prosba_uznayotsya(fraza, kv):
    chto, dom, kvartira = stoyak.parse(fraza)
    assert chto == 'zakryl'
    assert kvartira == kv
    assert dom is not None


def test_otkryl_otlichaetsya_ot_perekryl():
    assert stoyak.parse('открыл стояк на 65а/3, кв 105')[0] == 'otkryl'


@pytest.mark.parametrize('fraza', [
    'перекрыл кран в 105 квартире на 65а/3',   # кран, а не стояк
    'поехал на 65а/3',
    'стояк холодный на 65а/3',                 # ни перекрыл, ни открыл
])
def test_lishnee_ne_lovim(fraza):
    assert stoyak.parse(fraza) is None


def test_stoyak_beryotsya_iz_shahmatki():
    adres, etazh, nomer, kvartiry = stoyak.naydi_stoyak('Седова 65а/3', 105)

    assert nomer == 7
    assert 105 in kvartiry
    assert 7 in kvartiry and 35 in kvartiry, 'весь столб снизу доверху'
    assert len(kvartiry) == 15


# ---------- Сквозной путь ----------

async def test_perekryl_pokazyvaet_chernovik_a_ne_shlyot_srazu():
    text = 'перекрыл стояк по 105 квартире на 65а/3'
    e = event(text)

    vzyala = await H.handle_shutoff(e, text, 100)

    assert vzyala is True
    chernovik = e.message.sent[-1]
    assert 'Перекрыт стояк' in chernovik
    assert 'Седова 65а/3' in chernovik
    assert '105' in chernovik and '35' in chernovik, 'весь стояк перечислен'
    assert 'Андрей' in chernovik
    assert e.bot.sent == [], 'в чат ничего не ушло без подтверждения'
    assert len(db.open_shutoffs()) == 1


async def test_podtverzhdenie_otpravlyaet_v_rabochiy_chat():
    db.add_chat_record(chat_id=7, mid='m', user_id=100, user_name='Костя', text='привет')
    text = 'перекрыл стояк по 105 квартире на 65а/3'
    e = event(text)
    await H.handle_shutoff(e, text, 100)
    sid = db.open_shutoffs()[0]['id']

    bot = Bot()
    e2 = event(bot=bot)
    await H.run_action(f'stsend:{sid}', e2.message, 100, e2)

    assert len(bot.sent) == 1
    chat_id, soobschenie = bot.sent[0]
    assert chat_id == 7
    assert 'Перекрыт стояк' in soobschenie and '105' in soobschenie
    assert db.get_shutoff(sid)['announced'] == 1


async def test_otkaz_ubiraet_zapis():
    text = 'перекрыл стояк по 105 квартире на 65а/3'
    e = event(text)
    await H.handle_shutoff(e, text, 100)
    sid = db.open_shutoffs()[0]['id']

    await H.run_action(f'stdrop:{sid}', e.message, 100, e)

    assert db.open_shutoffs() == []


async def test_otkryl_zakryvaet_zapis_i_soobschaet():
    text = 'перекрыл стояк по 105 квартире на 65а/3'
    await H.handle_shutoff(event(text), text, 100)

    text2 = 'открыл стояк на 65а/3, кв 105'
    e = event(text2)
    await H.handle_shutoff(e, text2, 100)

    assert 'Стояк открыт' in e.message.sent[-1]
    assert 'Вода подана' in e.message.sent[-1]
    assert db.open_shutoffs() == [], 'запись закрыта'


async def test_otkryt_mozhno_po_lyuboy_kvartire_stoyaka():
    """Перекрывали по 105-й, а сказать могут про 35-ю — стояк-то один."""
    text = 'перекрыл стояк по 105 квартире на 65а/3'
    await H.handle_shutoff(event(text), text, 100)

    text2 = 'открыл стояк на 65а/3, кв 35'
    e = event(text2)
    await H.handle_shutoff(e, text2, 100)

    assert db.open_shutoffs() == []


async def test_bez_shahmatki_chestno_govorit():
    dom = next(h for h in houses.ALL_HOUSES if h['address'] == '4-я Советская 26')
    text = f"перекрыл стояк на {dom['address']}, кв 999"
    e = event(text)

    await H.handle_shutoff(e, text, 100)

    assert 'нет шахматки' in e.message.sent[-1] or 'нет кв' in e.message.sent[-1]
    assert db.open_shutoffs() == []


async def test_ekran_perekrytyh_stoyakov():
    text = 'перекрыл стояк по 105 квартире на 65а/3'
    await H.handle_shutoff(event(text), text, 100)

    msg = Msg()
    await H.run_action('stl', msg, 100, event())

    assert 'Седова 65а/3' in msg.sent[-1]
    assert 'кв. 105' in msg.sent[-1]


# ---------- Не забыть открыть ----------

async def test_napominanie_pro_zabytyy_stoyak():
    from bot import reminders

    dom = houses.detect_house('Седова 65а/3')
    sid = db.add_shutoff(dom['id'], 105, 7, 16, [7, 105], by_id=100, by_name='Андрей')
    with db._conn() as c:
        c.execute("UPDATE riser_shutoffs SET closed_at = ?",
                  (('01.01.2026 08:00',)))

    bot = Bot()
    await reminders._check_shutoffs(bot)

    assert bot.sent, 'напомнила'
    assert 'перекрыт' in bot.sent[0][1]
    assert db.get_shutoff(sid)['reminded'] == 1


async def test_napominaem_odin_raz():
    from bot import reminders

    dom = houses.detect_house('Седова 65а/3')
    db.add_shutoff(dom['id'], 105, 7, 16, [105], by_id=100, by_name='Андрей')
    with db._conn() as c:
        c.execute("UPDATE riser_shutoffs SET closed_at = '01.01.2026 08:00'")

    bot = Bot()
    await reminders._check_shutoffs(bot)
    await reminders._check_shutoffs(bot)

    assert len(bot.sent) == 1
