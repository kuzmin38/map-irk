"""Колонки, добавленные в схему позже, доезжают до рабочей базы.

CREATE TABLE IF NOT EXISTS не трогает уже существующую таблицу. Из-за этого
в бою молча отвалилась расшифровка видеоотчётов: колонку transcript добавили
в код, а в базе на томе Railway её не было — запрос падал на ходу.
"""
import sqlite3

import pytest

from bot import db


@pytest.fixture(autouse=True)
def baza(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    return str(tmp_path / 'test.db')


def staraya_baza(path):
    """База, созданная до появления колонки transcript."""
    c = sqlite3.connect(path)
    c.execute('''CREATE TABLE chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        mid TEXT,
        user_id INTEGER,
        user_name TEXT,
        text TEXT,
        house_id INTEGER,
        has_files INTEGER NOT NULL DEFAULT 0,
        is_issue INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL)''')
    c.execute("INSERT INTO chat_messages (chat_id, text, created_at) "
              "VALUES (1, 'старое сообщение', '01.01.2026 10:00')")
    c.commit()
    c.close()


def kolonki(path, table):
    c = sqlite3.connect(path)
    try:
        return {r[1] for r in c.execute(f'PRAGMA table_info({table})')}
    finally:
        c.close()


def test_nedostayuschaya_kolonka_dobavlyaetsya(baza):
    staraya_baza(baza)
    assert 'transcript' not in kolonki(baza, 'chat_messages')

    db.init()

    assert 'transcript' in kolonki(baza, 'chat_messages')


def test_dannye_pri_etom_ne_teryayutsya(baza):
    staraya_baza(baza)

    db.init()

    c = sqlite3.connect(baza)
    try:
        rows = c.execute('SELECT text, transcript FROM chat_messages').fetchall()
    finally:
        c.close()
    assert rows == [('старое сообщение', None)]


def test_rasshifrovka_zapisyvaetsya_posle_migracii(baza):
    """Ровно тот запрос, который падал в бою."""
    staraya_baza(baza)
    db.init()

    db.set_chat_transcript(1, 'нет горячей воды в 47-й', is_issue=True)

    c = sqlite3.connect(baza)
    try:
        row = c.execute('SELECT transcript, is_issue FROM chat_messages').fetchone()
    finally:
        c.close()
    assert row == ('нет горячей воды в 47-й', 1)


def test_povtornyy_zapusk_nichego_ne_lomaet(baza):
    db.init()
    before = kolonki(baza, 'chat_messages')

    db.init()

    assert kolonki(baza, 'chat_messages') == before


def test_svezhaya_baza_srazu_polnaya(baza):
    db.init()
    assert 'transcript' in kolonki(baza, 'chat_messages')
    assert 'campaign_id' in kolonki(baza, 'works')
