"""Одно и то же видео не должно фиксироваться трижды.

Заказчик прислал видеоотчёт о свище в офисе — Люся расшифровала, спросила
адрес. Потом кто-то ответил на это сообщение — она расшифровала заново.
Потом сообщение переслали в соседний чат — расшифровала в третий раз.
Один и тот же ролик стал тремя записями: два раза без адреса, один раз
с «Красных Мадьяр 14» (адрес был в подписи при пересылке).

Различать ответ, пересылку и повторную отправку по типу события — хрупко:
неизвестно наверняка, как MAX прикладывает вложение в каждом случае. Проще
и надёжнее — заметить, что расшифровка уже была, по самому тексту: голос
тот же, ролик тот же, слова почти не меняются.
"""
import pytest

from bot import db, houses
import bot.handlers as H

SVISCH = ('В офисах обнаружен свищ на розливе ГВС. Кран перекрыл '
          'общедомовой. Насос ГВС отключил. Хомут навряд ли встанет, там '
          'с обратной стороны наварено уже.')


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.SERIES.clear()


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id=None, text=None, link=None, attachments=None):
        self.sent.append(text)


def zapis(chat_id=-100, mid='m1', text=''):
    return db.add_chat_record(chat_id=chat_id, mid=mid, user_id=7,
                              user_name='Андрей', text=text)


# ── сам детектор повтора ────────────────────────────────────────────────

def test_odna_i_ta_zhe_rasshifrovka_povtor():
    rid = zapis()
    db.set_chat_transcript(rid, SVISCH)
    assert db.pohozhaya_rasshifrovka(SVISCH)


def test_nebolshie_raskhozhdeniya_modeli_ne_meshayut():
    """ASR не обязан распознать слово в слово одинаково дважды."""
    rid = zapis()
    db.set_chat_transcript(rid, SVISCH)
    chut_inache = SVISCH.replace('навряд ли', 'вряд ли').replace('обнаружен', 'найден')
    assert db.pohozhaya_rasshifrovka(chut_inache)


def test_drugoe_soobshchenie_ne_povtor():
    rid = zapis()
    db.set_chat_transcript(rid, SVISCH)
    assert not db.pohozhaya_rasshifrovka(
        'Заменил смеситель на кухне, всё работает штатно.')


def test_korotkiy_tekst_ne_sravnivaetsya():
    rid = zapis()
    db.set_chat_transcript(rid, 'да')
    assert not db.pohozhaya_rasshifrovka('да')


def test_povtor_naydyotsya_v_drugom_chate():
    """Ровно случай заказчика: видео уехало в соседний чат."""
    rid = zapis(chat_id=-100)
    db.set_chat_transcript(rid, SVISCH)
    assert db.pohozhaya_rasshifrovka(SVISCH)


def test_staraya_rasshifrovka_ne_schitaetsya(monkeypatch):
    import bot.db as dbmod
    from datetime import datetime, timedelta

    rid = zapis()
    db.set_chat_transcript(rid, SVISCH)
    davno = (datetime.now(dbmod.IRKUTSK_TZ) - timedelta(hours=72)).strftime('%d.%m.%Y %H:%M')
    with dbmod._conn() as c:
        c.execute('UPDATE chat_messages SET created_at = ? WHERE id = ?', (davno, rid))
    assert not db.pohozhaya_rasshifrovka(SVISCH, hours=48)


# ── реальный сценарий: transcribe_later не повторяет объявление ────────

async def test_tot_samyy_sluchay_tri_sobytiya(monkeypatch):
    """Оригинал, ответ на него, пересылка в другой чат — событие одно."""
    import asyncio

    async def fake_ask(prompt, **kw):
        return 'Свищ на розливе ГВС в офисе, хомут поставить не получится.'

    monkeypatch.setattr(H.ai, 'ask', fake_ask)
    monkeypatch.setattr(H, 'SERIES_WINDOW', 0.05)
    bot = FakeBot()

    # 1. Оригинал в «Сантехники»
    rid1 = zapis(chat_id=-1, mid='orig')
    await H.transcribe_later(rid1, None, bot=bot, chat_id=-1, mid='orig',
                             gotovo=SVISCH)
    assert db.get_chat_record(rid1)['transcript']
    await asyncio.sleep(0.3)
    otveta_posle_originala = len(bot.sent)
    assert otveta_posle_originala >= 1

    # 2. Кто-то отвечает на это сообщение — MAX прикладывает то же видео
    rid2 = zapis(chat_id=-1, mid='reply')
    await H.transcribe_later(rid2, None, bot=bot, chat_id=-1, mid='reply',
                             gotovo=SVISCH)
    assert db.get_chat_record(rid2)['transcript'] is None, \
        'дубль не должен получить транскрипт — иначе его подхватит хроника дома'
    await asyncio.sleep(0.3)
    assert len(bot.sent) == otveta_posle_originala, 'на ответ второго объявления быть не должно'

    # 3. Пересылка в чат «Обслуживание» — другой chat_id, тот же ролик
    rid3 = zapis(chat_id=-2, mid='forward')
    await H.transcribe_later(rid3, None, bot=bot, chat_id=-2, mid='forward',
                             gotovo=SVISCH)
    assert db.get_chat_record(rid3)['transcript'] is None
    await asyncio.sleep(0.3)
    assert len(bot.sent) == otveta_posle_originala, 'и на пересылку тоже'


async def test_deystvitelno_novoe_video_ne_glushitsya(monkeypatch):
    """Дубли ловим, а два разных отчёта подряд — не ложная тревога."""
    import asyncio

    async def fake_ask(prompt, **kw):
        return 'Коротко.'

    monkeypatch.setattr(H.ai, 'ask', fake_ask)
    monkeypatch.setattr(H, 'SERIES_WINDOW', 0.05)
    bot = FakeBot()

    rid1 = zapis(chat_id=-1, mid='m1')
    await H.transcribe_later(rid1, None, bot=bot, chat_id=-1, mid='m1',
                             gotovo=SVISCH)
    await asyncio.sleep(0.3)
    posle_pervogo = len(bot.sent)

    rid2 = zapis(chat_id=-1, mid='m2')
    await H.transcribe_later(
        rid2, None, bot=bot, chat_id=-1, mid='m2',
        gotovo='Заменил прокладку на вводе холодной воды, течь устранена.')
    await asyncio.sleep(0.3)

    assert db.get_chat_record(rid2)['transcript'] is not None
    assert len(bot.sent) > posle_pervogo, 'второй, по-настоящему новый отчёт объявить надо'
