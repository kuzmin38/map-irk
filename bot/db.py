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
        c.execute('''CREATE TABLE IF NOT EXISTS house_complex (
            house_id INTEGER PRIMARY KEY,
            complex_id TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            house_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            details TEXT,
            deadline TEXT,
            assignee TEXT,
            status TEXT NOT NULL DEFAULT 'plan',
            created_by_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            house_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            path TEXT NOT NULL,
            note TEXT,
            uploaded_by TEXT,
            uploaded_at TEXT NOT NULL)''')


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


# --- Работы по домам (график, дедлайны) ---

WORK_PLAN = 'plan'
WORK_IN_PROGRESS = 'work'
WORK_DONE = 'done'
WORK_LABELS = {WORK_PLAN: '📌 План', WORK_IN_PROGRESS: '🔧 В работе', WORK_DONE: '✅ Сдано'}


def add_work(house_id, title, deadline, user_name) -> int:
    ts = now()
    with _conn() as c:
        cur = c.execute(
            'INSERT INTO works (house_id, title, deadline, status, created_by_name, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (house_id, title, deadline, WORK_PLAN, user_name, ts, ts))
        return cur.lastrowid


def get_work(work_id):
    with _conn() as c:
        return c.execute('SELECT * FROM works WHERE id = ?', (work_id,)).fetchone()


def list_works(house_id=None, open_only=True, limit=40):
    q = 'SELECT * FROM works WHERE 1=1'
    args = []
    if house_id is not None:
        q += ' AND house_id = ?'
        args.append(house_id)
    if open_only:
        q += " AND status != 'done'"
    # deadline хранится как ГГГГ-ММ-ДД, пустые сроки в конце
    q += " ORDER BY status = 'done', deadline IS NULL, deadline, id LIMIT ?"
    args.append(limit)
    with _conn() as c:
        return c.execute(q, args).fetchall()


def update_work(work_id, **fields):
    cols = ', '.join(f'{k} = ?' for k in fields)
    with _conn() as c:
        c.execute(f'UPDATE works SET {cols}, updated_at = ? WHERE id = ?',
                  (*fields.values(), now(), work_id))


# --- Привязка домов к ЖК ---

def get_house_complex(house_id):
    with _conn() as c:
        row = c.execute('SELECT complex_id FROM house_complex WHERE house_id = ?', (house_id,)).fetchone()
    return row['complex_id'] if row else None


def set_house_complex(house_id, complex_id):
    with _conn() as c:
        c.execute('INSERT INTO house_complex (house_id, complex_id) VALUES (?, ?) '
                  'ON CONFLICT(house_id) DO UPDATE SET complex_id = excluded.complex_id',
                  (house_id, complex_id))


def all_house_complexes() -> dict:
    with _conn() as c:
        rows = c.execute('SELECT house_id, complex_id FROM house_complex').fetchall()
    return {r['house_id']: r['complex_id'] for r in rows}


# --- Документы домов ---

def add_doc(house_id, filename, path, note, user_name) -> int:
    with _conn() as c:
        cur = c.execute('INSERT INTO docs (house_id, filename, path, note, uploaded_by, uploaded_at) '
                        'VALUES (?, ?, ?, ?, ?, ?)',
                        (house_id, filename, path, note, user_name, now()))
        return cur.lastrowid


def set_doc_file(doc_id, filename, path):
    with _conn() as c:
        c.execute('UPDATE docs SET filename = ?, path = ? WHERE id = ?', (filename, path, doc_id))


def list_docs(house_id):
    with _conn() as c:
        return c.execute('SELECT * FROM docs WHERE house_id = ? ORDER BY id', (house_id,)).fetchall()


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
