"""SQLite-хранилище: заявки и паспорта домов."""
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta

DB_PATH = os.environ.get('BOT_DB', os.path.join(os.path.dirname(__file__), 'data', 'bot.db'))

IRKUTSK_TZ = timezone(timedelta(hours=8))

log = logging.getLogger('db')

STATUS_NEW = 'new'
STATUS_WORK = 'work'
STATUS_DONE = 'done'
STATUS_LABELS = {STATUS_NEW: '🆕 Новая', STATUS_WORK: '🔧 В работе', STATUS_DONE: '✅ Выполнена'}


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _create_all(c):
    """Создаёт таблицы, которых ещё нет."""
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
        last_meter_id INTEGER,
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
        serial TEXT,
        photo TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        status_at TEXT,
        status_by TEXT,
        created_by_name TEXT,
        created_at TEXT NOT NULL)''')
    # Журнал: счётчик снимают на поверку и ставят обратно, и каждый шаг
    # должен быть подписан. Иначе выходит как с инспектором: сказали
    # «поставлен», а прибор лежит в столярке
    c.execute('''CREATE TABLE IF NOT EXISTS meter_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meter_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        note TEXT,
        by_id INTEGER,
        by_name TEXT,
        at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS readings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meter_id INTEGER NOT NULL,
        value REAL NOT NULL,
        period TEXT NOT NULL,
        photo TEXT,
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
    # Напоминания по просьбе: «напомни завтра в 9 про опрессовку»
    c.execute('''CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        user_id INTEGER NOT NULL,
        user_name TEXT,
        text TEXT NOT NULL,
        due_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        sent_at TEXT,
        cancelled INTEGER NOT NULL DEFAULT 0)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_rem_due ON reminders(due_at)')
    # Настройки конкретного чата: пока одна — болтает Люся там или молчит
    c.execute('''CREATE TABLE IF NOT EXISTS chat_settings (
        chat_id INTEGER PRIMARY KEY,
        banter INTEGER NOT NULL DEFAULT 1)''')
    # Память агента: что Люся знает о человеке и о чём с ним говорила
    c.execute('''CREATE TABLE IF NOT EXISTS user_notes (
        user_id INTEGER PRIMARY KEY,
        profile TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL)''')
    # Личные ссылки на страницу записи: адрес и есть пропуск
    c.execute('''CREATE TABLE IF NOT EXISTS web_tokens (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL)''')
    # Личные диалоги: их chat_id нужен, чтобы забрать сообщение, которого
    # MAX не отдал в уведомлении. В списке чатов бота диалогов нет
    c.execute('''CREATE TABLE IF NOT EXISTS dialogs (
        chat_id INTEGER PRIMARY KEY,
        user_id INTEGER,
        seen_at TEXT NOT NULL)''')
    # Какой чат относится к какому дому. MAX имени чата не присылает,
    # поэтому привязку делает человек командой изнутри этого чата
    c.execute('''CREATE TABLE IF NOT EXISTS house_chats (
        chat_id INTEGER PRIMARY KEY,
        house_id INTEGER NOT NULL,
        title TEXT,
        bound_by TEXT,
        bound_at TEXT NOT NULL)''')
    # Перекрытые стояки: кто, когда, по какой квартире и кого это оставило
    # без воды. Открыть забывают чаще, чем перекрыть
    c.execute('''CREATE TABLE IF NOT EXISTS riser_shutoffs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        house_id INTEGER NOT NULL,
        flat INTEGER NOT NULL,
        riser INTEGER,
        floor INTEGER,
        flats TEXT,
        by_id INTEGER,
        by_name TEXT,
        closed_at TEXT NOT NULL,
        opened_at TEXT,
        res TEXT,
        original TEXT,
        announced INTEGER NOT NULL DEFAULT 0,
        reminded INTEGER NOT NULL DEFAULT 0)''')
    # Хроника дома: что за день произошло, разложенное по домам ночным
    # разбором ленты. Не сообщения, а факты
    c.execute('''CREATE TABLE IF NOT EXISTS house_facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        house_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        text TEXT NOT NULL,
        kind TEXT,
        created_at TEXT NOT NULL)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_facts ON house_facts(house_id, day)')
    # Замечания по квартирам: что уже находили за этой дверью. Подмес,
    # найденный однажды, находят там же снова — и об этом надо помнить
    c.execute('''CREATE TABLE IF NOT EXISTS flat_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        house_id INTEGER NOT NULL,
        flat INTEGER NOT NULL,
        kind TEXT,
        text TEXT NOT NULL,
        record_id INTEGER,
        author_id INTEGER,
        author TEXT,
        created_at TEXT NOT NULL)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_flatnotes ON flat_notes(house_id, flat)')
    # Опись имущества: что где лежит. Мотопомпа в компании была, а на
    # затопленной парковке о ней не вспомнили — искать больше негде
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        qty INTEGER NOT NULL DEFAULT 1,
        house_id INTEGER,
        place TEXT,
        note TEXT,
        status TEXT NOT NULL DEFAULT 'here',
        added_by INTEGER,
        added_by_name TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_inv_house ON inventory(house_id)')
    c.execute('''CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        chat_id INTEGER,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL)''')



def _sync_columns(c):
    """Досоздаёт колонки, появившиеся в схеме уже после создания базы.

    CREATE TABLE IF NOT EXISTS не трогает таблицу, если она уже есть: колонка,
    добавленная в код позже, в рабочей базе так и не появляется, и запрос
    падает на ходу. Так молча отвалилась расшифровка видеоотчётов — колонки
    transcript в бою просто не было.

    Эталон берём из этого же кода: поднимаем схему в памяти и сверяем состав.
    """
    ref = sqlite3.connect(':memory:')
    try:
        _create_all(ref)
        tables = [r[0] for r in ref.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")]
        for table in tables:
            want = {r[1]: r for r in ref.execute(f'PRAGMA table_info({table})')}
            have = {r[1] for r in c.execute(f'PRAGMA table_info({table})')}
            for name, row in want.items():
                if name in have:
                    continue
                # ALTER TABLE умеет добавлять только необязательные колонки
                decl = f'{name} {row[2]}'
                if row[4] is not None:
                    decl += f' DEFAULT {row[4]}'
                c.execute(f'ALTER TABLE {table} ADD COLUMN {decl}')
                log.warning('В таблицу %s добавлена колонка %s', table, name)
    finally:
        ref.close()


def seed_house_complexes() -> int:
    """Приводит привязку домов к ЖК в соответствие с `house_complex.txt`.

    Файл — источник истины: он лежит в репозитории и правится осознанно.
    Сначала заполнялись только пустые записи, чтобы не затирать правку,
    сделанную руками в боте. На деле вышло хуже: исправление в файле до базы
    не доезжало, и дома молча оставались в неверном комплексе.

    Возвращает число изменённых домов.
    """
    from . import houses

    mapping = houses.load_complex_map()
    if not mapping:
        return 0
    known = {houses._norm_addr(h['address']): h['id'] for h in houses.ALL_HOUSES}
    assigned = all_house_complexes()
    changed = 0
    for address, complex_id in mapping.items():
        house_id = known.get(address)
        if house_id is None:
            log.warning('В привязке к ЖК неизвестный адрес: %s', address)
            continue
        было = assigned.get(house_id)
        if было == complex_id:
            continue
        set_house_complex(house_id, complex_id)
        changed += 1
        if было:
            log.info('Дом %s переведён из %s в %s', address, было, complex_id)
    if changed:
        log.info('Привязка к ЖК обновлена, домов: %s', changed)

    # Итог в лог: по нему видно фактическое состояние базы, а не файла
    itog = {}
    for cid in all_house_complexes().values():
        itog[cid] = itog.get(cid, 0) + 1
    log.info('Дома по комплексам: %s', itog or 'привязок нет')
    return changed


def init():
    with _conn() as c:
        _create_all(c)
        _sync_columns(c)
    seed_house_complexes()

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

def upsert_user(user_id, name) -> bool:
    """Регистрирует пользователя при первом обращении. Первый зарегистрированный — админ.

    Возвращает True, если человек пришёл впервые: тогда руководству стоит
    показать новичка и назначить ему роль, иначе он так и останется «без роли».
    """
    with _conn() as c:
        row = c.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,)).fetchone()
        if row:
            if name:
                c.execute('UPDATE users SET name = ? WHERE user_id = ?', (name, user_id))
            return False
        n_users = c.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n']
        role = 'admin' if n_users == 0 else 'none'
        c.execute('INSERT INTO users (user_id, name, role, registered_at) VALUES (?, ?, ?, ?)',
                  (user_id, name or '', role, now()))
    return True


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


def devices_with_verification():
    """Действующие приборы, у которых указан срок поверки, с адресом и местом."""
    with _conn() as c:
        return c.execute(
            "SELECT d.*, p.house_id, p.place, p.tp FROM eq_devices d "
            'JOIN eq_points p ON p.id = d.point_id '
            "WHERE d.status = 'active' AND d.verified_until IS NOT NULL "
            'ORDER BY d.verified_until').fetchall()


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


def remember_meter(user_id, meter_id):
    """Счётчик, с которым человек работал последним.

    Карточка счётчика уезжает вверх по ленте, и чтобы вернуться к ней,
    приходится заново идти меню → дом → прибор. С этой памятью возврат
    в одно нажатие.
    """
    with _conn() as c:
        c.execute('UPDATE users SET last_meter_id = ? WHERE user_id = ?',
                  (meter_id, user_id))


def last_meter(user_id):
    """Последний счётчик человека — или None, если прибор успели удалить."""
    with _conn() as c:
        row = c.execute(
            'SELECT m.* FROM users u JOIN meters m ON m.id = u.last_meter_id '
            'WHERE u.user_id = ?', (user_id,)).fetchone()
    return row


def list_meters(house_id):
    with _conn() as c:
        return c.execute('SELECT * FROM meters WHERE house_id = ? ORDER BY id', (house_id,)).fetchall()


def update_meter(meter_id, **fields):
    """Правка счётчика: название, заводской номер, фото."""
    cols = ', '.join(f'{k} = ?' for k in fields)
    with _conn() as c:
        c.execute(f'UPDATE meters SET {cols} WHERE id = ?', (*fields.values(), meter_id))


METER_ACTIVE = 'active'    # на месте
METER_REMOVED = 'verify'   # снят на поверку


def _meter_event(meter_id, action, user_id, user_name, note=None):
    with _conn() as c:
        c.execute('INSERT INTO meter_events (meter_id, action, note, by_id, by_name, at) '
                  'VALUES (?, ?, ?, ?, ?, ?)',
                  (meter_id, action, note, user_id, user_name, now()))


def meter_remove(meter_id, user_id, user_name, note=None):
    """Счётчик снят на поверку: с этого момента показаний по нему быть не должно."""
    update_meter(meter_id, status=METER_REMOVED, status_at=now(), status_by=user_name)
    _meter_event(meter_id, METER_REMOVED, user_id, user_name, note)


def meter_install(meter_id, user_id, user_name, note=None):
    """Счётчик поставлен на место — подтверждает тот, кто ставил."""
    update_meter(meter_id, status=METER_ACTIVE, status_at=now(), status_by=user_name)
    _meter_event(meter_id, METER_ACTIVE, user_id, user_name, note)


def meter_events(meter_id, limit=20):
    """Журнал снятий и установок, свежие первыми."""
    with _conn() as c:
        return c.execute('SELECT * FROM meter_events WHERE meter_id = ? '
                         'ORDER BY id DESC LIMIT ?', (meter_id, limit)).fetchall()


def removed_meters():
    """Счётчики, снятые на поверку и пока не возвращённые."""
    with _conn() as c:
        return c.execute("SELECT * FROM meters WHERE status = ? ORDER BY house_id",
                         (METER_REMOVED,)).fetchall()


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


def set_reading_photo(reading_id, path):
    with _conn() as c:
        c.execute('UPDATE readings SET photo = ? WHERE id = ?', (path, reading_id))


def delete_reading(reading_id):
    """Убирает ошибочное показание.

    Одна неверная цифра портит и расход, и выгрузку для сбытовой, а спорить
    с ней потом некому: в таблице она выглядит как настоящая.
    """
    with _conn() as c:
        c.execute('DELETE FROM readings WHERE id = ?', (reading_id,))


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


def add_chat_message(user_id, role, content, chat_id=None):
    with _conn() as c:
        c.execute('INSERT INTO chat_history (user_id, chat_id, role, content, created_at) '
                  'VALUES (?, ?, ?, ?, ?)', (user_id, chat_id, role, content, now()))


def forget_user(user_id) -> int:
    """Стирает память разговора: историю и профиль. Возвращает, сколько сообщений забыто.

    Люся подмешивает в запрос свои прошлые ответы. Если один из них был
    ошибочным, она читает его как факт и повторяет ошибку снова и снова —
    даже после того, как причину в коде уже устранили.
    """
    with _conn() as c:
        n = c.execute('SELECT COUNT(*) AS n FROM chat_history WHERE user_id = ?',
                      (user_id,)).fetchone()['n']
        c.execute('DELETE FROM chat_history WHERE user_id = ?', (user_id,))
        c.execute('DELETE FROM user_notes WHERE user_id = ?', (user_id,))
    return n


def recent_chat_history(user_id, limit=6, chat_id=None) -> list:
    """Последние сообщения пользователя в этом же месте, от старых к новым.

    Память разделена по чатам не для порядка, а по делу: в личке человек
    заводил счётчик, а в рабочем чате спросил про адрес из видеоотчёта — и
    Люся выдала ему счётчик, потому что видела его в своей истории. Личный
    разговор в общий чат попадать не должен, и наоборот.
    """
    with _conn() as c:
        rows = c.execute('SELECT role, content FROM chat_history WHERE user_id = ? '
                         'AND chat_id IS ? ORDER BY id DESC LIMIT ?',
                         (user_id, chat_id, limit)).fetchall()
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


def last_chat_house(chat_id, look_back=10):
    """Дом, о котором в этом чате говорили последним.

    В чате адрес называют один раз, а дальше пишут показания подряд. Чтобы
    «гвс 567» следом за «Седова 71 хвс 1234» не потерялось, берём дом из
    недавних сообщений этого же чата.
    """
    with _conn() as c:
        row = c.execute(
            'SELECT house_id FROM (SELECT house_id FROM chat_messages '
            'WHERE chat_id = ? ORDER BY id DESC LIMIT ?) WHERE house_id IS NOT NULL '
            'LIMIT 1', (chat_id, look_back)).fetchone()
    return row['house_id'] if row else None


def house_chat_records(house_id, limit=20):
    """Сообщения чата по конкретному дому, свежие первыми."""
    with _conn() as c:
        return c.execute('SELECT * FROM chat_messages WHERE house_id = ? '
                         'ORDER BY id DESC LIMIT ?', (house_id, limit)).fetchall()


def chat_context(chat_id, limit=12, minutes=180):
    """Последние реплики этого чата — от старых к новым, для контекста.

    Люся видела только одно сообщение и решала по нему. «Ах ты ж))) Думала
    за спасибо» в отрыве не понять никому: нужен разговор вокруг.
    """
    if chat_id is None:
        return []
    with _conn() as c:
        rows = c.execute(
            'SELECT user_name, text, transcript, created_at, house_id, is_issue '
            'FROM chat_messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?',
            (chat_id, limit)).fetchall()
    porog = datetime.now(IRKUTSK_TZ) - timedelta(minutes=minutes)
    svezhie = []
    for r in rows:
        try:
            kogda = datetime.strptime(r['created_at'], '%d.%m.%Y %H:%M')
        except (TypeError, ValueError):
            svezhie.append(r)
            continue
        if kogda.replace(tzinfo=IRKUTSK_TZ) >= porog:
            svezhie.append(r)
    return list(reversed(svezhie))


def chat_records_between(nachalo: str, konets: str, limit=400):
    """Лента за период — для ночного разбора. Даты «ДД.ММ.ГГГГ»."""
    with _conn() as c:
        return c.execute(
            'SELECT * FROM chat_messages WHERE substr(created_at, 1, 10) '
            'BETWEEN ? AND ? ORDER BY id LIMIT ?',
            (nachalo, konets, limit)).fetchall()


def recent_chat_records(limit=15):
    """Последние сообщения рабочего чата по всем домам, свежие первыми."""
    with _conn() as c:
        return c.execute('SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?',
                         (limit,)).fetchall()


def add_reminder(user_id, user_name, text, due_at, chat_id=None) -> int:
    """Напоминание по просьбе человека. due_at — «ДД.ММ.ГГГГ ЧЧ:ММ»."""
    with _conn() as c:
        cur = c.execute(
            'INSERT INTO reminders (chat_id, user_id, user_name, text, due_at, '
            'created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (chat_id, user_id, user_name, text, due_at, now()))
        return cur.lastrowid


def due_reminders(limit=20):
    """Напоминания, чей срок настал и которые ещё не отправлены."""
    seychas = datetime.now(IRKUTSK_TZ)
    with _conn() as c:
        rows = c.execute(
            'SELECT * FROM reminders WHERE sent_at IS NULL AND cancelled = 0 '
            'ORDER BY id LIMIT 200').fetchall()
    pora = []
    for r in rows:
        try:
            srok = datetime.strptime(r['due_at'], '%d.%m.%Y %H:%M')
        except (TypeError, ValueError):
            continue
        if srok.replace(tzinfo=IRKUTSK_TZ) <= seychas:
            pora.append(r)
    return pora[:limit]


def mark_reminder_sent(reminder_id):
    with _conn() as c:
        c.execute('UPDATE reminders SET sent_at = ? WHERE id = ?', (now(), reminder_id))


def cancel_reminder(reminder_id):
    with _conn() as c:
        c.execute('UPDATE reminders SET cancelled = 1 WHERE id = ?', (reminder_id,))


def list_reminders(user_id=None, limit=20):
    """Что ещё не сработало — свежие сверху."""
    q = ('SELECT * FROM reminders WHERE sent_at IS NULL AND cancelled = 0')
    args = []
    if user_id is not None:
        q += ' AND user_id = ?'
        args.append(user_id)
    q += ' ORDER BY due_at LIMIT ?'
    args.append(limit)
    with _conn() as c:
        return c.execute(q, args).fetchall()


def set_banter(chat_id, on: bool):
    """Разрешить или запретить Люсе живые реплики в этом чате."""
    with _conn() as c:
        c.execute('INSERT INTO chat_settings (chat_id, banter) VALUES (?, ?) '
                  'ON CONFLICT(chat_id) DO UPDATE SET banter = excluded.banter',
                  (chat_id, 1 if on else 0))


def banter_on(chat_id) -> bool:
    """По умолчанию — да: заказчик просил, чтобы она разряжала обстановку."""
    with _conn() as c:
        row = c.execute('SELECT banter FROM chat_settings WHERE chat_id = ?',
                        (chat_id,)).fetchone()
    return True if row is None else bool(row['banter'])


def chat_reports(chat_id, limit=8):
    """Последние сообщения этого чата — свежие первыми.

    Нужно, чтобы Люся отвечала на вопрос «какой адрес?» по самой ленте:
    видеоотчёт лежит в ней вместе с расшифровкой и распознанным домом.
    """
    with _conn() as c:
        return c.execute('SELECT * FROM chat_messages WHERE chat_id = ? '
                         'ORDER BY id DESC LIMIT ?', (chat_id, limit)).fetchall()


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


NEARBY_MINUTES = 20      # «рядом» — это соседнее сообщение, а не сегодняшнее


def recent_house_of(chat_id, user_id, look_back=3, minutes=NEARBY_MINUTES):
    """Дом, который этот же человек назвал в чате только что.

    Адрес часто идёт отдельным сообщением перед роликом: сначала «8/5 Салон
    красоты», потом видео. Для самого видео дома нет, а он рядом — в соседней
    строке того же автора.

    Строго по времени: без ограничения сюда подтягивался адрес, названный
    несколько часов назад совсем по другому поводу, — и видео с Трилиссера
    уезжало на 4-ю Советскую.
    """
    with _conn() as c:
        rows = c.execute(
            'SELECT house_id, created_at FROM chat_messages '
            'WHERE chat_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?',
            (chat_id, user_id, look_back)).fetchall()
    porog = datetime.now(IRKUTSK_TZ) - timedelta(minutes=minutes)
    for row in rows:
        if row['house_id'] is None:
            continue
        try:
            kogda = datetime.strptime(row['created_at'], '%d.%m.%Y %H:%M')
        except (TypeError, ValueError):
            return row['house_id']       # старая запись без разбора времени
        if kogda.replace(tzinfo=IRKUTSK_TZ) >= porog:
            return row['house_id']
        return None                      # ближайший адрес уже несвежий
    return None


ORPHAN_MINUTES = 30      # адрес называют сразу после ролика, а не через день


def orphan_report(chat_id, user_id=None, minutes=ORPHAN_MINUTES):
    """Свежий отчёт без дома, к которому относится названный следом адрес.

    Нужно, чтобы ответ «Советская 30» на вопрос «какой адрес?» доехал до
    самого отчёта, а не остался отдельной строкой в ленте.

    Раньше срока не было вовсе: любое упоминание дома цепляло ролик
    недельной давности. И автор не проверялся — адрес одного человека
    приклеивался к чужому отчёту.
    """
    q = ('SELECT * FROM chat_messages WHERE chat_id = ? AND house_id IS NULL '
         'AND has_files = 1')
    args = [chat_id]
    if user_id is not None:
        q += ' AND user_id = ?'
        args.append(user_id)
    q += ' ORDER BY id DESC LIMIT 1'
    with _conn() as c:
        row = c.execute(q, args).fetchone()
    if not row:
        return None
    porog = datetime.now(IRKUTSK_TZ) - timedelta(minutes=minutes)
    try:
        kogda = datetime.strptime(row['created_at'], '%d.%m.%Y %H:%M')
    except (TypeError, ValueError):
        return row
    return row if kogda.replace(tzinfo=IRKUTSK_TZ) >= porog else None


def last_report_of(chat_id, user_id, hours=6):
    """Последний отчёт этого человека в этом чате — тот, который поправляют.

    «Не 28 дом, а 18 б» относится к тому, что человек только что прислал,
    и почти всегда это ролик или голосовое.
    """
    with _conn() as c:
        rows = c.execute(
            'SELECT * FROM chat_messages WHERE chat_id = ? AND user_id = ? '
            'AND has_files = 1 ORDER BY id DESC LIMIT 3', (chat_id, user_id)).fetchall()
    porog = datetime.now(IRKUTSK_TZ) - timedelta(hours=hours)
    for row in rows:
        try:
            kogda = datetime.strptime(row['created_at'], '%d.%m.%Y %H:%M')
        except (TypeError, ValueError):
            return row
        if kogda.replace(tzinfo=IRKUTSK_TZ) >= porog:
            return row
    return None


def set_chat_house(record_id, house_id):
    """Привязывает сообщение чата к дому."""
    with _conn() as c:
        c.execute('UPDATE chat_messages SET house_id = ? WHERE id = ?',
                  (house_id, record_id))


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


# ---------- Опись имущества ----------

INV_HERE = 'here'          # лежит на месте
INV_GONE = 'gone'          # списано или отдано


def add_item(name, place=None, house_id=None, qty=1, note=None,
             user_id=None, user_name=None) -> int:
    with _conn() as c:
        cur = c.execute(
            'INSERT INTO inventory (name, qty, house_id, place, note, '
            'added_by, added_by_name, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (name, qty, house_id, place, note, user_id, user_name, now(), now()))
        return cur.lastrowid


def list_items(house_id=None, include_gone=False, limit=300):
    q = 'SELECT * FROM inventory WHERE 1 = 1'
    args = []
    if house_id is not None:
        q += ' AND house_id = ?'
        args.append(house_id)
    if not include_gone:
        q += ' AND status = ?'
        args.append(INV_HERE)
    q += ' ORDER BY house_id IS NULL, house_id, name LIMIT ?'
    args.append(limit)
    with _conn() as c:
        return c.execute(q, args).fetchall()


def get_item(item_id):
    with _conn() as c:
        return c.execute('SELECT * FROM inventory WHERE id = ?', (item_id,)).fetchone()


def move_item(item_id, house_id, place):
    """Вещь переехала: место меняем, историю появления не трогаем."""
    with _conn() as c:
        c.execute('UPDATE inventory SET house_id = ?, place = ?, updated_at = ? '
                  'WHERE id = ?', (house_id, place, now(), item_id))


def write_off_item(item_id):
    with _conn() as c:
        c.execute('UPDATE inventory SET status = ?, updated_at = ? WHERE id = ?',
                  (INV_GONE, now(), item_id))


def set_item_qty(item_id, qty):
    with _conn() as c:
        c.execute('UPDATE inventory SET qty = ?, updated_at = ? WHERE id = ?',
                  (max(1, int(qty)), now(), item_id))


# ---------- Замечания по квартирам ----------

def add_flat_note(house_id, flat, text, kind=None, record_id=None,
                  author_id=None, author=None) -> int:
    with _conn() as c:
        cur = c.execute(
            'INSERT INTO flat_notes (house_id, flat, kind, text, record_id, '
            'author_id, author, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (house_id, flat, kind, text, record_id, author_id, author, now()))
        return cur.lastrowid


def flat_notes(house_id, flat=None, limit=50):
    """Что находили в квартире (или во всём доме), свежее первым."""
    q = 'SELECT * FROM flat_notes WHERE house_id = ?'
    args = [house_id]
    if flat is not None:
        q += ' AND flat = ?'
        args.append(flat)
    q += ' ORDER BY id DESC LIMIT ?'
    args.append(limit)
    with _conn() as c:
        return c.execute(q, args).fetchall()


def flat_note_exists(house_id, flat, kind, hours=24) -> bool:
    """Такую же находку сегодня уже записали.

    Один выезд — одно сообщение с подписью и одно голосовое об этом же.
    Писать дважды не нужно.
    """
    if not kind:
        return False
    porog = datetime.now(IRKUTSK_TZ) - timedelta(hours=hours)
    with _conn() as c:
        rows = c.execute(
            'SELECT created_at FROM flat_notes WHERE house_id = ? AND flat = ? '
            'AND kind = ? ORDER BY id DESC LIMIT 5',
            (house_id, flat, kind)).fetchall()
    for r in rows:
        try:
            kogda = datetime.strptime(r['created_at'], '%d.%m.%Y %H:%M')
        except (TypeError, ValueError):
            return True
        if kogda.replace(tzinfo=IRKUTSK_TZ) >= porog:
            return True
    return False


def delete_flat_note(note_id):
    with _conn() as c:
        c.execute('DELETE FROM flat_notes WHERE id = ?', (note_id,))


def houses_with_flat_notes():
    """Сколько замечаний по каждому дому — для списка."""
    with _conn() as c:
        rows = c.execute('SELECT house_id, COUNT(*) n FROM flat_notes '
                         'GROUP BY house_id').fetchall()
    return {r['house_id']: r['n'] for r in rows}


# ---------- Хроника дома ----------

def add_house_fact(house_id, day, text, kind=None) -> int:
    with _conn() as c:
        cur = c.execute(
            'INSERT INTO house_facts (house_id, day, text, kind, created_at) '
            'VALUES (?, ?, ?, ?, ?)', (house_id, day, text, kind, now()))
        return cur.lastrowid


def house_facts(house_id, limit=30):
    with _conn() as c:
        return c.execute('SELECT * FROM house_facts WHERE house_id = ? '
                         'ORDER BY day DESC, id DESC LIMIT ?',
                         (house_id, limit)).fetchall()


def facts_for_day(day, limit=200):
    with _conn() as c:
        return c.execute('SELECT * FROM house_facts WHERE day = ? ORDER BY house_id, id '
                         'LIMIT ?', (day, limit)).fetchall()


def day_already_parsed(day) -> bool:
    """Разбор за этот день уже делали — второй раз не нужно."""
    with _conn() as c:
        row = c.execute('SELECT 1 FROM house_facts WHERE day = ? LIMIT 1',
                        (day,)).fetchone()
    return row is not None


# ---------- Перекрытые стояки ----------

def add_shutoff(house_id, flat, riser, floor, flats, by_id=None, by_name=None,
                res=None, original=None) -> int:
    with _conn() as c:
        cur = c.execute(
            'INSERT INTO riser_shutoffs (house_id, flat, riser, floor, flats, '
            'by_id, by_name, res, original, closed_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (house_id, flat, riser, floor,
             ','.join(str(f) for f in (flats or [])), by_id, by_name, res,
             original, now()))
        return cur.lastrowid


def open_shutoffs(house_id=None):
    """Стояки, которые сейчас перекрыты. Старые сверху — их и открывать первыми."""
    q = 'SELECT * FROM riser_shutoffs WHERE opened_at IS NULL'
    args = []
    if house_id is not None:
        q += ' AND house_id = ?'
        args.append(house_id)
    q += ' ORDER BY id'
    with _conn() as c:
        return c.execute(q, args).fetchall()


def find_shutoff(house_id, flat):
    """Открытая запись по этому стояку — чтобы «открыл» нашёл своё «перекрыл».

    Ищем по любой квартире стояка: перекрывали по 105-й, а открыть могли
    сказать про 35-ю — стояк-то один.
    """
    for row in open_shutoffs(house_id):
        spisok = [int(x) for x in (row['flats'] or '').split(',') if x.strip().isdigit()]
        if flat == row['flat'] or flat in spisok:
            return row
    return None


def get_shutoff(shutoff_id):
    with _conn() as c:
        return c.execute('SELECT * FROM riser_shutoffs WHERE id = ?',
                         (shutoff_id,)).fetchone()


def close_shutoff(shutoff_id):
    with _conn() as c:
        c.execute('UPDATE riser_shutoffs SET opened_at = ? WHERE id = ?',
                  (now(), shutoff_id))


def mark_shutoff_announced(shutoff_id):
    with _conn() as c:
        c.execute('UPDATE riser_shutoffs SET announced = 1 WHERE id = ?', (shutoff_id,))


def mark_shutoff_reminded(shutoff_id):
    with _conn() as c:
        c.execute('UPDATE riser_shutoffs SET reminded = 1 WHERE id = ?', (shutoff_id,))


def delete_shutoff(shutoff_id):
    with _conn() as c:
        c.execute('DELETE FROM riser_shutoffs WHERE id = ?', (shutoff_id,))


def main_chat():
    """Самый живой чат — туда и уходят объявления.

    Люсю добавляют не в один чат, а имени чата MAX не присылает. Считаем
    рабочим тот, где за последнюю неделю было больше всего разговора.
    """
    porog = (datetime.now(IRKUTSK_TZ) - timedelta(days=7)).strftime('%d.%m.%Y')
    with _conn() as c:
        rows = c.execute(
            'SELECT chat_id, COUNT(*) n FROM chat_messages GROUP BY chat_id '
            'ORDER BY n DESC LIMIT 5').fetchall()
    return rows[0]['chat_id'] if rows else None


# ---------- Домовые чаты ----------

def bind_house_chat(chat_id, house_id, title=None, by_name=None):
    with _conn() as c:
        c.execute('INSERT INTO house_chats (chat_id, house_id, title, bound_by, '
                  'bound_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE '
                  'SET house_id = excluded.house_id, title = excluded.title, '
                  'bound_by = excluded.bound_by, bound_at = excluded.bound_at',
                  (chat_id, house_id, title, by_name, now()))


def unbind_house_chat(chat_id):
    with _conn() as c:
        c.execute('DELETE FROM house_chats WHERE chat_id = ?', (chat_id,))


def house_chat(house_id):
    """Чат этого дома или None."""
    with _conn() as c:
        row = c.execute('SELECT chat_id FROM house_chats WHERE house_id = ? '
                        'ORDER BY bound_at DESC LIMIT 1', (house_id,)).fetchone()
    return row['chat_id'] if row else None


def chat_house(chat_id):
    """Дом, к которому привязан этот чат, или None."""
    with _conn() as c:
        row = c.execute('SELECT house_id FROM house_chats WHERE chat_id = ?',
                        (chat_id,)).fetchone()
    return row['house_id'] if row else None


def all_house_chats():
    with _conn() as c:
        return c.execute('SELECT * FROM house_chats ORDER BY house_id').fetchall()


# ---------- Личные диалоги ----------

def remember_dialog(chat_id, user_id=None):
    """Запоминает chat_id личной переписки — его неоткуда больше взять."""
    if not chat_id:
        return
    with _conn() as c:
        c.execute('INSERT INTO dialogs (chat_id, user_id, seen_at) VALUES (?, ?, ?) '
                  'ON CONFLICT(chat_id) DO UPDATE SET user_id = excluded.user_id, '
                  'seen_at = excluded.seen_at', (chat_id, user_id, now()))


def dialog_chats(limit=50):
    with _conn() as c:
        rows = c.execute('SELECT chat_id FROM dialogs ORDER BY seen_at DESC '
                         'LIMIT ?', (limit,)).fetchall()
    return [r['chat_id'] for r in rows]


def delete_user(user_id):
    """Убирает запись о пользователе. Нужно, когда бот записал сам себя."""
    with _conn() as c:
        c.execute('DELETE FROM users WHERE user_id = ?', (user_id,))


# ---------- Личные ссылки на страницу голоса ----------

def issue_token(user_id) -> str:
    """Личная ссылка на страницу записи. У человека она одна и та же."""
    import secrets

    with _conn() as c:
        row = c.execute('SELECT token FROM web_tokens WHERE user_id = ?',
                        (user_id,)).fetchone()
        if row:
            return row['token']
        token = secrets.token_urlsafe(24)
        c.execute('INSERT INTO web_tokens (token, user_id, created_at) '
                  'VALUES (?, ?, ?)', (token, user_id, now()))
        return token


def token_user(token: str):
    if not token:
        return None
    with _conn() as c:
        row = c.execute('SELECT user_id FROM web_tokens WHERE token = ?',
                        (token,)).fetchone()
    return row['user_id'] if row else None


# ---------- Что записано за день ----------

def day_journal(day: str) -> dict:
    """Всё, что появилось в базе за этот день. day — «ДД.ММ.ГГГГ».

    Каждая запись лежит в своей таблице, и посмотреть «что Люся насохраняла»
    было негде: приходилось обходить пять экранов. Здесь всё сразу.
    """
    like = day + '%'
    out = {}
    with _conn() as c:
        out['readings'] = c.execute(
            'SELECT r.*, m.label, m.house_id FROM readings r '
            'JOIN meters m ON m.id = r.meter_id '
            'WHERE r.submitted_at LIKE ? ORDER BY r.id', (like,)).fetchall()
        out['requests'] = c.execute(
            'SELECT * FROM requests WHERE created_at LIKE ? ORDER BY id',
            (like,)).fetchall()
        out['works'] = c.execute(
            'SELECT * FROM works WHERE created_at LIKE ? ORDER BY id',
            (like,)).fetchall()
        out['flat_notes'] = c.execute(
            'SELECT * FROM flat_notes WHERE created_at LIKE ? ORDER BY id',
            (like,)).fetchall()
        out['inventory'] = c.execute(
            'SELECT * FROM inventory WHERE created_at LIKE ? ORDER BY id',
            (like,)).fetchall()
        out['passports'] = c.execute(
            'SELECT * FROM passports WHERE updated_at LIKE ? ORDER BY house_id',
            (like,)).fetchall()
        out['shutoffs'] = c.execute(
            'SELECT * FROM riser_shutoffs WHERE closed_at LIKE ? ORDER BY id',
            (like,)).fetchall()
        out['meters'] = c.execute(
            'SELECT * FROM meters WHERE created_at LIKE ? ORDER BY id',
            (like,)).fetchall()
        out['reminders'] = c.execute(
            'SELECT * FROM reminders WHERE created_at LIKE ? ORDER BY id',
            (like,)).fetchall()
        out['chat'] = c.execute(
            'SELECT COUNT(*) n FROM chat_messages WHERE created_at LIKE ?',
            (like,)).fetchone()['n']
    return out


def all_chat_records(limit=1000):
    """Вся лента чата — для проверки записей на чужие адреса."""
    with _conn() as c:
        return c.execute('SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?',
                         (limit,)).fetchall()


def delete_chat_record(record_id):
    with _conn() as c:
        c.execute('DELETE FROM chat_messages WHERE id = ?', (record_id,))
