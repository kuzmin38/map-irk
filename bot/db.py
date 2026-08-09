"""SQLite-хранилище: заявки и паспорта домов."""
import os
import sqlite3
from datetime import datetime, timezone, timedelta

DB_PATH = os.environ.get('BOT_DB', os.path.join(os.path.dirname(__file__), 'data', 'bot.db'))

IRKUTSK_TZ = timezone(timedelta(hours=8))

STATUS_NEW = 'new'
STATUS_WORK = 'work'
STATUS_DONE = 'done'
STATUS_LABELS = {STATUS_NEW: '🆕 Новая', STATUS_WORK: '🔧 В работе', STATUS_DONE: '✅ Выполнена'}


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with _conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            house_id INTEGER,
            address TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            created_by INTEGER,
            created_by_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS passports (
            house_id INTEGER NOT NULL,
            field TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_by TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (house_id, field))''')


def now() -> str:
    return datetime.now(IRKUTSK_TZ).strftime('%d.%m.%Y %H:%M')


# --- Заявки ---

def add_request(house_id, address, description, user_id, user_name) -> int:
    ts = now()
    with _conn() as c:
        cur = c.execute(
            'INSERT INTO requests (house_id, address, description, status, created_by, created_by_name, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (house_id, address, description, STATUS_NEW, user_id, user_name, ts, ts))
        return cur.lastrowid


def get_request(req_id):
    with _conn() as c:
        return c.execute('SELECT * FROM requests WHERE id = ?', (req_id,)).fetchone()


def list_requests(statuses=(STATUS_NEW, STATUS_WORK), limit=30):
    ph = ','.join('?' * len(statuses))
    with _conn() as c:
        return c.execute(
            f'SELECT * FROM requests WHERE status IN ({ph}) ORDER BY id DESC LIMIT ?',
            (*statuses, limit)).fetchall()


def set_request_status(req_id, status):
    with _conn() as c:
        c.execute('UPDATE requests SET status = ?, updated_at = ? WHERE id = ?',
                  (status, now(), req_id))


# --- Паспорта домов ---

def get_passport(house_id) -> dict:
    with _conn() as c:
        rows = c.execute('SELECT field, value FROM passports WHERE house_id = ?', (house_id,)).fetchall()
    return {r['field']: r['value'] for r in rows}


def set_passport_field(house_id, field, value, user_name):
    with _conn() as c:
        c.execute('INSERT INTO passports (house_id, field, value, updated_by, updated_at) '
                  'VALUES (?, ?, ?, ?, ?) '
                  'ON CONFLICT(house_id, field) DO UPDATE SET value = excluded.value, '
                  'updated_by = excluded.updated_by, updated_at = excluded.updated_at',
                  (house_id, field, value, user_name, now()))
