"""Протоколы планёрок: длинная запись → расшифровка → протокол → задачи."""
import asyncio
import json
import types

import pytest

from bot import db, houses, meetings, transcribe
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
    def __init__(self, text='', attachments=None, chat_type='dialog'):
        self.body = types.SimpleNamespace(text=text, attachments=attachments,
                                          mid='m1', markup=None)
        self.sender = types.SimpleNamespace(user_id=100, full_name='Андрей Кузьмин')
        self.recipient = types.SimpleNamespace(user_id=100, chat_id=None,
                                               chat_type=chat_type)
        self.sent = []

    async def answer(self, text=None, attachments=None):
        self.sent.append(text)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, user_id=None, chat_id=None, text=None, **kw):
        self.sent.append({'user_id': user_id, 'text': text})


def event(text='', attachments=None):
    e = types.SimpleNamespace()
    e.message = Msg(text, attachments)
    e.bot = FakeBot()
    return e


def house_id(address):
    return next(h['id'] for h in houses.HOUSES if h['address'] == address)


PROTOCOL = {
    'title': 'Опрессовка и аварии',
    'summary': 'Разобрали подготовку к опрессовке и течь на Байкальской.',
    'decisions': ['Опрессовку начать с Квадрума'],
    'tasks': [
        {'title': 'Заменить манометр на подаче', 'assignee': 'Константин',
         'deadline': '25.09.2026', 'address': 'Байкальская 237'},
        {'title': 'Закупить хомуты', 'assignee': None, 'deadline': None,
         'address': None},
    ],
    'questions': ['Кто даёт заявку на автовышку?'],
}


# ---------- Разбор ответа модели ----------

def test_parse_json_plain():
    assert meetings.parse_json('{"title": "Планёрка"}') == {'title': 'Планёрка'}


def test_parse_json_in_markdown_fence():
    answer = 'Вот протокол:\n```json\n{"title": "Планёрка", "tasks": []}\n```\nГотово.'
    assert meetings.parse_json(answer)['title'] == 'Планёрка'


def test_parse_json_broken_returns_none():
    assert meetings.parse_json('Ничего не понял, извините') is None
    assert meetings.parse_json('{это не json}') is None


def test_normalize_fills_missing_fields():
    data = meetings.normalize({})
    assert data['title'] == 'Планёрка'
    assert data['decisions'] == [] and data['tasks'] == [] and data['questions'] == []


def test_normalize_accepts_tasks_as_plain_strings():
    data = meetings.normalize({'tasks': ['Заменить кран', {'title': 'Купить трубу'}]})
    assert [t['title'] for t in data['tasks']] == ['Заменить кран', 'Купить трубу']
    assert data['tasks'][0]['assignee'] is None


def test_normalize_drops_null_strings_in_tasks():
    data = meetings.normalize({'tasks': [{'title': 'Дело', 'assignee': 'null',
                                          'deadline': '-', 'address': 'нет'}]})
    t = data['tasks'][0]
    assert t['assignee'] is None and t['deadline'] is None and t['address'] is None


async def test_build_protocol_uses_ai(monkeypatch):
    asked = {}

    async def fake_ask(prompt, **kw):
        asked['prompt'] = prompt
        return json.dumps(PROTOCOL, ensure_ascii=False)
    monkeypatch.setattr(meetings.ai, 'ask', fake_ask)

    data = await meetings.build_protocol('Речь с планёрки про опрессовку')
    assert data['title'] == 'Опрессовка и аварии'
    assert len(data['tasks']) == 2
    assert 'про опрессовку' in asked['prompt']


async def test_build_protocol_keeps_prose_answer(monkeypatch):
    async def fake_ask(prompt, **kw):
        return 'Обсуждали опрессовку, решений не приняли.'
    monkeypatch.setattr(meetings.ai, 'ask', fake_ask)

    data = await meetings.build_protocol('какая-то речь')
    assert data['summary'].startswith('Обсуждали опрессовку')


async def test_build_protocol_without_ai(monkeypatch):
    async def no_ai(prompt, **kw):
        return None
    monkeypatch.setattr(meetings.ai, 'ask', no_ai)
    assert await meetings.build_protocol('речь') is None


def test_format_protocol_has_all_sections():
    text = meetings.format_protocol(PROTOCOL, held_at='16.08.2026 10:00', duration=3900)
    assert '🗒 Опрессовка и аварии' in text
    assert '1 ч 05 мин' in text
    assert 'Опрессовку начать с Квадрума' in text
    assert 'Заменить манометр на подаче' in text
    assert 'Константин' in text and 'до 25.09.2026' in text
    assert 'Кто даёт заявку на автовышку?' in text


def test_fmt_duration():
    assert meetings.fmt_duration(None) == ''
    assert meetings.fmt_duration(600) == '10 мин'
    assert meetings.fmt_duration(3600) == '1 ч 00 мин'


# ---------- Нарезка длинной записи ----------

async def test_split_audio_keeps_short_file_whole(monkeypatch, tmp_path):
    path = tmp_path / 'a.mp3'
    path.write_bytes(b'x')

    async def short(p):
        return 60.0
    monkeypatch.setattr(transcribe, 'audio_duration', short)

    assert await transcribe.split_audio(str(path), chunk_seconds=600) == [str(path)]


async def test_split_audio_cuts_long_recording(monkeypatch, tmp_path):
    path = tmp_path / 'a.mp3'
    path.write_bytes(b'x')

    async def long(p):
        return 3600.0
    monkeypatch.setattr(transcribe, 'audio_duration', long)

    async def fake_run(*args):
        # ffmpeg -f segment пишет части рядом с исходником
        for i in range(3):
            (tmp_path / f'a.mp3.part{i:03d}.mp3').write_bytes(b'x')
        return 0, b''
    monkeypatch.setattr(transcribe, '_run', fake_run)

    parts = await transcribe.split_audio(str(path), chunk_seconds=600)
    assert len(parts) == 3
    assert parts == sorted(parts)          # порядок кусков = порядок речи


async def test_transcribe_meeting_joins_chunks(monkeypatch, tmp_path):
    async def fake_download(url, dest, max_mb):
        open(dest, 'wb').write(b'x')
        return True

    async def fake_extract(src, max_seconds=None):
        return src + '.mp3'

    async def fake_duration(path):
        return 1800.0

    async def fake_split(path, chunk_seconds=None):
        return [f'{path}.part{i}' for i in range(3)]

    texts = iter(['Первая часть.', 'Вторая часть.', 'Третья часть.'])

    async def fake_transcribe_file(path, prompt=None):
        assert prompt == transcribe.MEETING_PROMPT
        return next(texts)

    monkeypatch.setattr(transcribe, 'download', fake_download)
    monkeypatch.setattr(transcribe, 'extract_audio', fake_extract)
    monkeypatch.setattr(transcribe, 'audio_duration', fake_duration)
    monkeypatch.setattr(transcribe, 'split_audio', fake_split)
    monkeypatch.setattr(transcribe, 'transcribe_file', fake_transcribe_file)

    seen = []

    async def progress(done, total):
        seen.append((done, total))

    result = await transcribe.transcribe_meeting('http://x/meet.mp4', progress=progress)
    assert result['text'] == 'Первая часть.\nВторая часть.\nТретья часть.'
    assert result['duration'] == 1800.0 and result['chunks'] == 3
    assert seen == [(1, 3), (2, 3), (3, 3)]


async def test_transcribe_meeting_survives_lost_chunk(monkeypatch):
    async def fake_download(url, dest, max_mb):
        open(dest, 'wb').write(b'x')
        return True

    async def fake_extract(src, max_seconds=None):
        return src + '.mp3'

    async def fake_split(path, chunk_seconds=None):
        return ['a', 'b']

    answers = iter([None, 'Вторая часть цела.'])

    async def fake_transcribe_file(path, prompt=None):
        return next(answers)

    monkeypatch.setattr(transcribe, 'download', fake_download)
    monkeypatch.setattr(transcribe, 'extract_audio', fake_extract)
    monkeypatch.setattr(transcribe, 'audio_duration', lambda p: _none())
    monkeypatch.setattr(transcribe, 'split_audio', fake_split)
    monkeypatch.setattr(transcribe, 'transcribe_file', fake_transcribe_file)

    result = await transcribe.transcribe_meeting('http://x/meet.mp4')
    assert result['text'] == 'Вторая часть цела.'


async def _none():
    return None


# ---------- Хранение ----------

def test_meeting_round_trip():
    mid = db.add_meeting(100, 'Андрей')
    m = db.get_meeting(mid)
    assert m['status'] == db.MEETING_NEW and m['works_created'] == 0

    db.set_meeting_result(mid, title='Планёрка', transcript='речь',
                          protocol=json.dumps(PROTOCOL, ensure_ascii=False),
                          duration_sec=1200, status=db.MEETING_READY)
    m = db.get_meeting(mid)
    assert m['title'] == 'Планёрка' and m['duration_sec'] == 1200
    assert json.loads(m['protocol'])['tasks'][0]['assignee'] == 'Константин'
    assert [x['id'] for x in db.list_meetings()] == [mid]

    db.delete_meeting(mid)
    assert db.get_meeting(mid) is None


# ---------- Разговор с ботом ----------

async def test_voice_in_private_starts_meeting(monkeypatch):
    """Голосовое в личке — это запись планёрки, разбор уходит в фон."""
    started = {}

    async def fake_process(msg, meeting_id, url):
        started['id'], started['url'] = meeting_id, url
    monkeypatch.setattr(H, 'process_meeting', fake_process)

    e = event('', [Att('audio', 'http://x/meet.ogg')])
    await H.on_text(e)
    await asyncio.sleep(0)          # даём фоновой задаче стартовать

    assert 'Приняла запись' in e.message.sent[0]
    m = db.list_meetings()[0]
    assert m['status'] == db.MEETING_NEW          # протокола ещё нет
    assert started == {'id': m['id'], 'url': 'http://x/meet.ogg'}


async def test_meeting_protocol_is_built_and_saved(monkeypatch):
    async def fake_meeting(url, progress=None):
        return {'text': 'Байкальская 237, течь на розливе', 'duration': 300, 'chunks': 1}
    monkeypatch.setattr(transcribe, 'transcribe_meeting', fake_meeting)

    async def fake_ask(prompt, **kw):
        return json.dumps(PROTOCOL, ensure_ascii=False)
    monkeypatch.setattr(meetings.ai, 'ask', fake_ask)

    e = event()
    mid = db.add_meeting(100, 'Андрей')
    await H.process_meeting(e.message, mid, 'http://x/meet.ogg')

    m = db.get_meeting(mid)
    assert m['status'] == db.MEETING_READY
    assert m['title'] == 'Опрессовка и аварии'
    assert m['transcript'] == 'Байкальская 237, течь на розливе'
    assert m['duration_sec'] == 300
    assert 'Опрессовку начать с Квадрума' in e.message.sent[-1]


async def test_meeting_failure_is_reported(monkeypatch):
    async def fails(url, progress=None):
        return None
    monkeypatch.setattr(transcribe, 'transcribe_meeting', fails)

    e = event('', [Att('video', 'http://x/meet.mp4')])
    mid = db.add_meeting(100, 'Андрей')
    await H.process_meeting(e.message, mid, 'http://x/meet.mp4')

    assert db.get_meeting(mid)['status'] == db.MEETING_FAILED
    assert 'Не получилось разобрать запись' in e.message.sent[-1]


async def test_long_recording_warns_once(monkeypatch):
    async def fake_meeting(url, progress=None):
        for i in range(1, 4):
            await progress(i, 3)
        return {'text': 'речь', 'duration': 1800, 'chunks': 3}
    monkeypatch.setattr(transcribe, 'transcribe_meeting', fake_meeting)

    async def fake_ask(prompt, **kw):
        return json.dumps(PROTOCOL, ensure_ascii=False)
    monkeypatch.setattr(meetings.ai, 'ask', fake_ask)

    e = event()
    mid = db.add_meeting(100, 'Андрей')
    await H.process_meeting(e.message, mid, 'http://x/meet.mp4')

    warnings = [t for t in e.message.sent if 'частями' in t]
    assert len(warnings) == 1, 'о длинной записи предупреждаем один раз'


async def test_transcript_saved_even_without_protocol(monkeypatch):
    async def fake_meeting(url, progress=None):
        return {'text': 'живая речь с планёрки', 'duration': 60, 'chunks': 1}
    monkeypatch.setattr(transcribe, 'transcribe_meeting', fake_meeting)

    async def no_ai(prompt, **kw):
        return None
    monkeypatch.setattr(meetings.ai, 'ask', no_ai)

    e = event()
    mid = db.add_meeting(100, 'Андрей')
    await H.process_meeting(e.message, mid, 'http://x/meet.mp4')

    m = db.get_meeting(mid)
    assert m['status'] == db.MEETING_READY
    assert m['transcript'] == 'живая речь с планёрки'


async def test_photo_in_private_is_not_a_meeting(monkeypatch):
    """Фото — это документ, а не планёрка: расшифровку не запускаем."""
    called = {}

    async def fake_meeting(url, progress=None):
        called['yes'] = True
        return None
    monkeypatch.setattr(transcribe, 'transcribe_meeting', fake_meeting)

    await H.on_text(event('', [Att('image', 'http://x/p.jpg')]))
    assert not called and db.list_meetings() == []


async def test_voice_in_doc_mode_is_saved_as_document(monkeypatch):
    """Режим «Добавить документ» важнее: голосовое туда и уйдёт."""
    async def fake_meeting(url, progress=None):
        raise AssertionError('в режиме документов планёрку не заводим')
    monkeypatch.setattr(transcribe, 'transcribe_meeting', fake_meeting)

    async def fake_download(url):
        return b'data'
    monkeypatch.setattr(H, '_download', fake_download)

    hid = house_id('Байкальская 237')
    H.STATE[100] = {'mode': 'doc_wait', 'house_id': hid}
    await H.on_text(event('', [Att('audio', 'http://x/note.ogg')]))

    assert db.list_meetings() == []
    assert len(db.list_docs(hid)) == 1


# ---------- Задачи планёрки → работы ----------

async def test_tasks_become_works():
    db.upsert_user(200, 'Константин Петров')
    db.set_user_role(200, 'plumber')

    mid = db.add_meeting(100, 'Андрей')
    db.set_meeting_result(mid, title='Планёрка',
                          protocol=json.dumps(PROTOCOL, ensure_ascii=False),
                          status=db.MEETING_READY)
    bot = FakeBot()
    text = await H.works_from_meeting(bot, 100, 'Андрей', db.get_meeting(mid))

    works = db.list_works(house_id('Байкальская 237'))
    assert len(works) == 1
    w = works[0]
    assert w['title'] == 'Заменить манометр на подаче'
    assert w['deadline'] == '2026-09-25'
    assert w['assignee_id'] == 200                  # Константина нашли по имени
    assert bot.sent[0]['user_id'] == 200            # и предупредили его лично

    assert 'Закупить хомуты' in text                # задача без адреса — в остатке
    assert db.get_meeting(mid)['works_created'] == 1


async def test_tasks_without_house_create_nothing():
    mid = db.add_meeting(100, 'Андрей')
    db.set_meeting_result(mid, protocol=json.dumps(
        {'tasks': [{'title': 'Купить трубу', 'assignee': None,
                    'deadline': None, 'address': None}]}, ensure_ascii=False))
    text = await H.works_from_meeting(FakeBot(), 100, 'Андрей', db.get_meeting(mid))
    assert 'некуда' in text
    assert db.list_works() == []


async def test_task_address_taken_from_title():
    mid = db.add_meeting(100, 'Андрей')
    db.set_meeting_result(mid, protocol=json.dumps(
        {'tasks': [{'title': 'Опрессовать Трилиссера 8/4', 'assignee': None,
                    'deadline': None, 'address': None}]}, ensure_ascii=False))
    await H.works_from_meeting(FakeBot(), 100, 'Андрей', db.get_meeting(mid))
    assert len(db.list_works(house_id('Трилиссера 8/4'))) == 1


async def test_bad_deadline_does_not_break_work():
    mid = db.add_meeting(100, 'Андрей')
    db.set_meeting_result(mid, protocol=json.dumps(
        {'tasks': [{'title': 'Проверить подвал', 'assignee': None,
                    'deadline': 'на следующей неделе',
                    'address': 'Байкальская 237'}]}, ensure_ascii=False))
    await H.works_from_meeting(FakeBot(), 100, 'Андрей', db.get_meeting(mid))
    works = db.list_works(house_id('Байкальская 237'))
    assert len(works) == 1 and works[0]['deadline'] is None


def test_match_person_by_first_name():
    db.upsert_user(200, 'Константин Петров')
    db.set_user_role(200, 'plumber')
    assert H.match_person('Константин')['user_id'] == 200
    assert H.match_person('Костя') is None          # прозвищ не выдумываем
    assert H.match_person(None) is None
    assert H.match_person('Ан') is None             # слишком коротко, чтобы гадать


def test_meeting_card_shows_progress_and_result():
    mid = db.add_meeting(100, 'Андрей')
    assert 'расшифровываю' in H.meeting_card_text(db.get_meeting(mid))

    db.set_meeting_result(mid, title='Опрессовка и аварии',
                          transcript='речь',
                          protocol=json.dumps(PROTOCOL, ensure_ascii=False),
                          status=db.MEETING_READY)
    m = db.get_meeting(mid)
    assert 'Опрессовка и аварии' in H.meeting_card_text(m)
    assert 'расшифровываю' not in H.meeting_line(m)
