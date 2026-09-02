"""Библиотека теряла события, которые не сумела разобрать.

Пересланное голосовое до Люси не доходило вовсе: maxapi писала в лог
«неизвестный тип обновления: message_created» и выбрасывала событие
целиком. Обновиться было некуда — стояла самая свежая версия.
"""
import pytest

from bot import db, maxfix


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    """Подбор смотрит в известные диалоги — им нужна база."""
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


@pytest.fixture(autouse=True)
def chisto():
    maxfix.RAW.clear()


def sobytie(attachments=None, link_attachments=None, text=None, mid='m1'):
    ev = {
        'update_type': 'message_created',
        'timestamp': 1,
        'message': {
            'sender': {'user_id': 100, 'first_name': 'Андрей', 'name': 'Андрей',
                       'is_bot': False, 'last_activity_time': 1},
            'recipient': {'chat_type': 'dialog', 'user_id': 100},
            'timestamp': 1,
            'body': {'mid': mid, 'seq': 1, 'text': text,
                     'attachments': attachments or []},
        },
    }
    if link_attachments is not None:
        ev['message']['link'] = {
            'type': 'forward',
            'message': {'mid': 'm0', 'seq': 0, 'text': None,
                        'attachments': link_attachments},
        }
    return ev


GOLOS = {'type': 'audio', 'transcription': 'Перекрыл стояк на 65а/3',
         'payload': {'url': 'https://x/a.ogg'}}


# ---------- Чтение вложений в обход библиотеки ----------

def test_rech_chitaetsya_iz_syrogo_sobytiya():
    maxfix._zapomnit(sobytie([GOLOS]))

    gotovo, url = maxfix.speech_from_raw('m1')

    assert gotovo == 'Перекрыл стояк на 65а/3'
    assert url == 'https://x/a.ogg'


def test_peresylannoe_golosovoe_tozhe_vidno():
    maxfix._zapomnit(sobytie([], link_attachments=[GOLOS]))

    gotovo, url = maxfix.speech_from_raw('m1')

    assert gotovo == 'Перекрыл стояк на 65а/3'


def test_video_ssylka_iz_urls():
    video = {'type': 'video', 'urls': {'mp4_480': 'https://x/v.mp4'}}
    maxfix._zapomnit(sobytie([video]))

    assert maxfix.speech_from_raw('m1')[1] == 'https://x/v.mp4'


def test_kartinka_ne_rech():
    maxfix._zapomnit(sobytie([{'type': 'image', 'payload': {'url': 'https://x/p.jpg'}}]))

    assert maxfix.speech_from_raw('m1') == (None, None)


def test_zapas_ne_rastyot_beskonechno():
    for i in range(maxfix.RAW_LIMIT + 20):
        maxfix._zapomnit(sobytie([], mid=f'm{i}'))

    assert len(maxfix.RAW) == maxfix.RAW_LIMIT
    assert 'm0' not in maxfix.RAW, 'старые вытесняются'


# ---------- Починка события ----------

async def test_nerazobrannoe_sobytie_vsyo_ravno_dohodit(monkeypatch, caplog):
    """Раньше такое событие просто исчезало."""
    async def fake_enrich(event_object=None, bot=None):
        return event_object

    monkeypatch.setattr(maxfix, 'enrich_event', fake_enrich)
    slomannoe = sobytie([{'type': 'audio', 'payload': {'кривое': 'поле'}}],
                        text='привет')
    slomannoe['message']['link'] = {'type': 'непонятно', 'message': {}}

    with caplog.at_level('INFO'):
        obj = await maxfix.get_update_model(slomannoe, bot=None)

    assert obj is not None, 'сообщение доехало'
    assert obj.message.body.text == 'привет'
    assert any('Сырое событие' in r.message for r in caplog.records), \
        'и причина записана в лог'


async def test_rech_sohranyaetsya_dazhe_iz_slomannogo(monkeypatch):
    async def fake_enrich(event_object=None, bot=None):
        return event_object

    monkeypatch.setattr(maxfix, 'enrich_event', fake_enrich)
    slomannoe = sobytie([GOLOS])
    slomannoe['message']['link'] = {'type': 'непонятно', 'message': {}}

    await maxfix.get_update_model(slomannoe, bot=None)

    assert maxfix.speech_from_raw('m1')[0] == 'Перекрыл стояк на 65а/3'


async def test_normalnoe_sobytie_prohodit_kak_i_ranshe(monkeypatch):
    async def fake_enrich(event_object=None, bot=None):
        return event_object

    monkeypatch.setattr(maxfix, 'enrich_event', fake_enrich)

    obj = await maxfix.get_update_model(sobytie(text='привет'), bot=None)

    assert obj.message.body.text == 'привет'
    assert maxfix.raw_message('m1'), 'сырое запомнено в любом случае'


# ---------- Уведомление без сообщения ----------

# MAX присылает про голосовое в личке пустое уведомление: только метка
# времени. Текстовые приходят целиком — значит, дело в аудио

PUSTOE = {'timestamp': 1788328220091, 'user_locale': 'ru',
          'update_type': 'message_created'}


def test_pustoe_uvedomlenie_uznayotsya():
    assert maxfix.pustoe(PUSTOE) is True
    assert maxfix.pustoe(sobytie(text='привет')) is False


class FakeBot:
    def __init__(self, messages):
        self.messages = messages
        self.zaprosy = []

    async def get_chats(self, count=None):
        return types_ns(chats=[types_ns(chat_id=7), types_ns(chat_id=9)])

    async def get_messages(self, chat_id=None, count=None, **kw):
        self.zaprosy.append(chat_id)
        return types_ns(messages=self.messages.get(chat_id, []))


def types_ns(**kw):
    import types as t
    return t.SimpleNamespace(**kw)


def soobschenie(mid, ts, text=None, attachments=None):
    return types_ns(timestamp=ts, body=types_ns(
        mid=mid, text=text, attachments=attachments or []))


@pytest.fixture(autouse=True)
def chisto_vzyato():
    maxfix.VZYATO.clear()
    maxfix._CHATY.update(kogda=0.0, spisok=[])


async def test_poteryannoe_soobschenie_podbiraetsya(monkeypatch):
    ts = PUSTOE['timestamp']
    bot = FakeBot({7: [soobschenie('m9', ts, 'Перекрыл стояк')]})
    poluchennoe = []

    async def lovlyu(event):
        poluchennoe.append(event.message.body.text)

    monkeypatch.setattr(maxfix, 'ON_RECOVERED', lovlyu)

    await maxfix.podobrat(bot, ts)

    assert poluchennoe == ['Перекрыл стояк']


async def test_staroe_ne_podbiraem(monkeypatch):
    ts = PUSTOE['timestamp']
    staroe = soobschenie('m1', ts - 10 * 60 * 1000, 'вчерашнее')
    bot = FakeBot({7: [staroe]})
    poluchennoe = []

    async def lovlyu(event):
        poluchennoe.append(event.message.body.text)

    monkeypatch.setattr(maxfix, 'ON_RECOVERED', lovlyu)

    await maxfix.podobrat(bot, ts)

    assert poluchennoe == [], 'подбираем только свежее'


async def test_odno_i_to_zhe_ne_dvoitsya(monkeypatch):
    ts = PUSTOE['timestamp']
    bot = FakeBot({7: [soobschenie('m9', ts, 'Перекрыл стояк')]})
    poluchennoe = []

    async def lovlyu(event):
        poluchennoe.append(event.message.body.text)

    monkeypatch.setattr(maxfix, 'ON_RECOVERED', lovlyu)

    await maxfix.podobrat(bot, ts)
    await maxfix.podobrat(bot, ts)

    assert len(poluchennoe) == 1


async def test_spisok_chatov_kesiruetsya():
    bot = FakeBot({})

    await maxfix._dialogi(bot)
    spisok = await maxfix._dialogi(bot)

    assert spisok == [7, 9]


def test_metka_v_sekundah_tozhe_ponimaetsya():
    """MAX присылает миллисекунды не везде — секунды меньше триллиона."""
    assert maxfix._v_ms(1788328828928) == 1788328828928
    assert maxfix._v_ms(1788328828) == 1788328828000
    assert maxfix._v_ms(None) == 0


async def test_podbiraem_i_kogda_metka_v_sekundah(monkeypatch):
    ts = PUSTOE['timestamp']
    v_sekundah = soobschenie('m9', ts // 1000, 'Перекрыл стояк')
    bot = FakeBot({7: [v_sekundah]})
    poluchennoe = []

    async def lovlyu(event):
        poluchennoe.append(event.message.body.text)

    monkeypatch.setattr(maxfix, 'ON_RECOVERED', lovlyu)

    await maxfix.podobrat(bot, ts)

    assert poluchennoe == ['Перекрыл стояк'], 'секунды не должны мешать'


# ---------- Личный диалог ----------

# В списке чатов бота диалогов нет вовсе: MAX вернул два групповых чата
# с отрицательными id. Chat_id лички известен только из входящих сообщений

async def test_lichnyy_dialog_ischetsya_tozhe(monkeypatch):
    ts = PUSTOE['timestamp']
    db.remember_dialog(470264057, user_id=162131049)
    bot = FakeBot({470264057: [soobschenie('m9', ts, 'Перекрыл стояк')]})
    poluchennoe = []

    async def lovlyu(event):
        poluchennoe.append(event.message.body.text)

    monkeypatch.setattr(maxfix, 'ON_RECOVERED', lovlyu)

    await maxfix.podobrat(bot, ts)

    assert poluchennoe == ['Перекрыл стояк']
    assert 470264057 in bot.zaprosy, 'в личку заглянули'


async def test_dialog_ischetsya_pervym(monkeypatch):
    """Голосовое шлют в личку — туда и смотреть в первую очередь."""
    db.remember_dialog(470264057)
    bot = FakeBot({})

    spisok = await maxfix._dialogi(bot)

    assert spisok[0] == 470264057


def test_chat_id_lichki_zapominaetsya():
    import types as t
    import bot.handlers as H

    event = t.SimpleNamespace(message=t.SimpleNamespace(
        recipient=t.SimpleNamespace(chat_type='dialog', chat_id=470264057,
                                    user_id=363742352)))
    H.zapomnit_dialog(event)

    assert 470264057 in db.dialog_chats()


def test_gruppovoy_chat_ne_schitaem_dialogom():
    import types as t
    import bot.handlers as H

    event = t.SimpleNamespace(message=t.SimpleNamespace(
        recipient=t.SimpleNamespace(chat_type='chat', chat_id=-69324053039792,
                                    user_id=None)))
    H.zapomnit_dialog(event)

    assert db.dialog_chats() == []


def test_dialog_zapominaetsya_iz_syrogo_sobytiya():
    """Иначе замкнутый круг: голосовые не доезжают, а номер лички — только
    из доехавшего сообщения."""
    maxfix._zapomnit({'update_type': 'message_created', 'message': {
        'recipient': {'user_id': 363742352, 'chat_id': 470264057,
                      'chat_type': 'dialog'},
        'body': {'mid': 'm1', 'text': '', 'attachments': []}}})

    assert 470264057 in db.dialog_chats()


def test_gruppovoy_chat_iz_syrogo_ne_zapominaem():
    maxfix._zapomnit({'update_type': 'message_created', 'message': {
        'recipient': {'chat_id': -69324053039792, 'chat_type': 'chat'},
        'body': {'mid': 'm2', 'text': 'привет', 'attachments': []}}})

    assert db.dialog_chats() == []


async def test_dialog_zapominaetsya_dazhe_kogda_sobytie_ne_razobralos(monkeypatch):
    async def fake_enrich(event_object=None, bot=None):
        return event_object

    monkeypatch.setattr(maxfix, 'enrich_event', fake_enrich)
    slomannoe = sobytie(text='привет')
    slomannoe['message']['recipient'] = {'user_id': 363742352,
                                         'chat_id': 470264057,
                                         'chat_type': 'dialog'}
    slomannoe['message']['link'] = {'type': 'непонятно', 'message': {}}

    await maxfix.get_update_model(slomannoe, bot=None)

    assert 470264057 in db.dialog_chats()
