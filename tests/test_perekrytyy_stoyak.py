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
    chto, dom, kvartira, _ = stoyak.parse(fraza)
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
    shapka, rabochiy, zhiltsam = e.message.sent[-3:]
    assert 'Ниже два готовых текста' in shapka
    assert rabochiy.startswith('🚫 Перекрыт стояк'), 'пересылается как есть'
    assert 'Седова 65а/3' in rabochiy
    assert '105' in rabochiy and '35' in rabochiy, 'весь стояк перечислен'
    assert 'Андрей' in rabochiy
    assert zhiltsam.startswith('Уважаемые жильцы!'), 'второй текст — для жильцов'
    assert 'Ниже' not in rabochiy and 'Ниже' not in zhiltsam, 'ничего служебного'
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

    teksty = e.message.sent[-2:]
    assert teksty[0].startswith('✅ Стояк открыт')
    assert 'Вода подана' in teksty[0]
    assert teksty[1].startswith('Уважаемые жильцы!')
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


# ---------- Что именно перекрыто ----------

@pytest.mark.parametrize('fraza,res', [
    ('перекрыл стояк гвс по 105 квартире на 65а/3', 'горячая вода'),
    ('перекрыл стояк хвс по 105 квартире на 65а/3', 'холодная вода'),
    ('перекрыл стояк отопления по 105 квартире на 65а/3', 'отопление'),
    ('перекрыл стояк по 105 квартире на 65а/3', 'вода'),
])
def test_resurs_ne_vydumyvaetsya(fraza, res):
    """Нельзя объявлять жильцам, что нет горячей, если перекрыли холодную."""
    assert stoyak.parse(fraza)[3] == res


async def test_v_rabochem_chate_ukazan_resurs():
    text = 'перекрыл стояк гвс по 105 квартире на 65а/3'
    e = event(text)

    await H.handle_shutoff(e, text, 100)

    assert any('Без горячей воды' in t for t in e.message.sent)
    assert any('горячего водоснабжения' in t for t in e.message.sent)


# ---------- Объявление жильцам ----------

def test_tekst_zhiltsam_delovoy():
    text = stoyak.zhiltsam('Седова 65а/3', [7, 14, 105], '09:15',
                           zakryt=True, res='горячая вода')

    assert text.startswith('Уважаемые жильцы!')
    assert 'стояк горячего водоснабжения' in text
    assert '7, 14, 105' in text
    assert '•' in text, 'просьбы пунктами'
    assert 'Приносим извинения' in text
    assert 'Жемчужина' in text


def test_zhiltsam_ne_nazyvayut_kvartiru_avarii_i_santehnika():
    """Соседям незачем знать, у кого течёт и кто приезжал."""
    text = stoyak.zhiltsam('Седова 65а/3', [7, 14, 105], '09:15',
                           zakryt=True, res='вода')

    assert 'Андрей' not in text
    assert 'кв. 105' not in text, 'квартира-источник не называется'


def test_tekst_zhiltsam_o_podache():
    text = stoyak.zhiltsam('Седова 65а/3', [7, 14], '11:05',
                           zakryt=False, res='горячая вода')

    assert 'Подача горячей воды' in text
    assert 'возобновлена' in text
    assert 'Благодарим' in text


async def test_knopka_dlya_zhiltsov_poyavlyaetsya_tolko_s_privyazkoy():
    dom = houses.detect_house('Седова 65а/3')
    text = 'перекрыл стояк по 105 квартире на 65а/3'

    e = event(text)
    await H.handle_shutoff(e, text, 100)
    sid = db.open_shutoffs()[0]['id']
    kb = H._stoyak_kb(sid, dom['id'])
    payloads = [b.payload for row in kb.payload for b in row]
    assert not any(p.startswith('stdom') for p in payloads), 'чат дома не привязан'

    db.bind_house_chat(9, dom['id'], by_name='Андрей')
    kb2 = H._stoyak_kb(sid, dom['id'])
    payloads2 = [b.payload for row in kb2.payload for b in row]
    assert any(p.startswith('stdom') for p in payloads2)


async def test_otpravka_zhiltsam_uhodit_v_chat_doma():
    dom = houses.detect_house('Седова 65а/3')
    db.bind_house_chat(9, dom['id'], by_name='Андрей')
    db.add_chat_record(chat_id=7, mid='m', user_id=100, user_name='Костя', text='привет')
    text = 'перекрыл стояк гвс по 105 квартире на 65а/3'
    await H.handle_shutoff(event(text), text, 100)
    sid = db.open_shutoffs()[0]['id']

    bot = Bot()
    e = event(bot=bot)
    await H.run_action(f'stdom:{sid}', e.message, 100, e)

    assert len(bot.sent) == 1
    chat_id, soobschenie = bot.sent[0]
    assert chat_id == 9, 'ушло в чат дома, а не в обслуживание'
    assert soobschenie.startswith('Уважаемые жильцы!')
    assert 'горячего водоснабжения' in soobschenie


# ---------- Пересылка руками ----------

async def test_teksty_prihodyat_otdelnymi_soobscheniyami():
    """Пока чаты не привязаны, заказчик пересылает сообщение целиком.

    Значит, в нём не должно быть ни строчки служебного: «вот что напишу»
    уедет в домовой чат вместе с объявлением.
    """
    text = 'перекрыл стояк по 105 квартире на 65а/3'
    e = event(text)

    await H.handle_shutoff(e, text, 100)

    assert len(e.message.sent) >= 3, 'шапка и два текста — разными сообщениями'
    zhiltsam = e.message.sent[-1]
    assert zhiltsam.startswith('Уважаемые жильцы!')
    assert zhiltsam.endswith('Управляющая компания «Жемчужина»')
    assert 'Андрей' not in zhiltsam and 'кв. 105' not in zhiltsam


async def test_moimi_slovami_perekladyvaet_prichinu(monkeypatch):
    """«Топит офисное помещение» должно попасть в объявление жильцам."""
    text = ('топит офисное помещение, перекрыл стояк по 105 квартире на 65а/3 '
            'до вечера')
    await H.handle_shutoff(event(text), text, 100)
    sid = db.open_shutoffs()[0]['id']

    vidno = {}

    async def fake_ask(prompt, **kw):
        vidno['prompt'] = prompt
        return 'Уважаемые жильцы!\n\nПодтопление в нежилом помещении.'

    from bot import announce
    monkeypatch.setattr(announce.ai, 'ask', fake_ask)

    msg = Msg()
    await H.run_action(f'stwords:{sid}', msg, 100, event())

    assert 'офисное помещение' in vidno['prompt'], 'слова человека уходят модели'
    assert '105' in vidno['prompt'] and '35' in vidno['prompt'], 'и список квартир'
    assert msg.sent[-1].startswith('Уважаемые жильцы!')


# ---------- «Открыл стояк» без адреса ----------

# Люся сама напомнила: «стояк на Седова 71, кв. 1 перекрыт уже 4 ч. Если
# открыли — напишите "открыл стояк"». Андрей написал ровно это — а она
# потребовала уточнить адрес, хотя перекрытый стояк был один

async def test_otkryl_bez_adresa_kogda_stoyak_odin():
    text = 'перекрыл стояк по 105 квартире на 65а/3'
    await H.handle_shutoff(event(text), text, 100)

    otvet = 'Открыл стояк ещё вчера. Забыл сказать'
    e = event(otvet)
    vzyala = await H.handle_shutoff(e, otvet, 100)

    assert vzyala is True
    assert db.open_shutoffs() == [], 'запись закрыта без переспросов'
    assert any('Стояк открыт' in t for t in e.message.sent)


async def test_otkryl_bez_adresa_kogda_stoyakov_neskolko():
    for text in ('перекрыл стояк по 105 квартире на 65а/3',
                 'перекрыл стояк по 4 квартире на Трилиссера 8/1'):
        await H.handle_shutoff(event(text), text, 100)

    otvet = 'Открыл стояк'
    e = event(otvet)
    await H.handle_shutoff(e, otvet, 100)

    assert 'Какой открыли' in e.message.sent[-1]
    assert 'Седова 65а/3' in e.message.sent[-1]
    assert H.STATE[100]['mode'] == 'stoyak_otkryt'
    assert len(db.open_shutoffs()) == 2, 'наугад ничего не закрыли'


async def test_korotkiy_otvet_zakryvaet_nuzhnyy_stoyak():
    """«71 - 1» — так отвечают на вопрос, а не рассказывают."""
    for text in ('перекрыл стояк по 105 квартире на 65а/3',
                 'перекрыл стояк по 1 квартире на Седова 71'):
        await H.handle_shutoff(event(text), text, 100)
    await H.handle_shutoff(event('Открыл стояк'), 'Открыл стояк', 100)

    e = event('71 - 1')
    await H.resume_stoyak(e, '71 - 1', 100, H.STATE[100])

    ostalis = db.open_shutoffs()
    assert len(ostalis) == 1
    assert houses.HOUSES_BY_ID[ostalis[0]['house_id']]['address'] == 'Седова 65а/3'
    assert 100 not in H.STATE


async def test_otkryl_kogda_nichego_ne_perekryto():
    e = event('открыл стояк')

    await H.handle_shutoff(e, 'открыл стояк', 100)

    assert 'не записано' in e.message.sent[-1]


async def test_perekryl_bez_adresa_sprashivaet():
    e = event('перекрыл стояк')

    await H.handle_shutoff(e, 'перекрыл стояк', 100)

    assert 'По какому дому' in e.message.sent[-1]
    assert H.STATE[100]['mode'] == 'stoyak_zakryt'


async def test_otvet_na_vopros_perekrytiya_zavershaet_zapis():
    await H.handle_shutoff(event('перекрыл стояк гвс'), 'перекрыл стояк гвс', 100)

    e = event('65а/3, кв. 105')
    await H.resume_stoyak(e, '65а/3, кв. 105', 100, H.STATE[100])

    zapisi = db.open_shutoffs()
    assert len(zapisi) == 1
    assert zapisi[0]['flat'] == 105
    assert zapisi[0]['res'] == 'горячая вода', 'ресурс из первой фразы не потерян'
