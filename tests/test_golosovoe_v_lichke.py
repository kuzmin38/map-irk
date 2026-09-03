"""Голосовое в личку и объявление жильцам с голоса.

Заказчик наговорил Люсе в личку просьбу переложить его слова деловым
языком для домового чата — и не получил ничего. Расшифровка была заведена
только для рабочего чата: в личке сообщение без текста просто молча
пропускалось.
"""
import types

import pytest

from bot import announce, db, houses
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
    def __init__(self, text='', golos=False):
        att = None
        if golos:
            att = [types.SimpleNamespace(
                type='audio', payload=types.SimpleNamespace(url='https://x/a.ogg'))]
        self.body = types.SimpleNamespace(text=text, attachments=att, mid='m', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей')
        self.recipient = types.SimpleNamespace(user_id=100, chat_id=None, chat_type='dialog')
        self.sent = []
        self.link = None

    async def answer(self, text=None, attachments=None):
        self.sent.append(text or '')


def event(text='', golos=False, bot=None):
    e = types.SimpleNamespace()
    e.message = Msg(text, golos)
    e.bot = bot or Bot()
    e.callback = types.SimpleNamespace(
        user=types.SimpleNamespace(user_id=100, full_name='Андрей'))
    return e


# ---------- Голосовое в личке ----------

async def test_golosovoe_v_lichke_rasshifrovyvaetsya(monkeypatch):
    async def fake(url):
        return 'Перекрыл стояк по 105 квартире на 65а/3'

    monkeypatch.setattr(H.transcribe, 'transcribe_url', fake)
    monkeypatch.setattr(H, 'speech_url', lambda body: 'https://x/a.ogg')

    e = event(golos=True)
    await H.on_text(e)

    assert db.open_shutoffs(), 'работает как с обычным текстом'
    assert not any('Услышала' in t for t in e.message.sent), \
        'услышанное вслух не повторяем — человек и так знает, что сказал'


async def test_neponyatnuyu_zapis_ne_pridumyvaet(monkeypatch):
    async def fake(url):
        return None

    monkeypatch.setattr(H.transcribe, 'transcribe_url', fake)
    monkeypatch.setattr(H, 'speech_url', lambda body: 'https://x/a.ogg')

    e = event(golos=True)
    await H.on_text(e)

    assert 'Не разобрала' in e.message.sent[-1]


async def test_stiker_po_prezhnemu_molchit(monkeypatch):
    monkeypatch.setattr(H, 'speech_url', lambda body: None)

    e = event()
    await H.on_text(e)

    assert e.message.sent == []


# ---------- Объявление жильцам ----------

@pytest.mark.parametrize('fraza', [
    'Люся, сделай объявление жильцам грамотным языком: перекрываем стояк',
    'напиши в домовой чат, что завтра с 10 до 14 не будет воды',
    'перепиши грамотным языком для жильцов: воды не будет до вечера',
])
def test_prosba_ob_obyavlenii_uznayotsya(fraza):
    assert announce.wants_announcement(fraza) is True


@pytest.mark.parametrize('fraza', [
    'перекрыл стояк по 105 квартире на 65а/3',
    'поехал на объект',
])
def test_obychnye_prosby_ne_trogaem(fraza):
    assert announce.wants_announcement(fraza) is False


def test_sama_prosba_v_obyavlenie_ne_popadaet():
    sut = announce.strip_trigger(
        'Люся, сделай объявление жильцам грамотным языком: '
        'перекрываем стояк на 65а/3 с 10 до 14')

    assert 'объявление' not in sut.lower()
    assert 'жильцам' not in sut.lower()
    assert 'Люся' not in sut
    assert '65а/3' in sut and '10 до 14' in sut, 'суть и цифры на месте'


async def test_obyavlenie_pokazyvaetsya_pered_otpravkoy(monkeypatch):
    dom = houses.detect_house('Седова 65а/3')
    db.bind_house_chat(9, dom['id'], by_name='Андрей')

    async def fake_ask(prompt, **kw):
        assert '65а/3' in prompt, 'слова человека уходят модели'
        return 'Уважаемые жильцы!\n\nЗавтра с 10:00 до 14:00 не будет воды.'

    monkeypatch.setattr(announce.ai, 'ask', fake_ask)

    text = 'сделай объявление жильцам: на 65а/3 завтра с 10 до 14 не будет воды'
    e = event(text)
    await H.handle_announcement(e, text, 100)

    assert 'Уважаемые жильцы' in e.message.sent[-1]
    assert e.bot.sent == [], 'без кнопки ничего не ушло'
    assert H.STATE[100]['mode'] == 'obyava'


async def test_knopka_otpravlyaet_v_chat_doma(monkeypatch):
    dom = houses.detect_house('Седова 65а/3')
    db.bind_house_chat(9, dom['id'], by_name='Андрей')

    async def fake_ask(prompt, **kw):
        return 'Уважаемые жильцы!\n\nЗавтра не будет воды.'

    monkeypatch.setattr(announce.ai, 'ask', fake_ask)
    text = 'сделай объявление жильцам: на 65а/3 завтра не будет воды'
    await H.handle_announcement(event(text), text, 100)

    bot = Bot()
    e = event(bot=bot)
    await H.run_action('obsend', e.message, 100, e)

    assert bot.sent == [(9, 'Уважаемые жильцы!\n\nЗавтра не будет воды.')]


async def test_bez_privyazki_otdayot_tekst_dlya_kopirovaniya(monkeypatch):
    async def fake_ask(prompt, **kw):
        return 'Уважаемые жильцы!\n\nЗавтра не будет воды.'

    monkeypatch.setattr(announce.ai, 'ask', fake_ask)

    text = 'сделай объявление жильцам: на 65а/3 завтра не будет воды'
    e = event(text)
    await H.handle_announcement(e, text, 100)

    otvet = e.message.sent[-1]
    assert 'Уважаемые жильцы' in otvet, 'текст всё равно отдан'
    assert '/дом' in otvet, 'и сказано, как привязать чат'


# ---------- Откуда берётся расшифровка ----------

class Vlozhenie:
    def __init__(self, tip='audio', url=None, transcription=None, urls=None):
        self.type = tip
        self.payload = types.SimpleNamespace(url=url) if url else None
        self.transcription = transcription
        self.urls = urls


def telo(*vlozheniya):
    return types.SimpleNamespace(text='', attachments=list(vlozheniya), mid='m')


def test_beryom_rasshifrovku_ot_max():
    """MAX расшифровывает голосовые сам — это быстрее, точнее и бесплатно."""
    body = telo(Vlozhenie(transcription='Перекрыл стояк по 105 квартире на 65а/3'))

    assert H.speech_ready(body) == 'Перекрыл стояк по 105 квартире на 65а/3'


def test_ssylka_na_video_lezhit_v_urls():
    """У видео ссылка не в payload.url, а в urls.mp4_*."""
    body = telo(Vlozhenie('video', urls=types.SimpleNamespace(
        mp4_1080=None, mp4_720=None, mp4_480='https://x/v.mp4',
        mp4_360=None, mp4_240=None, mp4_144=None, hls=None)))

    assert H.speech_url(body) == 'https://x/v.mp4'


def test_ssylka_na_golosovoe_v_payload():
    assert H.speech_url(telo(Vlozhenie(url='https://x/a.ogg'))) == 'https://x/a.ogg'


def test_kartinka_ne_rech():
    body = telo(Vlozhenie('image', url='https://x/p.jpg'))

    assert H.speech_url(body) is None
    assert H.speech_ready(body) is None


def test_v_log_vidno_chto_prishlo():
    """Иначе «не ответила на голосовое» неотличимо от «не получила его»."""
    opis = H.opisat_vlozheniya(telo(Vlozhenie('audio', transcription='привет')))

    assert 'audio' in opis and 'transcription' in opis
    assert H.opisat_vlozheniya(telo()) == 'вложений нет'


async def test_gotovuyu_rasshifrovku_ne_perevodim_zanovo(monkeypatch):
    zvali = []

    async def fake(url):
        zvali.append(url)
        return 'из модели'

    monkeypatch.setattr(H.transcribe, 'transcribe_url', fake)

    e = event()
    e.message.body.attachments = [Vlozhenie(transcription='Перекрыл стояк по 105 квартире на 65а/3')]
    await H.on_text(e)

    assert zvali == [], 'модель не дёргаем, расшифровка уже есть'
    assert db.open_shutoffs(), 'и сказанное сделано'



async def test_lenta_ne_zasoryaetsya(monkeypatch):
    """Заказчик: «я знаю, что наговорил, мне важно, как она ответила»."""
    async def fake(url):
        return 'Перекрыл стояк по 105 квартире на 65а/3'

    monkeypatch.setattr(H.transcribe, 'transcribe_url', fake)
    monkeypatch.setattr(H, 'speech_url', lambda body: 'https://x/a.ogg')

    e = event(golos=True)
    await H.on_text(e)

    sluzhebnye = [t for t in e.message.sent
                  if 'Услышала' in t or 'Слушаю' in t]
    assert sluzhebnye == [], 'ни эха, ни заглушки'
    assert any('Перекрыт стояк' in t for t in e.message.sent), 'только ответ по делу'


async def test_pechataet_vmesto_zaglushki(monkeypatch):
    deystviya = []

    class Bot:
        async def send_action(self, chat_id=None, action=None):
            deystviya.append(chat_id)

    e = event()
    e.bot = Bot()
    e.message.recipient.chat_id = 470264057

    await H.pechataet(e)

    assert deystviya == [470264057]


async def test_pechataet_ne_padaet_bez_bota():
    e = event()
    e.bot = None

    await H.pechataet(e)   # молча ничего не делает


# ---------- Правка готового объявления ----------

# Люся составила объявление, Андрей попросил «убери: проверить запорную
# арматуру в шахте» — а она ответила, что не умеет редактировать списки:
# приняла правку объявления за правку плана работ

@pytest.fixture
def obyava(monkeypatch):
    """Люся составила объявление и ждёт, что с ним делать."""
    async def fake_ask(prompt, **kw):
        return ('Уважаемые жильцы!\n\n'
                '• Просьба проверить запорную арматуру в квартирах.\n'
                '• Проверить запорную арматуру в шахте.\n\n'
                'Управляющая компания «Жемчужина»')

    monkeypatch.setattr(announce.ai, 'ask', fake_ask)
    return fake_ask


async def test_pravka_menyaet_obyavlenie(obyava, monkeypatch):
    text = 'сделай объявление жильцам: на 65а/3 проверка арматуры'
    e = event(text)
    await H.handle_announcement(e, text, 100)

    async def fake_pravka(prompt, **kw):
        assert 'шахте' in prompt, 'правка ушла модели'
        assert 'Уважаемые жильцы' in prompt, 'вместе с прежним текстом'
        return 'Уважаемые жильцы!\n\n• Просьба проверить арматуру в квартирах.'

    monkeypatch.setattr(announce.ai, 'ask', fake_pravka)
    pravka = 'Убери : проверить запорную арматуру в шахте'
    e2 = event(pravka)

    vzyala = await H.handle_pravka_obyavy(e2, pravka, 100)

    assert vzyala is True
    assert 'шахте' not in e2.message.sent[-1]
    assert 'Уважаемые жильцы' in e2.message.sent[-1]


async def test_popravlennoe_mozhno_pravit_snova(obyava, monkeypatch):
    text = 'сделай объявление жильцам: на 65а/3 проверка арматуры'
    await H.handle_announcement(event(text), text, 100)

    async def fake_pravka(prompt, **kw):
        return 'Уважаемые жильцы!\n\nВторая правка.'

    monkeypatch.setattr(announce.ai, 'ask', fake_pravka)
    await H.handle_pravka_obyavy(event('убери шахту'), 'убери шахту', 100)

    assert H.STATE[100]['text'] == 'Уважаемые жильцы!\n\nВторая правка.'


async def test_bez_obyavy_pravku_ne_lovim():
    H.STATE.clear()

    assert await H.handle_pravka_obyavy(event('убери шахту'), 'убери шахту', 100) is False


async def test_staraya_obyava_ne_pravitsya(obyava):
    text = 'сделай объявление жильцам: на 65а/3 проверка арматуры'
    await H.handle_announcement(event(text), text, 100)
    H.STATE[100]['kogda'] -= H.PRAVKA_OKNO + 1

    vzyalas = await H.handle_pravka_obyavy(event('убери шахту'), 'убери шахту', 100)

    assert vzyalas is False, 'через полчаса «убери» уже про другое'


async def test_obychnaya_replika_ne_pravka(obyava):
    text = 'сделай объявление жильцам: на 65а/3 проверка арматуры'
    await H.handle_announcement(event(text), text, 100)

    assert await H.handle_pravka_obyavy(event('спасибо'), 'спасибо', 100) is False


# ---------- Строй объявления ----------

# Первое живое объявление вышло с перепутанным порядком: сначала просьбы,
# потом причина; «отключены квартиры» в прошедшем времени о том, что ещё
# предстоит; и указание сантехнику «начать проверку с первых этажей»

def test_zadanie_zadayot_poryadok():
    z = announce.ZADANIE

    assert 'ПОРЯДОК' in z
    for kusok in ('Уважаемые жильцы', 'Без воды будут квартиры',
                  'Просим вас', 'Управляющая компания'):
        assert kusok in z, f'в задании нет блока: {kusok}'
    assert z.index('Что будет и когда') < z.index('Без воды будут квартиры'), \
        'причина раньше списка квартир'


def test_zadanie_zapreschaet_vydumki_i_proshedshee_vremya():
    z = announce.ZADANIE

    assert 'выдумывать' in z
    assert 'не «отключены квартиры»' in z, 'о предстоящем — в будущем времени'
    assert 'не для жильцов' in z, 'указания рабочим выбрасываются'
    assert 'канцелярит' in z


def test_telefon_beryotsya_iz_spravochnika():
    """Выдуманный телефон в объявлении жильцам хуже отсутствующего."""
    assert announce.telefon_dispetcherskoy() == '48-78-05, доб. 1'

    z = announce.ZADANIE.format(text='X', telefon=announce.telefon_dispetcherskoy())
    assert '48-78-05' in z
    assert 'не меняй ни цифры' in z
    assert 'другого не придумывай' in z


def test_bez_telefona_bloka_prosto_net():
    """Справочник может быть не заполнен — тогда о телефоне ни слова."""
    z = announce.BEZ_TELEFONA

    assert 'Куда обращаться' not in z
    assert '{telefon}' not in z
    assert 'телефоны не указывай вовсе' in z


async def test_v_gotovom_zadanii_est_nomer(monkeypatch):
    vidno = {}

    async def fake_ask(prompt, **kw):
        vidno['prompt'] = prompt
        return 'Уважаемые жильцы!'

    monkeypatch.setattr(announce.ai, 'ask', fake_ask)

    await announce.sostavit('сделай объявление жильцам: на 65а/3 отключение воды')

    assert '48-78-05' in vidno['prompt'], 'настоящий номер уходит модели'
