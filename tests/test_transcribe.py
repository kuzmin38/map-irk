"""Расшифровка голосовых и видеоотчётов из рабочего чата."""
import asyncio
import types

import pytest

from bot import db, houses, transcribe
import bot.handlers as H


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    H.STATE.clear()


class Att:
    def __init__(self, kind, url):
        self.type = kind
        self.payload = types.SimpleNamespace(url=url)


class Msg:
    def __init__(self, text='', attachments=None):
        self.body = types.SimpleNamespace(text=text, attachments=attachments,
                                          mid='m1', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Константин')
        self.recipient = types.SimpleNamespace(user_id=None, chat_id=7, chat_type='chat')
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text)


def event(text='', attachments=None):
    e = types.SimpleNamespace()
    e.message = Msg(text, attachments)
    e.bot = None
    return e


def house_id(address):
    return next(h['id'] for h in houses.HOUSES if h['address'] == address)


def test_speech_url_finds_video_and_audio():
    body = types.SimpleNamespace(attachments=[Att('image', 'http://x/p.jpg'),
                                              Att('video', 'http://x/v.mp4')])
    assert H.speech_url(body) == 'http://x/v.mp4'

    body = types.SimpleNamespace(attachments=[Att('audio', 'http://x/voice.ogg')])
    assert H.speech_url(body) == 'http://x/voice.ogg'


def test_speech_url_ignores_photos_and_docs():
    body = types.SimpleNamespace(attachments=[Att('image', 'http://x/p.jpg'),
                                              Att('file', 'http://x/d.pdf')])
    assert H.speech_url(body) is None


async def test_transcript_saved_and_house_detected(monkeypatch):
    async def fake_transcribe(url):
        return 'Приехали на Байкальскую 237, в подвале течь на розливе, поставили хомут'
    monkeypatch.setattr(transcribe, 'transcribe_url', fake_transcribe)

    record_id = db.add_chat_record(7, 'm1', 100, 'Константин', '', has_files=True)
    await H.transcribe_later(record_id, 'http://x/v.mp4')

    records = db.house_chat_records(house_id('Байкальская 237'))
    assert len(records) == 1
    assert 'хомут' in records[0]['transcript']
    assert records[0]['is_issue'] == 1          # «течь» распознана как авария


async def test_video_message_schedules_transcription(monkeypatch):
    done = asyncio.Event()
    seen = {}

    async def fake_transcribe(url):
        seen['url'] = url
        done.set()
        return 'Седова 65а/3, заменили манометр на подаче'
    monkeypatch.setattr(transcribe, 'transcribe_url', fake_transcribe)

    await H.on_text(event('', [Att('video', 'http://x/report.mp4')]))
    await asyncio.wait_for(done.wait(), timeout=2)
    await asyncio.sleep(0)                      # даём фоновой задаче дописать

    assert seen['url'] == 'http://x/report.mp4'


async def test_failed_transcription_leaves_record_intact(monkeypatch):
    async def fails(url):
        return None
    monkeypatch.setattr(transcribe, 'transcribe_url', fails)

    record_id = db.add_chat_record(7, 'm1', 100, 'К', 'Трилиссера 8/4 отчёт',
                                   house_id=house_id('Трилиссера 8/4'), has_files=True)
    await H.transcribe_later(record_id, 'http://x/v.mp4')

    records = db.house_chat_records(house_id('Трилиссера 8/4'))
    assert records[0]['transcript'] is None      # расшифровки нет
    assert records[0]['text'] == 'Трилиссера 8/4 отчёт'   # но запись цела


def test_ffmpeg_availability_is_reported():
    # на машине без ffmpeg расшифровка обязана честно отключаться, а не падать
    assert isinstance(transcribe.ffmpeg_available(), bool)


async def test_extract_audio_without_ffmpeg_returns_none(monkeypatch):
    monkeypatch.setattr(transcribe.shutil, 'which', lambda name: None)
    assert await transcribe.extract_audio('/tmp/nonexistent.mp4') is None


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id=None, text=None, link=None, **kw):
        self.sent.append({'chat_id': chat_id, 'text': text, 'link': link})


async def test_emergency_gets_short_reply_in_chat(monkeypatch):
    async def fake_transcribe(url):
        return ('Приехали на Байкальскую 237, в подвале свищ на розливе ХВС, '
                'перекрыли стояк, поставили хомут, завтра меняем участок трубы')
    monkeypatch.setattr(transcribe, 'transcribe_url', fake_transcribe)

    async def fake_ask(prompt, **kw):
        return 'Свищ на розливе ХВС, перекрыли стояк и поставили хомут.'
    monkeypatch.setattr(H.ai, 'ask', fake_ask)

    bot = FakeBot()
    record_id = db.add_chat_record(7, 'm1', 100, 'К', '', has_files=True)
    await H.transcribe_later(record_id, 'http://x/v.mp4', bot=bot, chat_id=7, mid='m1')

    assert len(bot.sent) == 1
    assert 'Байкальская 237' in bot.sent[0]['text']
    assert 'хомут' in bot.sent[0]['text']
    assert bot.sent[0]['link'] is not None          # ответом на само видео


async def test_routine_report_stays_silent(monkeypatch):
    async def fake_transcribe(url):
        return 'Трилиссера 8/4, покрасили трубы в подвале, всё по плану'
    monkeypatch.setattr(transcribe, 'transcribe_url', fake_transcribe)

    bot = FakeBot()
    record_id = db.add_chat_record(7, 'm1', 100, 'К', '', has_files=True)
    await H.transcribe_later(record_id, 'http://x/v.mp4', bot=bot, chat_id=7, mid='m1')

    assert bot.sent == []                           # в чат не встревает
    assert db.house_chat_records(house_id('Трилиссера 8/4'))[0]['transcript']


async def test_summary_falls_back_when_ai_unavailable(monkeypatch):
    async def no_ai(prompt, **kw):
        return None
    monkeypatch.setattr(H.ai, 'ask', no_ai)
    text = 'Байкальская 237 ' + 'очень длинный отчёт ' * 30
    summary = await H.short_summary(text, 'Байкальская 237')
    assert summary.startswith('🎙 Байкальская 237')
    assert len(summary) < 300                       # обрезано, а не простыня
