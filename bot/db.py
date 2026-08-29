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
    # LIKE в SQLite не различает регистр только у латиницы, а у нас всё
    # по-русски: без своей функции «розлив» не найдёт «Розлив».
    conn.create_function('lower_ru', 1,
                         lambda v: v.lower() if isinstance(v, str) else v)
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
            assignee_id INTEGER,
            campaign_id INTEGER,
            last_reminded TEXT,
            report TEXT,
            done_at TEXT,
            status TEXT NOT NULL DEFAULT 'plan',
            created_by INTEGER,
            created_by_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            role TEXT NOT NULL DEFAULT 'none',
            registered_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            complex_id TEXT NOT NULL,
            deadline TEXT,
            created_by INTEGER,
            created_by_name TEXT,
            created_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS meters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            house_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            created_by_name TEXT,
            created_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meter_id INTEGER NOT NULL,
            value REAL NOT NULL,
            period TEXT NOT NULL,
            submitted_by INTEGER,
            submitted_by_name TEXT,
            submitted_at TEXT NOT NULL)''')
        # Точки установки приборов на тепловом пункте (место живёт годами)
        c.execute('''CREATE TABLE IF NOT EXISTS eq_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            house_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'manometer',
            tp TEXT,
            place TEXT NOT NULL,
            created_by_name TEXT,
            created_at TEXT NOT NULL)''')
        # Приборы в точках (сменяют друг друга)
        c.execute('''CREATE TABLE IF NOT EXISTS eq_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            point_id INTEGER NOT NULL,
            serial TEXT,
            verified_until TEXT,
            installed_at TEXT,
            installed_by_id INTEGER,
            installed_by TEXT,
            photo_device TEXT,
            photo_passport TEXT,
            passport_info TEXT,
            note TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            removed_at TEXT,
            last_reminded TEXT,
            created_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            house_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            path TEXT NOT NULL,
            note TEXT,
            uploaded_by TEXT,
            uploaded_at TEXT NOT NULL)''')
        # Лента рабочего чата: что писали, к какому дому относится
        c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            mid TEXT,
            user_id INTEGER,
            user_name TEXT,
            text TEXT,
            house_id INTEGER,
            has_files INTEGER NOT NULL DEFAULT 0,
            is_issue INTEGER NOT NULL DEFAULT 0,
            transcript TEXT,
            created_at TEXT NOT NULL)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_chat_house ON chat_messages(house_id)')
        # Память агента: что Люся знает о человеке и о чём с ним говорила
        c.execute('''CREATE TABLE IF NOT EXISTS user_notes (
            user_id INTEGER PRIMARY KEY,
            profile TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL)''')
        # Тексты документов: PDF/Word/Excel, разобранные в читаемый вид
        c.execute('''CREATE TABLE IF NOT EXISTS doc_texts (
            source TEXT NOT NULL,
            key TEXT NOT NULL,
            title TEXT,
            addresses TEXT,
            house_id INTEGER,
            text TEXT,
            chars INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'ok',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (source, key))''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_doc_texts_house ON doc_texts(house_id)')
        # Планёрки: расшифровка записи и разобранный из неё протокол (JSON)
        c.execute('''CREATE TABLE IF NOT EXISTS meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            transcript TEXT,
            protocol TEXT,
            duration_sec INTEGER,
            works_created INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'new',
            created_by INTEGER,
            created_by_name TEXT,
            created_at TEXT NOT NULL)''')


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


# --- Пользователи и роли ---

def upsert_user(user_id, name):
    """Регистрирует пользователя при первом обращении. Первый зарегистрированный — админ."""
    with _conn() as c:
        row = c.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,)).fetchone()
        if row:
            if name:
                c.execute('UPDATE users SET name = ? WHERE user_id = ?', (name, user_id))
            return
        n_users = c.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n']
        role = 'admin' if n_users == 0 else 'none'
        c.execute('INSERT INTO users (user_id, name, role, registered_at) VALUES (?, ?, ?, ?)',
                  (user_id, name or '', role, now()))


def get_user(user_id):
    with _conn() as c:
        return c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)).fetchone()


def list_users():
    with _conn() as c:
        return c.execute('SELECT * FROM users ORDER BY registered_at').fetchall()


def set_user_role(user_id, role):
    with _conn() as c:
        c.execute('UPDATE users SET role = ? WHERE user_id = ?', (role, user_id))


# --- Кампании (задания по ЖК: опрессовка, сдача ТУ и т.п.) ---

def add_campaign(title, complex_id, deadline, user_id, user_name) -> int:
    with _conn() as c:
        cur = c.execute(
            'INSERT INTO campaigns (title, complex_id, deadline, created_by, created_by_name, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (title, complex_id, deadline, user_id, user_name, now()))
        return cur.lastrowid


def get_campaign(campaign_id):
    with _conn() as c:
        return c.execute('SELECT * FROM campaigns WHERE id = ?', (campaign_id,)).fetchone()


def list_campaigns(limit=20):
    with _conn() as c:
        return c.execute('SELECT * FROM campaigns ORDER BY id DESC LIMIT ?', (limit,)).fetchall()


def campaign_progress(campaign_id):
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS total, SUM(status = 'done') AS done "
            'FROM works WHERE campaign_id = ?', (campaign_id,)).fetchone()
    return (row['done'] or 0), (row['total'] or 0)


# --- Работы по домам (график, дедлайны) ---

WORK_PLAN = 'plan'
WORK_IN_PROGRESS = 'work'
WORK_DONE = 'done'
WORK_LABELS = {WORK_PLAN: '📌 План', WORK_IN_PROGRESS: '🔧 В работе', WORK_DONE: '✅ Сдано'}


def add_work(house_id, title, deadline, user_name, user_id=None, campaign_id=None) -> int:
    ts = now()
    with _conn() as c:
        cur = c.execute(
            'INSERT INTO works (house_id, title, deadline, status, created_by, created_by_name, '
            'campaign_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (house_id, title, deadline, WORK_PLAN, user_id, user_name, campaign_id, ts, ts))
        return cur.lastrowid


def list_my_works(user_id, limit=30):
    with _conn() as c:
        return c.execute(
            "SELECT * FROM works WHERE assignee_id = ? AND status != 'done' "
            'ORDER BY deadline IS NULL, deadline, id LIMIT ?', (user_id, limit)).fetchall()


def list_done_works(house_id=None, limit=30):
    q = "SELECT * FROM works WHERE status = 'done'"
    args = []
    if house_id is not None:
        q += ' AND house_id = ?'
        args.append(house_id)
    q += ' ORDER BY done_at DESC, id DESC LIMIT ?'
    args.append(limit)
    with _conn() as c:
        return c.execute(q, args).fetchall()


def list_due_works(until_iso, today_iso):
    """Открытые работы с назначенным исполнителем и сроком не позже until_iso,
    по которым сегодня ещё не напоминали."""
    with _conn() as c:
        return c.execute(
            "SELECT * FROM works WHERE status != 'done' AND assignee_id IS NOT NULL "
            'AND deadline IS NOT NULL AND deadline <= ? '
            'AND (last_reminded IS NULL OR last_reminded != ?)',
            (until_iso, today_iso)).fetchall()


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


# --- Оборудование ТП: точки установки и приборы ---

def add_point(house_id, place, tp, user_name, kind='manometer') -> int:
    with _conn() as c:
        cur = c.execute('INSERT INTO eq_points (house_id, kind, tp, place, created_by_name, created_at) '
                        'VALUES (?, ?, ?, ?, ?, ?)', (house_id, kind, tp, place, user_name, now()))
        return cur.lastrowid


def get_point(point_id):
    with _conn() as c:
        return c.execute('SELECT * FROM eq_points WHERE id = ?', (point_id,)).fetchone()


def list_points(house_id, kind='manometer'):
    with _conn() as c:
        return c.execute('SELECT * FROM eq_points WHERE house_id = ? AND kind = ? '
                         'ORDER BY tp, id', (house_id, kind)).fetchall()


def points_count(house_id, kind='manometer') -> int:
    with _conn() as c:
        return c.execute('SELECT COUNT(*) AS n FROM eq_points WHERE house_id = ? AND kind = ?',
                         (house_id, kind)).fetchone()['n']


def add_device(point_id, serial, verified_until, user_id, user_name,
               installed_at=None) -> int:
    """Ставит новый прибор в точку, прежний уводит в историю."""
    ts = now()
    with _conn() as c:
        c.execute("UPDATE eq_devices SET status = 'removed', removed_at = ? "
                  "WHERE point_id = ? AND status = 'active'", (ts, point_id))
        cur = c.execute(
            'INSERT INTO eq_devices (point_id, serial, verified_until, installed_at, '
            'installed_by_id, installed_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (point_id, serial, verified_until, installed_at or ts, user_id, user_name, ts))
        return cur.lastrowid


def get_device(device_id):
    with _conn() as c:
        return c.execute('SELECT * FROM eq_devices WHERE id = ?', (device_id,)).fetchone()


def active_device(point_id):
    with _conn() as c:
        return c.execute("SELECT * FROM eq_devices WHERE point_id = ? AND status = 'active' "
                         'ORDER BY id DESC LIMIT 1', (point_id,)).fetchone()


def point_history(point_id):
    with _conn() as c:
        return c.execute('SELECT * FROM eq_devices WHERE point_id = ? ORDER BY id DESC',
                         (point_id,)).fetchall()


def update_device(device_id, **fields):
    cols = ', '.join(f'{k} = ?' for k in fields)
    with _conn() as c:
        c.execute(f'UPDATE eq_devices SET {cols} WHERE id = ?', (*fields.values(), device_id))


def devices_verification_due(until_iso, today_iso):
    """Действующие приборы, у которых поверка истекает не позже until_iso."""
    with _conn() as c:
        return c.execute(
            "SELECT d.*, p.house_id, p.place, p.tp FROM eq_devices d "
            'JOIN eq_points p ON p.id = d.point_id '
            "WHERE d.status = 'active' AND d.verified_until IS NOT NULL "
            'AND d.verified_until <= ? '
            'AND (d.last_reminded IS NULL OR d.last_reminded != ?)',
            (until_iso, today_iso)).fetchall()


def all_active_devices():
    with _conn() as c:
        return c.execute(
            "SELECT d.*, p.house_id, p.place, p.tp FROM eq_points p "
            "LEFT JOIN eq_devices d ON d.point_id = p.id AND d.status = 'active' "
            'ORDER BY p.house_id, p.tp, p.id').fetchall()


# --- Счётчики и показания ---

def add_meter(house_id, kind, label, user_name) -> int:
    with _conn() as c:
        cur = c.execute('INSERT INTO meters (house_id, kind, label, created_by_name, created_at) '
                        'VALUES (?, ?, ?, ?, ?)', (house_id, kind, label, user_name, now()))
        return cur.lastrowid


def get_meter(meter_id):
    with _conn() as c:
        return c.execute('SELECT * FROM meters WHERE id = ?', (meter_id,)).fetchone()


def list_meters(house_id):
    with _conn() as c:
        return c.execute('SELECT * FROM meters WHERE house_id = ? ORDER BY id', (house_id,)).fetchall()


def houses_with_meters() -> dict:
    """house_id -> число счётчиков."""
    with _conn() as c:
        rows = c.execute('SELECT house_id, COUNT(*) AS n FROM meters GROUP BY house_id').fetchall()
    return {r['house_id']: r['n'] for r in rows}


def add_reading(meter_id, value, period, user_id, user_name) -> int:
    with _conn() as c:
        cur = c.execute('INSERT INTO readings (meter_id, value, period, submitted_by, '
                        'submitted_by_name, submitted_at) VALUES (?, ?, ?, ?, ?, ?)',
                        (meter_id, value, period, user_id, user_name, now()))
        return cur.lastrowid


def meter_readings(meter_id, limit=8):
    """История показаний счётчика, свежие первыми."""
    with _conn() as c:
        return c.execute('SELECT * FROM readings WHERE meter_id = ? ORDER BY id DESC LIMIT ?',
                         (meter_id, limit)).fetchall()


def readings_for_period(period):
    """Все показания за период (ГГГГ-ММ), по одному последнему на счётчик."""
    with _conn() as c:
        return c.execute(
            'SELECT r.*, m.house_id, m.kind, m.label FROM readings r '
            'JOIN meters m ON m.id = r.meter_id '
            'WHERE r.period = ? AND r.id = (SELECT MAX(id) FROM readings '
            'WHERE meter_id = r.meter_id AND period = ?) ORDER BY m.house_id, m.id',
            (period, period)).fetchall()


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


# --- Память диалогов (агент) ---

def get_user_notes(user_id) -> str:
    with _conn() as c:
        row = c.execute('SELECT profile FROM user_notes WHERE user_id = ?', (user_id,)).fetchone()
    return row['profile'] if row else ''


def set_user_notes(user_id, profile):
    with _conn() as c:
        c.execute('INSERT INTO user_notes (user_id, profile, updated_at) VALUES (?, ?, ?) '
                  'ON CONFLICT(user_id) DO UPDATE SET profile = excluded.profile, '
                  'updated_at = excluded.updated_at',
                  (user_id, profile, now()))


def add_chat_message(user_id, role, content):
    with _conn() as c:
        c.execute('INSERT INTO chat_history (user_id, role, content, created_at) '
                  'VALUES (?, ?, ?, ?)', (user_id, role, content, now()))


def recent_chat_history(user_id, limit=6) -> list:
    """Последние сообщения пользователя, от старых к новым."""
    with _conn() as c:
        rows = c.execute('SELECT role, content FROM chat_history WHERE user_id = ? '
                         'ORDER BY id DESC LIMIT ?', (user_id, limit)).fetchall()
    return [{'role': r['role'], 'content': r['content']} for r in reversed(rows)]


# --- Лента рабочего чата ---

def add_chat_record(chat_id, mid, user_id, user_name, text,
                    house_id=None, has_files=False, is_issue=False) -> int:
    with _conn() as c:
        cur = c.execute(
            'INSERT INTO chat_messages (chat_id, mid, user_id, user_name, text, '
            'house_id, has_files, is_issue, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (chat_id, mid, user_id, user_name, text, house_id,
             int(has_files), int(is_issue), now()))
        return cur.lastrowid


def house_chat_records(house_id, limit=20):
    """Сообщения чата по конкретному дому, свежие первыми."""
    with _conn() as c:
        return c.execute('SELECT * FROM chat_messages WHERE house_id = ? '
                         'ORDER BY id DESC LIMIT ?', (house_id, limit)).fetchall()


def chat_records_since(since_iso, limit=200):
    """Сообщения за период (по дате создания в формате ДД.ММ.ГГГГ)."""
    with _conn() as c:
        return c.execute('SELECT * FROM chat_messages WHERE created_at >= ? '
                         'ORDER BY id DESC LIMIT ?', (since_iso, limit)).fetchall()


def chat_stats_for_day(day_str):
    """Сводка за день: всего сообщений, по домам, аварийных, с файлами."""
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS total, "
            'SUM(house_id IS NOT NULL) AS with_house, '
            'SUM(is_issue) AS issues, '
            'SUM(has_files) AS with_files '
            'FROM chat_messages WHERE created_at LIKE ?', (day_str + '%',)).fetchone()
    return {'total': row['total'] or 0, 'with_house': row['with_house'] or 0,
            'issues': row['issues'] or 0, 'with_files': row['with_files'] or 0}


def recent_issues(limit=10):
    """Последние сообщения, похожие на заявки."""
    with _conn() as c:
        return c.execute("SELECT * FROM chat_messages WHERE is_issue = 1 "
                         'ORDER BY id DESC LIMIT ?', (limit,)).fetchall()


def set_chat_transcript(record_id, transcript, house_id=None, is_issue=None):
    """Дописывает расшифровку голосового/видео к сообщению чата."""
    fields = {'transcript': transcript}
    if house_id is not None:
        fields['house_id'] = house_id
    if is_issue is not None:
        fields['is_issue'] = int(is_issue)
    cols = ', '.join(f'{k} = ?' for k in fields)
    with _conn() as c:
        c.execute(f'UPDATE chat_messages SET {cols} WHERE id = ?',
                  (*fields.values(), record_id))


def get_chat_record(record_id):
    with _conn() as c:
        return c.execute('SELECT * FROM chat_messages WHERE id = ?', (record_id,)).fetchone()


# --- Планёрки ---

MEETING_NEW = 'new'          # запись принята, идёт расшифровка
MEETING_READY = 'ready'      # протокол готов
MEETING_FAILED = 'failed'    # распознать не вышло


def add_meeting(user_id, user_name) -> int:
    with _conn() as c:
        cur = c.execute(
            'INSERT INTO meetings (status, created_by, created_by_name, created_at) '
            'VALUES (?, ?, ?, ?)', (MEETING_NEW, user_id, user_name, now()))
        return cur.lastrowid


def set_meeting_result(meeting_id, title=None, transcript=None, protocol=None,
                       duration_sec=None, status=None):
    """Дописывает к планёрке расшифровку, протокол (JSON-строкой) и статус."""
    fields = {}
    if title is not None:
        fields['title'] = title
    if transcript is not None:
        fields['transcript'] = transcript
    if protocol is not None:
        fields['protocol'] = protocol
    if duration_sec is not None:
        fields['duration_sec'] = int(duration_sec)
    if status is not None:
        fields['status'] = status
    if not fields:
        return
    cols = ', '.join(f'{k} = ?' for k in fields)
    with _conn() as c:
        c.execute(f'UPDATE meetings SET {cols} WHERE id = ?',
                  (*fields.values(), meeting_id))


def set_meeting_works(meeting_id, count):
    with _conn() as c:
        c.execute('UPDATE meetings SET works_created = ? WHERE id = ?',
                  (int(count), meeting_id))


def get_meeting(meeting_id):
    with _conn() as c:
        return c.execute('SELECT * FROM meetings WHERE id = ?', (meeting_id,)).fetchone()


def list_meetings(limit=15):
    with _conn() as c:
        return c.execute('SELECT * FROM meetings ORDER BY id DESC LIMIT ?',
                         (limit,)).fetchall()


def delete_meeting(meeting_id):
    with _conn() as c:
        c.execute('DELETE FROM meetings WHERE id = ?', (meeting_id,))


# --- Тексты документов ---

DOC_OK = 'ok'            # текст извлечён
DOC_EMPTY = 'empty'      # файл прочитан, но букв нет (скан без текстового слоя)
DOC_SKIPPED = 'skipped'  # формат не читается (чертёж) или нет markitdown
DOC_FAILED = 'failed'    # файл битый или разбор упал


def save_doc_text(source, key, text, status, title=None, addresses=None, house_id=None):
    """Кладёт разобранный документ. Повторный разбор перезаписывает прежний."""
    with _conn() as c:
        c.execute(
            'INSERT INTO doc_texts (source, key, title, addresses, house_id, text, chars, '
            'status, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) '
            'ON CONFLICT(source, key) DO UPDATE SET title = excluded.title, '
            'addresses = excluded.addresses, house_id = excluded.house_id, '
            'text = excluded.text, chars = excluded.chars, status = excluded.status, '
            'updated_at = excluded.updated_at',
            (source, str(key), title, addresses, house_id, text, len(text or ''),
             status, now()))


def get_doc_text(source, key):
    with _conn() as c:
        return c.execute('SELECT * FROM doc_texts WHERE source = ? AND key = ?',
                         (source, str(key))).fetchone()


def list_doc_texts(source=None, status=DOC_OK, limit=100):
    q, args = 'SELECT * FROM doc_texts WHERE 1 = 1', []
    if source:
        q += ' AND source = ?'
        args.append(source)
    if status:
        q += ' AND status = ?'
        args.append(status)
    q += ' ORDER BY title LIMIT ?'
    args.append(limit)
    with _conn() as c:
        return c.execute(q, args).fetchall()


def doc_texts_stats() -> dict:
    """Сколько документов разобрано и сколько не поддалось — по статусам."""
    with _conn() as c:
        rows = c.execute('SELECT status, COUNT(*) n FROM doc_texts GROUP BY status').fetchall()
    return {r['status']: r['n'] for r in rows}


def search_doc_texts(query, house_id=None, address=None, limit=5):
    """Документы, где встречается запрос. Совпадение в названии — важнее."""
    if not query or not query.strip():
        return []
    like = f'%{query.strip().lower()}%'
    q = ("SELECT * FROM doc_texts WHERE status = 'ok' "
         'AND (lower_ru(text) LIKE ? OR lower_ru(title) LIKE ?)')
    args = [like, like]
    # Документ относится к дому двумя способами: файл дома привязан по id,
    # а проектный — перечнем адресов в каталоге. Годится любой из них.
    if house_id is not None and address:
        q += ' AND (house_id = ? OR lower_ru(addresses) LIKE ?)'
        args += [house_id, f'%{address.lower()}%']
    elif house_id is not None:
        q += ' AND house_id = ?'
        args.append(house_id)
    elif address:
        q += ' AND lower_ru(addresses) LIKE ?'
        args.append(f'%{address.lower()}%')
    q += ' ORDER BY (lower_ru(title) LIKE ?) DESC, chars DESC LIMIT ?'
    args += [like, limit]
    with _conn() as c:
        return c.execute(q, args).fetchall()
