import pytest

from bot import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


def test_user_notes_empty_by_default():
    assert db.get_user_notes(42) == ''


def test_user_notes_set_and_get():
    db.set_user_notes(42, 'Любит точные ответы, часто спрашивает про Байкальскую.')
    assert db.get_user_notes(42) == 'Любит точные ответы, часто спрашивает про Байкальскую.'


def test_user_notes_update_overwrites():
    db.set_user_notes(42, 'Первая заметка')
    db.set_user_notes(42, 'Вторая заметка')
    assert db.get_user_notes(42) == 'Вторая заметка'


def test_chat_history_empty_by_default():
    assert db.recent_chat_history(42) == []


def test_chat_history_roundtrip_order():
    db.add_chat_message(42, 'user', 'Привет')
    db.add_chat_message(42, 'assistant', 'Привет!')
    db.add_chat_message(42, 'user', 'Как дела?')
    history = db.recent_chat_history(42, limit=6)
    assert [m['content'] for m in history] == ['Привет', 'Привет!', 'Как дела?']
    assert [m['role'] for m in history] == ['user', 'assistant', 'user']


def test_chat_history_limit_keeps_most_recent():
    for i in range(10):
        db.add_chat_message(42, 'user', f'сообщение {i}')
    history = db.recent_chat_history(42, limit=4)
    assert [m['content'] for m in history] == [f'сообщение {i}' for i in range(6, 10)]


def test_chat_history_scoped_per_user():
    db.add_chat_message(1, 'user', 'от первого')
    db.add_chat_message(2, 'user', 'от второго')
    assert [m['content'] for m in db.recent_chat_history(1)] == ['от первого']
