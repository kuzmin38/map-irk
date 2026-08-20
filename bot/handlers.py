"""Обработчики бота «Помощник сантехника» УК Жемчужина (мессенджер MAX)."""
import asyncio
import json
import logging
import os
import re

import aiohttp

from maxapi import Dispatcher
from maxapi.types import (
    BotStarted,
    CallbackButton,
    Command,
    CommandStart,
    LinkButton,
    MessageCallback,
    MessageCreated,
    NewMessageLink,
    OpenAppButton,
)
from maxapi.enums.message_link_type import MessageLinkType
from maxapi.types import InputMedia
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from . import agent, ai, db, houses
from . import project_docs
from . import risers as risers_mod
from . import status as bot_status
from . import transcribe

log = logging.getLogger(__name__)

# use_create_task: каждое событие обрабатывается своей задачей. Без этого
# диспетчер разбирает события по одному прямо в цикле опроса — пока Люся
# думает над вопросом к ИИ, она не спрашивает MAX о новых сообщениях, и
# бот молчит для всех сразу. Один долгий ответ подвешивал весь чат.
dp = Dispatcher(use_create_task=True)

with open(os.path.join(houses.DATA_DIR, 'directory.json'), encoding='utf-8') as f:
    DIRECTORY = json.load(f)['sections']

with open(os.path.join(houses.DATA_DIR, 'complexes.json'), encoding='utf-8') as f:
    COMPLEXES = json.load(f)
COMPLEX_NAMES = {c['id']: c['name'] for c in COMPLEXES}

with open(os.path.join(houses.DATA_DIR, 'team.json'), encoding='utf-8') as f:
    TEAM = json.load(f)
TEAM_BY_ID = {m['id']: m for m in TEAM}

# Каталог проектной документации: файлы лежат на Google Диске, бот отдаёт ссылки
with open(os.path.join(houses.DATA_DIR, 'docs_catalog.json'), encoding='utf-8') as f:
    DOCS_CATALOG = json.load(f)


# Мини-приложение MAX: заполняется в main.py при старте (username и id бота)
BOT_ME = {}

# Дата последнего изменения кода — чтобы видеть, какая сборка реально запущена
BUILD_TIME = None


def build_version() -> str:
    """Версия запущенного кода: коммит (если Railway его отдал) и дата файла."""
    global BUILD_TIME
    if BUILD_TIME is None:
        from datetime import datetime
        ts = os.path.getmtime(__file__)
        BUILD_TIME = datetime.fromtimestamp(ts, db.IRKUTSK_TZ).strftime('%d.%m.%Y %H:%M')
    sha = (os.environ.get('RAILWAY_GIT_COMMIT_SHA') or '')[:7]
    branch = os.environ.get('RAILWAY_GIT_BRANCH') or ''
    parts = [BUILD_TIME]
    if sha:
        parts.append(f'коммит {sha}')
    if branch:
        parts.append(branch)
    return ' · '.join(parts)


def miniapp_button(text: str, payload: str | None = None):
    """Кнопка открытия мини-приложения; None, если приложение не настроено."""
    if not BOT_ME.get('username'):
        return None
    return OpenAppButton(text=text, web_app=BOT_ME['username'],
                         contact_id=BOT_ME.get('user_id'), payload=payload)


def catalog_for_house(address: str) -> list:
    """Документы каталога, относящиеся к дому."""
    a = houses._norm(address)
    return [d for d in DOCS_CATALOG
            if any(houses._norm(x) == a for x in d['addresses'])]

# Роли в порядке структуры УК: руководитель → инженер → мастера →
# рабочие (сантехники, электрики, дворники, плотники)
ROLES = {
    'admin': '👑 Админ',
    'director': '👔 Руководитель',
    'engineer': '🛠 Инженер',
    'master': '📋 Мастер',
    'plumber': '🔧 Сантехник',
    'electrician': '⚡ Электрик',
    'janitor': '🧹 Дворник',
    'carpenter': '🪚 Плотник',
    'none': '⏳ Без роли',
}
ROLE_ORDER = {r: i for i, r in enumerate(ROLES)}
# Кто может назначать роли и давать задания по ЖК
MANAGER_ROLES = ('admin', 'engineer', 'master')
# Кому доступен брифинг (руководство и ИТР)
BRIEFING_ROLES = ('admin', 'director', 'engineer', 'master')


def _role(uid) -> str:
    u = db.get_user(uid)
    return u['role'] if u else 'none'


def _short_name(name: str) -> str:
    return (name or '?').split()[0][:20]


def assignable_users():
    """Кому можно делегировать работу: зарегистрированные с рабочей ролью."""
    return [u for u in db.list_users() if u['role'] not in ('none', 'director')]


async def notify(bot, user_id, text):
    """Личное сообщение пользователю; молча пропускаем, если не доставить."""
    try:
        await bot.send_message(user_id=user_id, text=text)
    except Exception:
        log.warning('Не удалось отправить уведомление пользователю %s', user_id)

# На хостинге с томом (Railway и т.п.) задайте BOT_DOCS_DIR на смонтированный диск
DOCS_DIR = os.environ.get('BOT_DOCS_DIR', os.path.join(houses.DATA_DIR, 'docs'))

PASSPORT_FIELDS = [
    ('year', 'Год постройки'),
    ('floors', 'Этажность'),
    ('entrances', 'Подъезды'),
    ('flats', 'Квартиры'),
    ('heat', 'Тепловой узел (элеватор/ИТП, расположение)'),
    ('rozliv', 'Розлив (верхний/нижний, материал, ДУ)'),
    ('hvs', 'ХВС: ввод, материал, диаметры'),
    ('gvs', 'ГВС: схема, материал, диаметры'),
    ('kanaliz', 'Канализация: материал, выпуски'),
    ('valves', 'Запорная арматура: где перекрывать'),
    ('keys', 'Доступ: ключи от подвала/ТУ'),
    ('notes', 'Примечания'),
]
PASSPORT_LABELS = dict(PASSPORT_FIELDS)

# Состояние диалога: user_id -> {'mode': ..., ...}
STATE = {}

MAX_LEN = 3800


async def send(msg, text, kb: InlineKeyboardBuilder | None = None):
    """Отправляет текст (при необходимости частями), клавиатуру цепляет к последней части.

    Факт отправки пишем в лог: когда Люся «не отвечает», надо различать
    «ответ ушёл в MAX» и «до отправки дело не дошло» — снаружи это одно и то же.
    """
    size = len(text)
    parts = []
    while len(text) > MAX_LEN:
        cut = text.rfind('\n', 0, MAX_LEN)
        cut = cut if cut > 0 else MAX_LEN
        parts.append(text[:cut])
        text = text[cut:].lstrip('\n')
    parts.append(text)
    for i, part in enumerate(parts):
        attachments = [kb.as_markup()] if kb and i == len(parts) - 1 else None
        await msg.answer(text=part, attachments=attachments)
    log.info('Ответила: %d симв., частей %d', size, len(parts))


def main_menu_kb() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text='🔍 Найти дом', payload='srch'),
           CallbackButton(text='🚿 Стояки квартир', payload='rsl'))
    kb.row(CallbackButton(text='🏘 Наши дома', payload='homes'))
    kb.row(CallbackButton(text='📋 Заявки', payload='rl'),
           CallbackButton(text='➕ Новая заявка', payload='nr'))
    kb.row(CallbackButton(text='📅 Все работы', payload='wl'),
           CallbackButton(text='🧰 Мои работы', payload='myw'))
    kb.row(CallbackButton(text='📢 Задание по ЖК', payload='camp'),
           CallbackButton(text='👥 Люди', payload='ppl'))
    kb.row(CallbackButton(text='📊 Брифинг', payload='brief'),
           CallbackButton(text='🧮 Сводка счётчиков', payload='mtall'))
    app = miniapp_button('🗺 Карта и таблицы')
    if app:
        kb.row(app, CallbackButton(text='📖 Справочник', payload='dir'))
    else:
        kb.row(CallbackButton(text='📖 Справочник', payload='dir'))
    return kb


BOT_NAME = 'Люся'  # имя помощницы — поменяйте здесь, если выбрали другое

MAIN_TEXT = (
    f'👋 Привет, я {BOT_NAME} — помощница нашего звена сантехников УК «Жемчужина».\n\n'
    'Чем помогу:\n'
    '• 🔍 Найду дом — наш или нет, и покажу точку на карте\n'
    '• 🚿 Стояки — напишите «Седова 65а/2 кв 47», скажу этаж, стояк и соседей\n'
    '• 🗂 Паспорт дома — розливы, арматура, где перекрывать, доступ\n'
    '• 📋 Заявки — запишу и буду вести: новая → в работе → выполнена\n'
    '• 📖 Справочник — телефоны, нормативы, сроки, шпаргалка по трубам\n\n'
    f'💡 Просто напишите адрес (например: «{houses.examples(1)[0]}») — я всё найду. 😉'
)


# ---------- Карточки ----------

def house_card_text(h) -> str:
    cx = db.get_house_complex(h['id'])
    cx_name = COMPLEX_NAMES.get(cx, 'не указан')
    n_docs = len(db.list_docs(h['id']))
    kind = ('🏬 Нежилое' if h.get('kind') == 'nonres' else '👷 Наш дом (УК «Жемчужина»)')
    lines = [f"🏠 {h['address']}", f'🏙 ЖК: {cx_name}', kind]
    if h.get('note'):
        lines.append(f"ℹ️ {h['note']}")
    lines += [f"📊 Заявок за год: {h['requests_year']}", f'📁 Документов: {n_docs}']
    n_points = db.points_count(h['id'])
    if n_points:
        prosrocheno = sum(1 for p in db.list_points(h['id']) if _verify_overdue(p))
        line = f'🔧 Манометров: {n_points}'
        if prosrocheno:
            line += f' (поверка просрочена: {prosrocheno})'
        lines.append(line)
    return '\n'.join(lines)


def house_card_kb(h) -> InlineKeyboardBuilder:
    """Карточка дома: только частое, остальное — в разделе «Техника»."""
    gis, ya = houses.map_links(h)
    kb = InlineKeyboardBuilder()
    kb.row(LinkButton(text='🗺 2ГИС', url=gis),
           LinkButton(text='🗺 Яндекс', url=ya))
    kb.row(CallbackButton(text='➕ Заявка сюда', payload=f"nrh:{h['id']}"),
           CallbackButton(text='📅 Работы дома', payload=f"wlh:{h['id']}"))
    row = [CallbackButton(text='🔧 Техника дома', payload=f"tech:{h['id']}")]
    block, _ = risers_mod.find_block(h['address'])
    if block:
        row.append(CallbackButton(text='🚿 Стояки', payload=f"rsv:{block['id']}"))
    kb.row(*row)
    app = miniapp_button('🗺 Открыть в приложении', payload=f"house:{h['id']}")
    if app:
        kb.row(app, CallbackButton(text='🏠 Меню', payload='menu'))
    else:
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
    return kb


def tech_kb(h) -> InlineKeyboardBuilder:
    """Техника дома: паспорт, оборудование, счётчики, документы, история."""
    kb = InlineKeyboardBuilder()
    n_points = db.points_count(h['id'])
    n_docs = len(db.list_docs(h['id'])) + len(catalog_for_house(h['address']))
    kb.row(CallbackButton(text='🗂 Паспорт дома', payload=f"p:{h['id']}"),
           CallbackButton(text=f'🔧 Оборудование ТП{f" ({n_points})" if n_points else ""}',
                          payload=f"eq:{h['id']}"))
    kb.row(CallbackButton(text='🧮 Счётчики', payload=f"mt:{h['id']}"),
           CallbackButton(text=f'📁 Документы{f" ({n_docs})" if n_docs else ""}',
                          payload=f"dl:{h['id']}"))
    n_chat = len(db.house_chat_records(h['id'], limit=99))
    kb.row(CallbackButton(text='📜 История работ', payload=f"hist:{h['id']}"),
           CallbackButton(text=f'💬 Из чата{f" ({n_chat})" if n_chat else ""}',
                          payload=f"chat:{h['id']}"))
    kb.row(CallbackButton(text='🏙 Указать ЖК', payload=f"cxs:{h['id']}"))
    kb.row(CallbackButton(text='🏠 К дому', payload=f"h:{h['id']}"),
           CallbackButton(text='🏠 Меню', payload='menu'))
    return kb


def passport_text(h) -> str:
    data = db.get_passport(h['id'])
    lines = [f"🗂 ПАСПОРТ ДОМА: {h['address']}", '']
    filled = 0
    for key, label in PASSPORT_FIELDS:
        val = data.get(key)
        if val:
            filled += 1
            lines.append(f'▪️ {label}:\n   {val}')
        else:
            lines.append(f'▫️ {label}: —')
    lines.append('')
    lines += equipment_lines(h)
    lines.append(f'Заполнено: {filled}/{len(PASSPORT_FIELDS)}. '
                 'Нажмите «Редактировать», чтобы дополнить.')
    return '\n'.join(lines)


def equipment_lines(h) -> list:
    """Приборы дома — в самом паспорте, а не только в разделе «Техника».

    Паспорт для того и нужен, чтобы всё про дом было в одном месте: что
    стоит, с каким номером и до какого числа поверка.
    """
    points = db.list_points(h['id'])
    if not points:
        return ['🔧 Манометры: не заведены', '']
    lines = [f'🔧 Манометры ({len(points)}):']
    for p in points:
        lines.append('   ' + point_line(p).replace('\n   ', '\n      '))
    lines.append('')
    return lines


def request_card_text(r) -> str:
    return (f"📋 Заявка №{r['id']} — {db.STATUS_LABELS.get(r['status'], r['status'])}\n"
            f"🏠 {r['address']}\n"
            f"📝 {r['description']}\n"
            f"👤 {r['created_by_name'] or '—'}\n"
            f"🕐 Создана: {r['created_at']} | Обновлена: {r['updated_at']}")


def request_card_kb(r) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    row = []
    if r['status'] != db.STATUS_WORK:
        row.append(CallbackButton(text='🔧 В работу', payload=f"rs:{r['id']}:work"))
    if r['status'] != db.STATUS_DONE:
        row.append(CallbackButton(text='✅ Выполнена', payload=f"rs:{r['id']}:done"))
    if r['status'] == db.STATUS_DONE:
        row.append(CallbackButton(text='↩️ Вернуть в работу', payload=f"rs:{r['id']}:work"))
    kb.row(*row)
    kb.row(CallbackButton(text='📋 Все заявки', payload='rl'),
           CallbackButton(text='🏠 Меню', payload='menu'))
    return kb


# ---------- Работы (график, дедлайны) ----------

def parse_deadline(text: str):
    """'25.09' / '25.09.2026' → 'ГГГГ-ММ-ДД'; '-' → None; иначе ValueError."""
    text = text.strip()
    if text in ('-', '—', ''):
        return None
    m = re.match(r'^(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?$', text)
    if not m:
        raise ValueError
    from datetime import date
    d, mo = int(m.group(1)), int(m.group(2))
    today = date.today()
    y = int(m.group(3)) if m.group(3) else today.year
    if y < 100:
        y += 2000
    dl = date(y, mo, d)
    if not m.group(3) and dl < today:
        dl = date(y + 1, mo, d)  # без года и дата прошла — значит, следующий год
    return dl.isoformat()


def fmt_deadline(iso: str | None) -> str:
    if not iso:
        return 'без срока'
    from datetime import date
    y, m, d = iso.split('-')
    label = f'{d}.{m}.{y}'
    days = (date(int(y), int(m), int(d)) - date.today()).days
    if days < 0:
        return f'⚠️ {label} (просрочено на {-days} дн.)'
    if days == 0:
        return f'🔥 {label} (сегодня!)'
    if days <= 3:
        return f'⏰ {label} (через {days} дн.)'
    return label


def work_line(w) -> str:
    h = houses.HOUSES_BY_ID.get(w['house_id'])
    addr = h['address'] if h else '?'
    who = f" · {w['assignee']}" if w['assignee'] else ''
    return f"№{w['id']} {db.WORK_LABELS[w['status']].split()[0]} {addr}: {w['title']} — {fmt_deadline(w['deadline'])}{who}"


def work_card_text(w) -> str:
    h = houses.HOUSES_BY_ID.get(w['house_id'])
    lines = [f"📅 Работа №{w['id']} — {db.WORK_LABELS[w['status']]}",
             f"🏠 {h['address'] if h else '?'}",
             f"🔧 {w['title']}",
             f"⏳ Срок: {fmt_deadline(w['deadline'])}",
             f"👤 Ответственный: {w['assignee'] or 'не назначен'}"]
    if w['details']:
        lines.append(f"📝 {w['details']}")
    lines.append(f"🕐 Создана: {w['created_at']} ({w['created_by_name'] or '—'})")
    return '\n'.join(lines)


def work_card_kb(w) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    users = assignable_users()
    if users:
        for i in range(0, len(users), 3):
            kb.row(*[CallbackButton(text=f"👤 {_short_name(u['name'])}",
                                    payload=f"wa:{w['id']}:{u['user_id']}")
                     for u in users[i:i + 3]])
    else:
        kb.row(*[CallbackButton(text=f"👤 {m['short']}", payload=f"wat:{w['id']}:{m['id']}")
                 for m in TEAM])
    row = []
    if w['status'] != db.WORK_IN_PROGRESS:
        row.append(CallbackButton(text='🔧 В работу', payload=f"ws:{w['id']}:work"))
    if w['status'] != db.WORK_DONE:
        row.append(CallbackButton(text='✅ Сдано', payload=f"ws:{w['id']}:done"))
    else:
        row.append(CallbackButton(text='↩️ Вернуть', payload=f"ws:{w['id']}:work"))
    kb.row(*row)
    kb.row(CallbackButton(text='⏳ Изменить срок', payload=f"wd:{w['id']}"),
           CallbackButton(text='📝 Материалы/заметка', payload=f"wn:{w['id']}"))
    kb.row(CallbackButton(text='📅 Все работы', payload='wl'),
           CallbackButton(text='🏠 К дому', payload=f"h:{w['house_id']}"))
    return kb


# ---------- Стояки и квартиры ----------

def riser_card_text(block, addr, flat, floor, riser, on_floor) -> str:
    chain = risers_mod.riser_flats(block, riser)
    partial = risers_mod.partial_floors(block)
    lines = [f'🚿 {addr}, кв. {flat}',
             f'🔢 Этаж: {floor}',
             f"🚰 Стояк: {riser}-й из {block['risers']} (слева направо)", '']
    if chain:
        neighbours = []
        for fl, fnum in chain:
            mark = ' ⬅️' if fnum == flat else ''
            neighbours.append(f'  {fl} эт. — кв. {fnum}{mark}')
        lines.append('📍 Весь стояк снизу вверх:')
        lines += neighbours
        idx = next((i for i, (_, fnum) in enumerate(chain) if fnum == flat), None)
        if idx is not None:
            below = f'кв. {chain[idx - 1][1]}' if idx > 0 else 'нет (низ стояка)'
            above = f'кв. {chain[idx + 1][1]}' if idx + 1 < len(chain) else 'нет (верх стояка)'
            lines += ['', f'⬇️ Снизу: {below}', f'⬆️ Сверху: {above}']
    if partial:
        lines += ['', f"ℹ️ На этаж{'ах' if len(partial) > 1 else 'е'} "
                      f"{', '.join(map(str, partial))} квартир меньше "
                      '(нежилые помещения) — крайних стояков там нет.']
    return '\n'.join(lines)


def riser_card_kb(block, addr) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text='🚿 Все стояки дома', payload=f"rsv:{block['id']}"))
    hs = houses.search(addr, limit=1)
    if hs:
        kb.row(CallbackButton(text='🏠 Карточка дома', payload=f"h:{hs[0]['id']}"),
               CallbackButton(text='🏠 Меню', payload='menu'))
    else:
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
    return kb


# ---------- Оборудование ТП (манометры) ----------

# Типовые места установки — чтобы в подвале не набирать текст
PLACES = [
    'Подача отопления',
    'Обратка отопления',
    'Подача ГВС',
    'Циркуляция ГВС',
    'До элеватора',
    'После элеватора',
    'Ввод ХВС',
]
TP_LIST = ['ТП №1', 'ТП №2', 'ТП №3', 'ТП №4', 'без номера']


def fmt_date(iso: str | None) -> str:
    """ГГГГ-ММ-ДД → ДД.ММ.ГГГГ."""
    if not iso:
        return '—'
    try:
        y, m, d = iso.split('-')
        return f'{d}.{m}.{y}'
    except ValueError:
        return iso


# Межповерочный интервал манометров: на приборе стоит клеймо поверки,
# годен он с этой даты ещё столько лет.
VERIFY_YEARS = 2

_MONTHS = {'янв': 1, 'фев': 2, 'мар': 3, 'апр': 4, 'май': 5, 'мая': 5, 'июн': 6,
           'июл': 7, 'авг': 8, 'сен': 9, 'окт': 10, 'ноя': 11, 'дек': 12}


def parse_verify(text: str):
    """Поверка манометра → ('ГГГГ-ММ-ДД', пояснение). '-' → (None, '').

    На приборе стоит клеймо: месяц и год, когда поверяли. Человек так и
    говорит — «поверка июль двадцать шестого», — а срок годности считается
    прибавлением межповерочного интервала. Поэтому понимаем оба вида:

      «июль 2026», «07.2026»  — клеймо, прибавляем интервал
      «до 07.2028», «до 25.09.2028», «25.09.2028» — уже срок годности
    """
    from calendar import monthrange
    from datetime import date

    text = text.strip().lower().replace('ё', 'е')
    if text in ('-', '—', ''):
        return None, ''

    srok = text.startswith('до')
    if srok:
        text = text[2:].strip()

    # полная дата с днём — это всегда срок годности, как было раньше
    m = re.match(r'^(\d{1,2})[./](\d{1,2})[./](\d{2,4})$', text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y += 2000 if y < 100 else 0
        return date(y, mo, d).isoformat(), ''

    # месяц и год: «07.2026» или «июль 2026»
    m = re.match(r'^(\d{1,2})[./\s](\d{2,4})$', text)
    if m:
        mo, y = int(m.group(1)), int(m.group(2))
    else:
        m = re.match(r'^([а-я]{3,})\s*(\d{2,4})$', text)
        if not m or m.group(1)[:3] not in _MONTHS:
            raise ValueError
        mo, y = _MONTHS[m.group(1)[:3]], int(m.group(2))
    if not 1 <= mo <= 12:
        raise ValueError
    y += 2000 if y < 100 else 0

    if srok:
        # срок годности до конца названного месяца
        return date(y, mo, monthrange(y, mo)[1]).isoformat(), ''
    god = y + VERIFY_YEARS
    return (date(god, mo, monthrange(god, mo)[1]).isoformat(),
            f'клеймо {mo:02d}.{y} + {VERIFY_YEARS} года')


def fmt_verify(iso: str | None) -> str:
    """Срок поверки с пометкой, если скоро истекает или уже истёк."""
    if not iso:
        return 'не указана'
    from datetime import date
    y, m, d = iso.split('-')
    label = f'{d}.{m}.{y}'
    days = (date(int(y), int(m), int(d)) - datetime_today()).days
    if days < 0:
        return f'❌ {label} (просрочена на {-days} дн.)'
    if days <= 30:
        return f'⚠️ {label} (осталось {days} дн.)'
    return f'✅ {label}'


def datetime_today():
    from datetime import datetime
    return datetime.now(db.IRKUTSK_TZ).date()


def _verify_overdue(p) -> bool:
    """Поверка прибора в этой точке просрочена."""
    from datetime import date

    dev = db.active_device(p['id'])
    if not dev or not dev['verified_until']:
        return False
    y, m, d = dev['verified_until'].split('-')
    return date(int(y), int(m), int(d)) < datetime_today()


def point_line(p) -> str:
    dev = db.active_device(p['id'])
    head = f"{p['tp'] + ', ' if p['tp'] else ''}{p['place']}"
    if not dev:
        return f'▫️ {head} — прибора нет'
    return (f"▪️ {head}\n"
            f"   № {dev['serial'] or '—'} · поверка: {fmt_verify(dev['verified_until'])}")


def point_card_text(p) -> str:
    h = houses.HOUSES_BY_ID.get(p['house_id'])
    dev = db.active_device(p['id'])
    lines = [f"🔧 {p['tp'] + ', ' if p['tp'] else ''}{p['place']}",
             f"🏠 {h['address'] if h else '?'}", '']
    if dev:
        lines += [f"📟 Заводской номер: {dev['serial'] or '—'}",
                  f"📅 Поверка до: {fmt_verify(dev['verified_until'])}",
                  f"👤 Установил: {dev['installed_by'] or '—'}",
                  f"🕐 Установлен: {dev['installed_at'] or '—'}"]
        photos = []
        if dev['photo_device']:
            photos.append('прибор')
        if dev['photo_passport']:
            photos.append('паспорт')
        lines.append(f"📷 Фото: {', '.join(photos) if photos else 'нет'}")
        if dev['note']:
            lines.append(f"📝 {dev['note']}")
    else:
        lines.append('Прибор не установлен.')
    return '\n'.join(lines)


def point_card_kb(p) -> InlineKeyboardBuilder:
    dev = db.active_device(p['id'])
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text='🔄 Заменить прибор' if dev else '➕ Поставить прибор',
                          payload=f"eqnew:{p['id']}"))
    if dev:
        row = []
        if dev['photo_device']:
            row.append(CallbackButton(text='📷 Фото прибора', payload=f"eqph:{dev['id']}:device"))
        if dev['photo_passport']:
            row.append(CallbackButton(text='📄 Фото паспорта', payload=f"eqph:{dev['id']}:passport"))
        if row:
            kb.row(*row)
        kb.row(CallbackButton(text='📷 Добавить фото', payload=f"eqphadd:{dev['id']}"),
               CallbackButton(text='📜 История', payload=f"eqhist:{p['id']}"))
    kb.row(CallbackButton(text='🔧 Все точки дома', payload=f"eq:{p['house_id']}"),
           CallbackButton(text='🏠 К дому', payload=f"h:{p['house_id']}"))
    return kb


# ---------- Счётчики ----------

# Вопрос понят, но модель не поспела. Говорим об этом прямо: «ничего не нашла»
# в такой ситуации — неправда, и человек зря решает, что спрашивать бесполезно.
SLOW_REPLY = ('⏳ Задумалась и не успела ответить — модель сегодня отвечает медленно.\n'
              'Повторите вопрос, пожалуйста: обычно со второго раза получается.')


METER_KINDS = {
    'hvs': '💧 ХВС (водомер)',
    'gvs': '🔥 ГВС (водомер)',
    'heat': '♨️ Тепло (теплосчётчик)',
    'other': '📟 Другой',
}

MONTHS_RU = ['янв', 'фев', 'мар', 'апр', 'май', 'июн',
             'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']


def current_period() -> str:
    from datetime import datetime
    return datetime.now(db.IRKUTSK_TZ).strftime('%Y-%m')


def fmt_period(period: str) -> str:
    y, m = period.split('-')
    return f'{MONTHS_RU[int(m) - 1]} {y}'


def fmt_value(v: float) -> str:
    return f'{v:g}'


def meter_line(m, with_last=True) -> str:
    line = f"{METER_KINDS.get(m['kind'], '📟')} {m['label']}"
    if with_last:
        rs = db.meter_readings(m['id'], limit=1)
        if rs:
            r = rs[0]
            line += f"\n   последнее: {fmt_value(r['value'])} — {fmt_period(r['period'])} ({r['submitted_by_name'] or '—'})"
        else:
            line += '\n   показаний ещё нет'
    return line


# Как вид счётчика называют вслух и в переписке
METER_WORDS = {
    'hvs': ('хвс', 'холодная', 'холодную', 'холодной', 'хв', 'хол'),
    'gvs': ('гвс', 'горячая', 'горячую', 'горячей', 'гв', 'гор'),
    'heat': ('тепло', 'теплосчетчик', 'теплосчётчик', 'отопление', 'отоплению', 'гкал'),
}

_READING_RE = re.compile(r'(\d+(?:[.,]\d+)?)')


def parse_readings(text: str):
    """Разбирает «Седова 71 хвс 1234, гвс 567» → ('Седова 71', [('hvs', 1234)]).

    Сантехник стоит у прибора с телефоном — ходить по меню ему неудобно.
    Пишет как говорит. Возвращаем отдельно кусок с адресом и пары
    «вид — число»: вид без числа и число без вида не считаются.

    Адрес отрезаем по первому слову о счётчике. Иначе в «Седова 71 хвс 67»
    показание 67 спуталось бы с номером дома — а дом Седова 67 существует.
    """
    low = text.lower().replace('ё', 'е')
    # находим позиции слов вида и чисел, дальше сопоставляем по порядку
    metki = []
    for kind, words in METER_WORDS.items():
        for w in words:
            for m in re.finditer(rf'(?<![а-я]){re.escape(w)}(?![а-я])', low):
                metki.append((m.start(), 'kind', kind))
    for m in _READING_RE.finditer(low):
        metki.append((m.start(), 'value', m.group(1)))
    metki.sort()

    result, ozhidaet = [], None
    for _, tip, val in metki:
        if tip == 'kind':
            ozhidaet = val
        elif ozhidaet is not None:
            result.append((ozhidaet, float(val.replace(',', '.'))))
            ozhidaet = None

    pervoe = next((pos for pos, tip, _ in metki if tip == 'kind'), None)
    adres = text[:pervoe].strip(' ,;.—-') if pervoe is not None else ''
    return adres, result


def pick_meter(house_id: int, kind: str):
    """Счётчик дома нужного вида. None — если нет, список — если их несколько."""
    same = [m for m in db.list_meters(house_id) if m['kind'] == kind]
    if not same:
        return None
    return same[0] if len(same) == 1 else same


# Вопрос, а не сдача показаний: «сколько было хвс 1234?» записывать нельзя
_VOPROS = re.compile(r'\?|^\s*(сколько|какой|какие|какое|когда|где|что|почему)\b',
                     re.IGNORECASE)


async def handle_readings(event, text: str, uid: int) -> bool:
    """Показания из свободного текста → в учёт. True, если сообщение разобрано.

    Работает и в личке, и в рабочем чате: сантехник пишет туда, где ему
    удобно, а данные должны попадать в одно место.
    """
    if _VOPROS.search(text or ''):
        return False
    adres_text, readings = parse_readings(text)
    if not readings:
        return False

    h = houses.detect_house(adres_text) or houses.detect_house(text)
    if not h and adres_text:
        found = houses.search(adres_text)
        h = found[0] if len(found) == 1 else None
    if not h and is_group(event):
        # В чате адрес называют один раз, дальше пишут показания подряд
        chat_id = getattr(event.message.recipient, 'chat_id', None)
        house_id = db.last_chat_house(chat_id) if chat_id else None
        h = houses.HOUSES_BY_ID.get(house_id) if house_id else None
    if not h:
        await send(event.message,
                   '🏠 Не поняла, по какому дому показания. Напишите с адресом, '
                   f'например: «{_primer(0)} хвс 1234».')
        return True

    otvety, nekuda = [], []
    for kind, value in readings:
        m = pick_meter(h['id'], kind)
        if m is None:
            nekuda.append((kind, value))
        elif isinstance(m, list):
            kb = InlineKeyboardBuilder()
            for one in m:
                kb.row(CallbackButton(text=f"✍️ {one['label'][:35]}",
                                      payload=f"mtr:{one['id']}"))
            await send(event.message,
                       f"🤔 На {h['address']} несколько счётчиков "
                       f'«{METER_KINDS[kind]}». Куда записать {fmt_value(value)}?', kb)
            return True
        else:
            otvety.append(await record_reading(event, m, value, uid))

    if otvety:
        await send(event.message, '\n'.join(otvety))
    if nekuda:
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='➕ Завести счётчик', payload=f"mta:{h['id']}"))
        kb.row(CallbackButton(text='🧮 Счётчики дома', payload=f"mt:{h['id']}"))
        vidy = ', '.join(METER_KINDS[k] for k, _ in nekuda)
        await send(event.message,
                   f"🤔 На {h['address']} нет счётчика: {vidy}. Заведите — "
                   'и дальше хватит одного сообщения.', kb)
    return True


async def record_reading(event, m, value: float, uid: int) -> str:
    """Записывает показание и возвращает строку ответа. Аномалию рассылает сама."""
    h = houses.HOUSES_BY_ID.get(m['house_id'])
    delta, warning = check_anomaly(m['id'], value)
    reading_id = db.add_reading(m['id'], value, current_period(), uid, _uname(event))

    photo = await _save_reading_photo(event, reading_id)
    line = (f"✅ {h['address'] if h else ''} — {m['label']}: "
            f'{fmt_value(value)} ({fmt_period(current_period())})')
    if delta is not None and delta >= 0:
        line += f', расход {fmt_value(delta)}'
    if photo:
        line += ', фото сохранено'
    if warning:
        line += f'\n   ⚠️ {warning}'
        for u in db.list_users():
            if u['role'] in ('engineer', 'admin', 'director') and u['user_id'] != uid:
                await notify(event.bot, u['user_id'],
                             f"⚠️ Счётчики, {h['address'] if h else ''} — {m['label']}: "
                             f'{warning} (подал {_uname(event)})')
    return line


async def _save_reading_photo(event, reading_id: int) -> bool:
    """Фото счётчика к показанию: снимок с табло — лучшее подтверждение цифры."""
    for a in (event.message.body.attachments or []):
        url = getattr(a.payload, 'url', None) if a.payload else None
        if not url:
            continue
        folder = os.path.join(DOCS_DIR, 'readings')
        os.makedirs(folder, exist_ok=True)
        try:
            data = await _download(url)
        except Exception:
            log.exception('Не удалось скачать фото счётчика')
            return False
        path = os.path.join(folder, f'{reading_id}.jpg')
        with open(path, 'wb') as f:
            f.write(data)
        db.set_reading_photo(reading_id, path)
        return True
    return False


def check_anomaly(meter_id, new_value):
    """Сравнивает расход с прошлым периодом. Возвращает (delta, предупреждение | None)."""
    rs = db.meter_readings(meter_id, limit=3)  # без нового показания
    if not rs:
        return None, None
    delta = new_value - rs[0]['value']
    if delta < 0:
        return delta, ('показание МЕНЬШЕ предыдущего '
                       f"({fmt_value(new_value)} < {fmt_value(rs[0]['value'])}). "
                       'Проверьте, не перепутан ли счётчик.')
    if len(rs) >= 2:
        prev_delta = rs[0]['value'] - rs[1]['value']
        if prev_delta > 0 and delta > prev_delta * 1.8:
            return delta, (f'расход {fmt_value(delta)} заметно выше прошлого периода '
                           f'({fmt_value(prev_delta)}). Возможна утечка — стоит проверить.')
    return delta, None


def _brief_lines() -> list:
    """Собирает данные брифинга (общие для текстовой и ИИ-версии)."""
    from datetime import datetime, timedelta
    today = datetime.now(db.IRKUTSK_TZ).date()
    all_works = db.list_works(open_only=False, limit=500)
    in_progress = [w for w in all_works if w['status'] == db.WORK_IN_PROGRESS]
    overdue = [w for w in all_works if w['status'] != db.WORK_DONE and w['deadline']
               and w['deadline'] < today.isoformat()]
    week = [w for w in all_works if w['status'] == db.WORK_PLAN and w['deadline']
            and today.isoformat() <= w['deadline'] <= (today + timedelta(days=7)).isoformat()]
    done_recent = [w for w in db.list_done_works(limit=100) if w['done_at']
                   and w['done_at'] >= (today - timedelta(days=1)).isoformat()]
    open_reqs = db.list_requests()
    lines = [f"📊 БРИФИНГ на {today.strftime('%d.%m.%Y')}", '']
    lines.append(f'🔧 В работе сейчас — {len(in_progress)}:')
    for w in in_progress[:15]:
        lines.append('  ' + work_line(w))
    if overdue:
        lines.append('')
        lines.append(f'⚠️ Просрочено — {len(overdue)}:')
        for w in overdue[:10]:
            lines.append('  ' + work_line(w))
    lines.append('')
    lines.append(f'✅ Сдано вчера-сегодня — {len(done_recent)}:')
    for w in done_recent[:15]:
        h = houses.HOUSES_BY_ID.get(w['house_id'])
        line = f"  ✅ {h['address'] if h else '?'} — {w['title']} ({w['assignee'] or '—'})"
        if w['report']:
            line += f" · «{w['report'][:60]}»"
        lines.append(line)
    if week:
        lines.append('')
        lines.append(f'📌 Ближайшая неделя — {len(week)}:')
        for w in week[:15]:
            lines.append('  ' + work_line(w))
    camps = db.list_campaigns()
    active = []
    for camp in camps:
        done, total = db.campaign_progress(camp['id'])
        if total and done < total:
            active.append(f"  📢 «{camp['title']}» ({COMPLEX_NAMES.get(camp['complex_id'], '')}): "
                          f'{done}/{total}, срок {fmt_deadline(camp["deadline"])}')
    if active:
        lines.append('')
        lines.append('📢 Задания в ходу:')
        lines += active
    with_meters = db.houses_with_meters()
    if with_meters:
        submitted = {r['house_id'] for r in db.readings_for_period(current_period())}
        lines.append('')
        lines.append(f'🧮 Показания за {fmt_period(current_period())}: '
                     f'сдано {len(submitted)} из {len(with_meters)} домов')

    day = today.strftime('%d.%m.%Y')
    chat_today = db.chat_stats_for_day(day)
    if chat_today['total']:
        lines.append('')
        lines.append(f"💬 Рабочий чат сегодня: {chat_today['total']} сообщений, "
                     f"по домам — {chat_today['with_house']}, "
                     f"фото и файлов — {chat_today['with_files']}")
        issues = [r for r in db.recent_issues(limit=5) if r['created_at'].startswith(day)]
        if issues:
            lines.append(f'🔴 Похоже на аварийное — {len(issues)}:')
            for r in issues:
                hh = houses.HOUSES_BY_ID.get(r['house_id']) if r['house_id'] else None
                lines.append(f"   • {hh['address'] + ': ' if hh else ''}"
                             f"{(r['text'] or '')[:70]} ({r['user_name'] or '—'})")

    lines.append('')
    lines.append(f'📋 Открытых заявок: {len(open_reqs)}')
    return lines


def _brief_data_text() -> str:
    return '\n'.join(_brief_lines())


# ---------- Старт ----------

@dp.bot_started()
async def on_bot_started(event: BotStarted):
    await event.bot.send_message(chat_id=event.chat_id, text=MAIN_TEXT,
                                 attachments=[main_menu_kb().as_markup()])


@dp.message_created(CommandStart())
async def on_start(event: MessageCreated):
    db.upsert_user(_uid(event), _uname(event))
    STATE.pop(_uid(event), None)
    await send(event.message, MAIN_TEXT, main_menu_kb())


@dp.message_created(Command('menu'))
async def on_menu(event: MessageCreated):
    STATE.pop(_uid(event), None)
    await send(event.message, MAIN_TEXT, main_menu_kb())


@dp.message_created(Command('version'))
async def on_version(event: MessageCreated):
    """Какая сборка сейчас работает — чтобы не гадать, доехало обновление или нет."""
    from .webapp import public_url
    app_url = public_url()
    ffmpeg_ok = transcribe.ffmpeg_available()
    rec = 'работает' if (ffmpeg_ok and ai.enabled()) else (
        'нет ffmpeg' if not ffmpeg_ok else 'нет ключа ИИ')
    await send(event.message,
               f'🛠 Сборка: {build_version()}\n'
               f"🤖 Бот: {BOT_ME.get('username') or 'username не получен'}\n"
               f"🧠 ИИ: {'подключён' if ai.enabled() else 'выключен'}\n"
               f'🎙 Расшифровка видео: {rec}\n'
               f'⏱ Ответ на серию роликов: через {SERIES_WINDOW // 60} мин после последнего\n'
               f"🗺 Приложение: {app_url or 'домен не выдан'}")


@dp.message_created(Command('reset'))
async def on_reset(event: MessageCreated):
    """Стереть память разговора: Люся перестаёт опираться на прошлые ответы."""
    n = db.forget_user(_uid(event))
    await send(event.message,
               f'🧹 Забыла нашу переписку ({n} сообщ.) и всё, что о вас запомнила.\n'
               'Данные по домам, приборам и заявкам не тронуты — это только память разговора.',
               main_menu_kb())


@dp.message_created(Command('chat'))
async def on_chat_log(event: MessageCreated):
    """Что Люся услышала в рабочем чате — включая расшифровки без привязки к дому.

    Нужна, чтобы проверить работу молча: в чат она отвечает только на
    аварийное, а всё остальное складывает в базу без единого слова.
    """
    records = db.recent_chat_records(limit=12)
    if not records:
        await send(event.message,
                   '💬 В базе пока пусто.\n\n'
                   'Люся видит сообщения только тех чатов, куда её добавили '
                   'администратором. Без прав администратора MAX отдаёт ей '
                   'лишь сообщения с прямым обращением.')
        return

    voiced = sum(1 for r in records if r['transcript'])
    files = sum(1 for r in records if r['has_files'])
    lines = [f'💬 Последние {len(records)} сообщений рабочего чата',
             f'📎 с вложениями: {files} · 🎙 расшифровано: {voiced}', '']
    for r in records:
        house = houses.HOUSES_BY_ID.get(r['house_id']) if r['house_id'] else None
        where = house['address'] if house else 'дом не определён'
        mark = '🚨 ' if r['is_issue'] else ''
        lines.append(f"{mark}{r['created_at']} · {r['user_name'] or '—'} · {where}")
        if r['text']:
            lines.append(f"   {r['text'][:120]}")
        if r['transcript']:
            lines.append(f"   🎙 {r['transcript'][:200]}")
        elif r['has_files']:
            lines.append('   📎 вложение, расшифровки нет')
    await send(event.message, '\n'.join(lines))


# ---------- Текстовые сообщения (поиск + шаги диалогов) ----------

def _primer(i: int) -> str:
    """Адрес-пример для подсказок. Берём из домов в работе, а не из текста:
    после сужения списка зашитые примеры указывали в пустоту."""
    got = houses.examples(2)
    return got[i] if i < len(got) else ''


def _uid(event) -> int:
    return event.message.sender.user_id


def _uname(event) -> str:
    return getattr(event.message.sender, 'full_name', None) or ''


def _uname_cb(event) -> str:
    """Имя пользователя из callback-события."""
    return getattr(event.callback.user, 'full_name', None) or ''


def is_group(event) -> bool:
    """Групповой чат или канал (в отличие от лички)."""
    recipient = getattr(event.message, 'recipient', None)
    chat_type = getattr(recipient, 'chat_type', None)
    chat_type = getattr(chat_type, 'value', chat_type)
    return chat_type in ('chat', 'channel')


# Как к Люсе обращаются в чате: по имени, с @ или без
ADDRESS_RE = re.compile(
    r'^\s*@?(люс[яеию]|lusya|lyusya)\b[\s,:—-]*', re.IGNORECASE)


# Слова, по которым сообщение похоже на заявку/аварию
ISSUE_WORDS = re.compile(
    r'\b(теч[её]т|течь|подтека|кап[аи]ет|затопил|топит|залив|прорв|порыв|свищ|'
    r'засор|забил|не\s+работает|нет\s+воды|нет\s+гвс|нет\s+хвс|нет\s+отоплен|'
    r'холодн[ыа]я\s+батаре|авари|сорвал|потоп|фонтан|срочно)', re.IGNORECASE)


SPEECH_TYPES = ('audio', 'video')


def speech_url(body) -> str | None:
    """Ссылка на голосовое или видео во вложениях — то, что можно расшифровать."""
    for a in (getattr(body, 'attachments', None) or []):
        a_type = getattr(getattr(a, 'type', None), 'value', getattr(a, 'type', None))
        if a_type in SPEECH_TYPES:
            url = getattr(a.payload, 'url', None) if a.payload else None
            if url:
                return url
    return None


async def short_summary(parts: list[str], address: str | None) -> str:
    """Сжимает серию отчётов до пересказа: что случилось и что сделали."""
    if len(parts) == 1:
        task = ('Отчёт сантехника с объекта: «{}»\n\n'
                'Перескажи сутью в одну-две строки: что случилось и что сделали. '
                'Без вступлений и без адреса — только суть.').format(parts[0])
    else:
        joined = '\n'.join(f'{i}. {p}' for i, p in enumerate(parts, 1))
        task = ('Сантехник прислал несколько видео с одного объекта по порядку. '
                'Обычно на первом — сама проблема, на следующих — ход и результат работы.\n\n'
                f'{joined}\n\n'
                'Сведи в короткий отчёт из двух строк:\n'
                'Проблема: ...\nСделано: ...\n'
                'Без вступлений и без адреса.')
    summary = await ai.ask(task, max_tokens=200, temperature=0.2)
    if not summary:
        full = ' '.join(parts)
        summary = full[:250] + ('…' if len(full) > 250 else '')
    head = f'🎙 {address}' if address else '🎙 Видеоотчёт'
    if len(parts) > 1:
        head += f' · {len(parts)} видео'
    return head + '\n' + summary.strip()


# Серии видео копим по (чат, автор): первое обычно проблема, дальше — работа.
# Ответ откладываем, пока летит серия, чтобы не отвечать на каждый ролик.
SERIES_WINDOW = int(os.environ.get('VIDEO_SERIES_WINDOW', '420'))  # секунд
SERIES: dict[tuple, dict] = {}


async def _flush_series(key: tuple):
    """Дожидается конца серии и отвечает одним пересказом."""
    try:
        while True:
            await asyncio.sleep(SERIES_WINDOW)
            series = SERIES.get(key)
            if not series:
                return
            # пришло новое видео, пока ждали — продлеваем ожидание
            if series['pending']:
                series['pending'] = False
                continue
            break
        series = SERIES.pop(key, None)
        if not series or not series['parts'] or not series['is_issue']:
            return
        summary = await short_summary(series['parts'], series['address'])
        link = (NewMessageLink(type=MessageLinkType.REPLY, mid=series['first_mid'])
                if series['first_mid'] else None)
        await series['bot'].send_message(chat_id=series['chat_id'], text=summary, link=link)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception('Не удалось отправить пересказ серии')
        SERIES.pop(key, None)


def queue_series(key, text, house, is_issue, bot, chat_id, mid):
    """Копит расшифровки серии; ответ уйдёт, когда поток видео стихнет."""
    series = SERIES.get(key)
    if series is None:
        series = SERIES[key] = {
            'parts': [], 'address': None, 'house_id': None, 'is_issue': False,
            'pending': False, 'bot': bot, 'chat_id': chat_id,
            'first_mid': mid, 'task': None,
        }
        series['task'] = asyncio.create_task(_flush_series(key))
    else:
        series['pending'] = True          # продлить ожидание: серия продолжается
    series['parts'].append(text)
    series['is_issue'] = series['is_issue'] or is_issue
    if house and not series['address']:
        series['address'] = house['address']
        series['house_id'] = house['id']


async def transcribe_later(record_id: int, url: str, bot=None, chat_id=None, mid=None):
    """Расшифровывает голосовое/видео в фоне и дописывает текст к сообщению.

    Ответ в чат — только на аварийное и только один на серию роликов.
    """
    try:
        text = await transcribe.transcribe_url(url)
        if not text:
            return
        record = db.get_chat_record(record_id)
        key = (chat_id, record['user_id'] if record else None)
        house = houses.detect_house(text)
        # Продолжения серии («перекрыли стояк», «всё готово») адреса не содержат —
        # цепляем их к дому, который назвали в начале отчёта.
        if not house:
            series = SERIES.get(key)
            if series and series.get('house_id') is not None:
                house = houses.HOUSES_BY_ID.get(series['house_id'])
        is_issue = bool(ISSUE_WORDS.search(text))
        db.set_chat_transcript(record_id, text,
                               house_id=house['id'] if house else None,
                               is_issue=is_issue)
        log.info('Расшифровано сообщение %s: %.80s', record_id, text)

        if bot and chat_id:
            queue_series(key, text, house, is_issue, bot, chat_id, mid)
    except Exception:
        log.exception('Не удалось расшифровать вложение')


def record_chat_message(event, text: str):
    """Тихо сохраняет сообщение рабочего чата и цепляет его к дому."""
    try:
        body = event.message.body
        files = bool(getattr(body, 'attachments', None))
        if not text and not files:
            return
        house = houses.detect_house(text) if text else None
        record_id = db.add_chat_record(
            chat_id=getattr(event.message.recipient, 'chat_id', 0),
            mid=getattr(body, 'mid', None),
            user_id=_uid(event),
            user_name=_uname(event),
            text=text,
            house_id=house['id'] if house else None,
            has_files=files,
            is_issue=bool(text and ISSUE_WORDS.search(text)),
        )
        # Голосовые и видеоотчёты расшифровываем фоном, чтобы не тормозить чат
        url = speech_url(body)
        if url:
            asyncio.create_task(transcribe_later(
                record_id, url, bot=getattr(event, 'bot', None),
                chat_id=getattr(event.message.recipient, 'chat_id', None),
                mid=getattr(body, 'mid', None)))
    except Exception:
        log.exception('Не удалось записать сообщение чата')


def mentioned_in_markup(event) -> bool:
    """Упомянули ли бота через @ — MAX передаёт это разметкой, а не текстом."""
    me = BOT_ME.get('user_id')
    if not me:
        return False
    for el in (getattr(event.message.body, 'markup', None) or []):
        el_type = getattr(getattr(el, 'type', None), 'value', getattr(el, 'type', None))
        if el_type == 'user_mention' and getattr(el, 'user_id', None) == me:
            return True
    return False


def strip_address(text: str) -> tuple[bool, str]:
    """Позвали ли Люсю и что осталось от вопроса без обращения."""
    if not text:
        return False, ''
    m = ADDRESS_RE.match(text)
    if m:
        return True, text[m.end():].strip()
    # обращение в конце: «что по нормативам ГВС, Люся?»
    m = re.search(r'[\s,]@?(люс[яеию]|lusya|lyusya)\s*[?!.]*\s*$', text, re.IGNORECASE)
    if m:
        return True, text[:m.start()].strip(' ,')
    if BOT_ME.get('username') and f"@{BOT_ME['username']}".lower() in text.lower():
        return True, re.sub(f"@{BOT_ME['username']}", '', text, flags=re.IGNORECASE).strip()
    return False, text


def _safe_filename(name: str) -> str:
    name = re.sub(r'[^\w.\-() ]', '_', name)
    return name[:80] or 'file'


async def _download(url: str) -> bytes:
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()


async def _save_equipment_photo(event, state) -> bool:
    """Сохраняет фото прибора или его паспорта. True, если что-то сохранили."""
    atts = event.message.body.attachments or []
    dev_id, slot = state['device_id'], state['slot']
    folder = os.path.join(DOCS_DIR, 'equipment')
    os.makedirs(folder, exist_ok=True)
    for a in atts:
        url = getattr(a.payload, 'url', None) if a.payload else None
        if not url:
            continue
        try:
            data = await _download(url)
        except Exception:
            log.exception('Не удалось скачать фото прибора')
            return False
        path = os.path.join(folder, f'{dev_id}_{slot}.jpg')
        with open(path, 'wb') as f:
            f.write(data)
        db.update_device(dev_id, **{f'photo_{slot}': path})
        return True
    return False


async def _save_docs(event, state) -> int:
    """Сохраняет вложения сообщения как документы дома. Возвращает число сохранённых."""
    atts = event.message.body.attachments or []
    house_id = state['house_id']
    note = (event.message.body.text or '').strip() or None
    os.makedirs(os.path.join(DOCS_DIR, str(house_id)), exist_ok=True)
    saved = 0
    for a in atts:
        url = getattr(a.payload, 'url', None) if a.payload else None
        if not url:
            continue
        a_type = getattr(a.type, 'value', str(a.type))
        if a_type == 'file' and getattr(a, 'filename', None):
            filename = _safe_filename(a.filename)
        elif a_type == 'image':
            filename = 'foto.jpg'
        else:
            base = os.path.basename(url.split('?')[0]) or 'file'
            filename = _safe_filename(base)
        data = await _download(url)
        doc_id = db.add_doc(house_id, filename, '', note, _uname(event))
        filename = f'{doc_id}_{filename}'
        path = os.path.join(DOCS_DIR, str(house_id), filename)
        with open(path, 'wb') as f:
            f.write(data)
        db.set_doc_file(doc_id, filename, path)
        saved += 1
    return saved


@dp.message_created()
async def on_text(event: MessageCreated):
    text = (event.message.body.text or '').strip()
    uid = _uid(event)
    group = is_group(event)
    bot_status.note_update('чат' if group else 'личка')
    if not group:
        log.info('Личка от %s: %.60s', uid, text or '<без текста>')

    # В группах Люся молчит, пока её не позвали по имени: реагировать на каждое
    # сообщение рабочего чата — верный способ, чтобы бота оттуда выгнали.
    if group:
        log.info('Сообщение из чата %s: %.60s',
                 getattr(event.message.recipient, 'chat_id', '?'), text)
        record_chat_message(event, text)
        # Показания, присланные в чат, попадают в учёт наравне с личкой:
        # сантехник пишет туда, где удобно
        if await handle_readings(event, text, uid):
            return
        addressed, text = strip_address(text)
        if not addressed and not mentioned_in_markup(event):
            return
        db.upsert_user(uid, _uname(event))
        try:
            reply = await agent.answer(uid, _uname(event), text) if text else None
        except agent.TooSlow:
            await send(event.message, SLOW_REPLY)
            return
        await send(event.message,
                   reply or f'{BOT_NAME} на связи 🙂 Спроси что-нибудь по домам, '
                            'заявкам или нормативам.')
        return

    db.upsert_user(uid, _uname(event))

    # Голосом проще сказать «забудь», чем набрать команду
    if re.fullmatch(r'забудь(\s+(вс[её]|наш\w*|переписку))?[.!]?', text.lower().strip()):
        n = db.forget_user(uid)
        await send(event.message,
                   f'🧹 Забыла нашу переписку ({n} сообщ.). '
                   'Данные по домам и приборам не тронуты.', main_menu_kb())
        return

    state = STATE.get(uid)

    if state and state['mode'] == 'doc_wait':
        h = houses.HOUSES_BY_ID[state['house_id']]
        try:
            saved = await _save_docs(event, state)
        except Exception:
            log.exception('Не удалось сохранить документ')
            await send(event.message, '⚠️ Не получилось сохранить файл, попробуйте ещё раз.')
            return
        if saved:
            STATE.pop(uid, None)
            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text='📎 Добавить ещё', payload=f"da:{h['id']}"),
                   CallbackButton(text='📁 Документы дома', payload=f"dl:{h['id']}"))
            await send(event.message,
                       f"✅ Сохранила ({saved} шт.) в документы дома {h['address']}.", kb)
        else:
            await send(event.message,
                       '📎 Пришлите файл, фото или скан одним сообщением '
                       '(текст в подписи сохраню как примечание).')
        return

    # Фото прибора/паспорта: приходит вложением, часто вообще без подписи
    if state and state['mode'] == 'eq_photo':
        dev = db.get_device(state['device_id'])
        p = db.get_point(dev['point_id']) if dev else None
        slot = state['slot']
        has_files = bool(event.message.body.attachments or [])
        if has_files:
            if not await _save_equipment_photo(event, state):
                await send(event.message, '⚠️ Не получилось сохранить фото, пришлите ещё раз.')
                return
        elif text not in ('-', '—'):
            await send(event.message, '📷 Пришлите фото или напишите «-», чтобы пропустить.')
            return
        if slot == 'device':
            STATE[uid] = {'mode': 'eq_photo', 'device_id': state['device_id'], 'slot': 'passport'}
            await send(event.message, '📄 Теперь фото паспорта манометра '
                                      '(там номер, класс точности, диапазон), или «-»:')
            return
        STATE.pop(uid, None)
        await send(event.message, '✅ Готово, манометр записан.\n\n' + point_card_text(p),
                   point_card_kb(p))
        return

    if not text:
        log.info('Молчу: сообщение без текста (вложение или стикер)')
        return
    if text.startswith('/'):
        # Известные команды разобраны фильтрами выше — сюда падают только чужие.
        # Молчать на них можно, но в логе это должно быть видно: иначе «бот не
        # отвечает на /version» неотличимо от «бот вообще не получил сообщение».
        log.info('Молчу: команда %s не распознана', text.split()[0][:30])
        return

    if state and state['mode'] == 'req_addr':
        # Шаг 1 новой заявки: адрес свободным текстом
        found = houses.search(text, limit=1)
        h = found[0] if found else None
        STATE[uid] = {'mode': 'req_descr',
                      'house_id': h['id'] if h else None,
                      'address': h['address'] if h else text}
        addr = h['address'] if h else text
        note = '' if h else '\n⚠️ Такого адреса у меня в базе нет — запишу как есть.'
        await send(event.message, f'🏠 Адрес: {addr}{note}\n\n📝 Теперь опишите проблему одним сообщением:')
        return

    if state and state['mode'] == 'req_descr':
        req_id = db.add_request(state['house_id'], state['address'], text, uid, _uname(event))
        STATE.pop(uid, None)
        r = db.get_request(req_id)
        await send(event.message, '✅ Записала заявку!\n\n' + request_card_text(r), request_card_kb(r))
        return

    if state and state['mode'] == 'pass_edit':
        h = houses.HOUSES_BY_ID[state['house_id']]
        db.set_passport_field(h['id'], state['field'], text, _uname(event))
        STATE.pop(uid, None)
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='✏️ Редактировать ещё', payload=f"pe:{h['id']}"),
               CallbackButton(text='🗂 Открыть паспорт', payload=f"p:{h['id']}"))
        await send(event.message,
                   f"✅ Записала: {PASSPORT_LABELS[state['field']]} — {h['address']}", kb)
        return

    if state and state['mode'] == 'work_title':
        STATE[uid] = {'mode': 'work_deadline', 'house_id': state['house_id'], 'title': text}
        await send(event.message,
                   '⏳ К какому сроку? Напишите дату:\n'
                   '• «25.09» или «25.09.2026»\n'
                   '• «-» — если без срока')
        return

    if state and state['mode'] == 'work_deadline':
        try:
            deadline = parse_deadline(text)
        except ValueError:
            await send(event.message, '🤔 Не поняла дату. Напишите, например, «25.09.2026» или «-» без срока.')
            return
        work_id = db.add_work(state['house_id'], state['title'], deadline, _uname(event))
        STATE.pop(uid, None)
        w = db.get_work(work_id)
        await send(event.message,
                   '✅ Записала работу! Назначьте ответственного кнопкой:\n\n' + work_card_text(w),
                   work_card_kb(w))
        return

    if state and state['mode'] == 'work_dl_edit':
        try:
            deadline = parse_deadline(text)
        except ValueError:
            await send(event.message, '🤔 Не поняла дату. Напишите, например, «25.09.2026» или «-» без срока.')
            return
        db.update_work(state['work_id'], deadline=deadline)
        STATE.pop(uid, None)
        w = db.get_work(state['work_id'])
        await send(event.message, work_card_text(w), work_card_kb(w))
        return

    if state and state['mode'] == 'work_note':
        db.update_work(state['work_id'], details=text)
        STATE.pop(uid, None)
        w = db.get_work(state['work_id'])
        await send(event.message, work_card_text(w), work_card_kb(w))
        return

    if state and state['mode'] == 'work_report':
        if text not in ('-', '—'):
            db.update_work(state['work_id'], report=text)
        STATE.pop(uid, None)
        w = db.get_work(state['work_id'])
        h = houses.HOUSES_BY_ID.get(w['house_id'])
        await send(event.message,
                   f"✅ Спасибо! Отчёт записан в историю дома {h['address'] if h else ''}."
                   if text not in ('-', '—') else '✅ Хорошо, без отчёта.',
                   main_menu_kb())
        return

    # --- Манометры: место → номер → поверка → фото прибора → фото паспорта ---
    if state and state['mode'] == 'eq_place':
        point_id = db.add_point(state['house_id'], text, state.get('tp', ''), _uname(event))
        STATE[uid] = {'mode': 'eq_serial', 'point_id': point_id}
        await send(event.message, f'✅ Точка добавлена: {text}.\n\n'
                                  '📟 Теперь заводской номер манометра (или «-»):')
        return

    if state and state['mode'] == 'eq_serial':
        serial = None if text in ('-', '—') else text
        STATE[uid] = {'mode': 'eq_verify', 'point_id': state['point_id'], 'serial': serial}
        await send(event.message,
                   '📅 Поверка. Напишите клеймо с прибора — «июль 2026» или '
                   f'«07.2026»: сама прибавлю {VERIFY_YEARS} года.\n'
                   'Если знаете сразу срок годности — «до 07.2028».\n'
                   '«-», если неизвестна.')
        return

    if state and state['mode'] == 'eq_verify':
        try:
            verified, kak = parse_verify(text)
        except ValueError:
            await send(event.message,
                       '🤔 Не поняла. Клеймо — «июль 2026», срок — «до 07.2028», '
                       'или «-».')
            return
        dev_id = db.add_device(state['point_id'], state['serial'], verified, uid, _uname(event))
        STATE[uid] = {'mode': 'eq_photo', 'device_id': dev_id, 'slot': 'device'}
        p = db.get_point(state['point_id'])
        srok = f'поверка до {fmt_date(verified)}' if verified else 'поверка не указана'
        await send(event.message,
                   f"✅ Записала манометр № {state['serial'] or '—'} "
                   f"({p['place']}), {srok}"
                   + (f' ({kak})' if kak else '') + '.\n\n'
                   '📷 Пришлите фото манометра, или «-», чтобы пропустить.')
        return

    if state and state['mode'] == 'meter_label':
        m_id = db.add_meter(state['house_id'], state['kind'], text, _uname(event))
        STATE.pop(uid, None)
        h = houses.HOUSES_BY_ID[state['house_id']]
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='✍️ Внести первое показание', payload=f'mtr:{m_id}'),
               CallbackButton(text='➕ Ещё счётчик', payload=f"mta:{h['id']}"))
        kb.row(CallbackButton(text='🧮 Счётчики дома', payload=f"mt:{h['id']}"))
        await send(event.message,
                   f"✅ Запомнила: {METER_KINDS[state['kind']]} — «{text}» ({h['address']}).", kb)
        return

    if state and state['mode'] == 'meter_value':
        try:
            value = float(text.replace(',', '.').replace(' ', ''))
        except ValueError:
            await send(event.message, '🤔 Нужно число, например «1234,56». Попробуйте ещё раз:')
            return
        m = db.get_meter(state['meter_id'])
        h = houses.HOUSES_BY_ID.get(m['house_id'])
        delta, warning = check_anomaly(m['id'], value)
        db.add_reading(m['id'], value, current_period(), uid, _uname(event))
        STATE.pop(uid, None)
        lines = [f"✅ Записала: {h['address'] if h else ''} — {m['label']}: "
                 f'{fmt_value(value)} ({fmt_period(current_period())}).']
        if delta is not None and delta >= 0:
            lines.append(f'Расход за период: {fmt_value(delta)}')
        if warning:
            lines.append(f'⚠️ Внимание: {warning}')
            # предупреждаем инженера и руководителя
            for u in db.list_users():
                if u['role'] in ('engineer', 'admin', 'director') and u['user_id'] != uid:
                    await notify(event.bot, u['user_id'],
                                 f"⚠️ Счётчики, {h['address'] if h else ''} — {m['label']}: {warning} "
                                 f'(подал {_uname(event)})')
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='📈 История счётчика', payload=f"mth:{m['id']}"),
               CallbackButton(text='🧮 Счётчики дома', payload=f"mt:{m['house_id']}"))
        await send(event.message, '\n'.join(lines), kb)
        return

    if state and state['mode'] == 'camp_title':
        STATE[uid] = {'mode': 'camp_deadline', 'complex_id': state['complex_id'], 'title': text}
        await send(event.message, '⏳ К какому сроку сдать по всем домам? '
                                  'Дата («15.09» / «15.09.2026») или «-» — без срока.')
        return

    if state and state['mode'] == 'camp_deadline':
        try:
            deadline = parse_deadline(text)
        except ValueError:
            await send(event.message, '🤔 Не поняла дату. Напишите, например, «15.09.2026» или «-».')
            return
        cid = state['complex_id']
        assigned = db.all_house_complexes()
        house_ids = [h['id'] for h in houses.HOUSES if assigned.get(h['id']) == cid]
        camp_id = db.add_campaign(state['title'], cid, deadline, uid, _uname(event))
        for hid in house_ids:
            db.add_work(hid, state['title'], deadline, _uname(event), user_id=uid, campaign_id=camp_id)
        STATE.pop(uid, None)
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='📊 Открыть задание', payload=f'campv:{camp_id}'),
               CallbackButton(text='🏠 Меню', payload='menu'))
        # оповещаем всех с ролью — пусть разбирают работы
        for u in assignable_users():
            if u['user_id'] != uid:
                await notify(event.bot, u['user_id'],
                             f"📢 Новое задание по {COMPLEX_NAMES.get(cid, '')}: "
                             f"«{state['title']}», срок — {fmt_deadline(deadline)}, "
                             f'домов: {len(house_ids)}. Смотрите «📅 Все работы».')
        await send(event.message,
                   f"✅ Задание создано! «{state['title']}» — {COMPLEX_NAMES.get(cid, '')}, "
                   f'работ: {len(house_ids)}, срок: {fmt_deadline(deadline)}.\n'
                   'Я оповестила команду и буду следить за прогрессом.', kb)
        return

    if await handle_readings(event, text, uid):
        return

    # Запрос вида «Седова 65а/2 кв 47» — где квартира, какой стояк
    addr_q, flat_q = risers_mod.parse_query(text)
    if addr_q and flat_q:
        found = risers_mod.locate(addr_q, flat_q)
        if found:
            block, addr, floor, riser, on_floor = found
            await send(event.message, riser_card_text(block, addr, flat_q, floor, riser, on_floor),
                       riser_card_kb(block, addr))
            return
        blocks = risers_mod.find_blocks(addr_q)
        if blocks:
            b, addr = blocks[0]
            allf = [f for fl in b['floors'].values() for f in fl]
            await send(event.message,
                       f'🤔 По адресу {addr} есть таблица стояков, но квартиры {flat_q} в ней нет.\n'
                       f'Здесь квартиры с {min(allf)} по {max(allf)}.')
            return

    # Режим по умолчанию — поиск дома по адресу
    found = houses.search(text)
    if not found:
        try:
            ai_answer = await agent.answer(uid, _uname(event), text)
        except agent.TooSlow:
            await send(event.message, SLOW_REPLY)
            return
        if ai_answer:
            await send(event.message, ai_answer)
        else:
            await send(event.message,
                       f'🤷‍♀️ По запросу «{text}» я ничего не нашла.\n'
                       f'Попробуйте написать иначе, например: «{_primer(0)}» или «{_primer(1)}».',
                       main_menu_kb())
    elif len(found) == 1:
        h = found[0]
        await send(event.message, house_card_text(h), house_card_kb(h))
    else:
        kb = InlineKeyboardBuilder()
        for h in found:
            kb.row(CallbackButton(text=h['address'], payload=f"h:{h['id']}"))
        await send(event.message, f'🔍 Нашла несколько домов по «{text}» — выберите:', kb)


# ---------- Кнопки ----------

@dp.message_callback()
async def on_callback(event: MessageCallback):
    payload = event.callback.payload or ''
    uid = event.callback.user.user_id
    bot_status.note_update('кнопка')
    log.info('Нажата кнопка: %s (пользователь %s)', payload, uid)

    # Подтверждаем нажатие: без ответа на callback кнопка в MAX «зависает»
    try:
        await event.bot.send_callback(callback_id=event.callback.callback_id)
    except Exception:
        log.warning('Не удалось подтвердить нажатие кнопки', exc_info=True)

    db.upsert_user(uid, getattr(event.callback.user, 'full_name', None) or '')
    msg = event.message
    parts = payload.split(':')
    action = parts[0]

    if action == 'menu':
        STATE.pop(uid, None)
        await send(msg, MAIN_TEXT, main_menu_kb())

    elif action == 'srch':
        STATE.pop(uid, None)
        await send(msg, '🔍 Напишите адрес (улица и номер дома), например:\n'
                        f'«{_primer(0)}» или «{_primer(1)}»')

    elif action == 'homes':
        assigned = db.all_house_complexes()
        counts = {}
        for cid in assigned.values():
            counts[cid] = counts.get(cid, 0) + 1
        n_unassigned = len(houses.HOUSES) - len(assigned)
        kb = InlineKeyboardBuilder()
        for c in COMPLEXES:
            kb.row(CallbackButton(text=f"{c['name']} ({counts.get(c['id'], 0)})",
                                  payload=f"cxl:{c['id']}"))
        if n_unassigned:
            kb.row(CallbackButton(text=f'📍 Без привязки к ЖК ({n_unassigned})', payload='cxl:none'))
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, f'🏘 Наши дома — всего {len(houses.HOUSES)}.\nВыберите ЖК:', kb)

    elif action == 'cxl':
        cid = parts[1]
        assigned = db.all_house_complexes()
        if cid == 'none':
            hs = [h for h in houses.HOUSES if h['id'] not in assigned]
            title = '📍 Дома без привязки к ЖК'
        else:
            hs = [h for h in houses.HOUSES if assigned.get(h['id']) == cid]
            title = f"🏙 {COMPLEX_NAMES.get(cid, cid)}"
        lines = [f'{title} — {len(hs)} домов:', '']
        lines += [f"• {h['address']}" for h in hs]
        lines.append('')
        lines.append('💡 Напишите адрес, чтобы открыть карточку дома. '
                     'Привязать дом к ЖК можно кнопкой «Указать ЖК» в карточке.')
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='◀️ К списку ЖК', payload='homes'),
               CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '\n'.join(lines), kb)

    elif action == 'cxs':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            kb = InlineKeyboardBuilder()
            for c in COMPLEXES:
                kb.row(CallbackButton(text=c['name'], payload=f"cxset:{h['id']}:{c['id']}"))
            kb.row(CallbackButton(text='◀️ Назад', payload=f"h:{h['id']}"))
            await send(msg, f"🏙 {h['address']}: к какому ЖК относится дом?", kb)

    elif action == 'cxset':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        cid = parts[2]
        if h and cid in COMPLEX_NAMES:
            db.set_house_complex(h['id'], cid)
            await send(msg, house_card_text(h), house_card_kb(h))

    elif action == 'dl':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            docs = db.list_docs(h['id'])
            project = catalog_for_house(h['address'])
            kb = InlineKeyboardBuilder()
            if project:
                kb.row(CallbackButton(text=f'📐 Проектная документация ({len(project)})',
                                      payload=f"pd:{h['id']}"))
            kb.row(CallbackButton(text='📎 Добавить документ', payload=f"da:{h['id']}"),
                   CallbackButton(text='🏠 К дому', payload=f"h:{h['id']}"))
            if not docs:
                extra = (f'\n📐 Зато есть проектная документация — {len(project)} документов, '
                         'кнопка ниже.' if project else '')
                await send(msg, f"📁 Своих файлов по дому {h['address']} пока нет.\n"
                                'Нажмите «Добавить документ» и пришлите фото/скан/файл.' + extra, kb)
            else:
                await send(msg, f"📁 Документы дома {h['address']} — {len(docs)} шт., отправляю:")
                for d in docs[-15:]:
                    caption = d['filename']
                    if d['note']:
                        caption += f" — {d['note']}"
                    caption += f" (загрузил: {d['uploaded_by'] or '—'}, {d['uploaded_at']})"
                    try:
                        await msg.answer(text=caption, attachments=[InputMedia(d['path'])])
                    except Exception:
                        log.exception('Не удалось отправить документ %s', d['path'])
                        await msg.answer(text=f'⚠️ Не удалось отправить: {caption}')
                await send(msg, 'Готово!', kb)

    elif action == 'pd':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            project = catalog_for_house(h['address'])
            if not project:
                await send(msg, f"📐 Проектной документации по дому {h['address']} в каталоге нет.")
                return
            by_section = {}
            for d in project:
                by_section.setdefault(d['section'], []).append(d)
            lines = [f"📐 Проектная документация — {h['address']}", '']
            kb = InlineKeyboardBuilder()
            n_local = 0
            for section, items in by_section.items():
                lines.append(f'▪️ {section}:')
                for d in items:
                    idx = project_docs.CATALOG.index(d)
                    mark = ''
                    if project_docs.local_path(d):
                        n_local += 1
                        mark = ' 📥'
                        kb.row(CallbackButton(text=d['title'][:60], payload=f'pdf:{idx}'))
                    else:
                        kb.row(LinkButton(text=d['title'][:60], url=d['url']))
                    lines.append(f"   • {d['title']}{mark}")
                    if d.get('note'):
                        lines.append(f"     ⚠️ {d['note']}")
                lines.append('')
            lines.append('📥 — файл лежит у Люси, придёт прямо в чат.'
                         if n_local else
                         '💡 Документы открываются с Google Диска — нужен доступ к папке УК.')
            kb.row(CallbackButton(text='📁 Файлы дома', payload=f"dl:{h['id']}"),
                   CallbackButton(text='🏠 К дому', payload=f"h:{h['id']}"))
            await send(msg, '\n'.join(lines), kb)

    elif action == 'pdf':
        doc = project_docs.CATALOG[int(parts[1])]
        path = project_docs.local_path(doc)
        if path:
            try:
                await msg.answer(text=doc['title'], attachments=[InputMedia(path)])
            except Exception:
                log.exception('Не удалось отправить проектный документ %s', path)
                kb = InlineKeyboardBuilder()
                kb.row(LinkButton(text='Открыть на Диске', url=doc['url']))
                await send(msg, f"⚠️ Не получилось отправить «{doc['title']}» файлом.", kb)
        else:
            kb = InlineKeyboardBuilder()
            kb.row(LinkButton(text='Открыть на Диске', url=doc['url']))
            await send(msg, f"📎 {doc['title']} — файл ещё не загружен к Люсе.", kb)

    elif action == 'pdsync':
        if _role(uid) not in ('admin', 'engineer'):
            await send(msg, '📥 Загружать документацию может админ или инженер.')
            return
        total = len(project_docs.CATALOG)
        await send(msg, f'📥 Забираю документацию с Диска — {total} документов. '
                        'Это займёт минуту, пришлю отчёт.')

        async def progress(done, all_n):
            log.info('Загрузка документации: %s/%s', done, all_n)

        ok, total, report = await project_docs.download_all(progress)
        head = f'📥 Загружено {ok} из {total}.'
        if ok < total:
            head += ('\nЧто не получилось — ниже. Чаще всего причина в том, '
                     'что на Диске закрыт доступ по ссылке.')
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, head + '\n\n' + '\n'.join(report), kb)

    elif action == 'da':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            STATE[uid] = {'mode': 'doc_wait', 'house_id': h['id']}
            await send(msg, f"📎 Пришлите фото, скан или файл для дома {h['address']} "
                            '(можно несколько и с подписью — подпись сохраню как примечание).')

    elif action == 'h':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            await send(msg, house_card_text(h), house_card_kb(h))

    elif action == 'p':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text='✏️ Редактировать', payload=f"pe:{h['id']}"),
                   CallbackButton(text='🏠 К дому', payload=f"h:{h['id']}"))
            await send(msg, passport_text(h), kb)

    elif action == 'pe':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            kb = InlineKeyboardBuilder()
            for key, label in PASSPORT_FIELDS:
                kb.row(CallbackButton(text=label[:40], payload=f"pf:{h['id']}:{key}"))
            kb.row(CallbackButton(text='◀️ Назад', payload=f"p:{h['id']}"))
            await send(msg, f"✏️ {h['address']}: какое поле заполнить?", kb)

    elif action == 'pf':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        field = parts[2]
        if h and field in PASSPORT_LABELS:
            STATE[uid] = {'mode': 'pass_edit', 'house_id': h['id'], 'field': field}
            current = db.get_passport(h['id']).get(field)
            cur_line = f'\nСейчас: {current}\n' if current else ''
            await send(msg, f"✏️ {h['address']} — {PASSPORT_LABELS[field]}{cur_line}\n"
                            'Отправьте новое значение одним сообщением:')

    elif action == 'nr':
        STATE[uid] = {'mode': 'req_addr'}
        await send(msg, '➕ Новая заявка.\n🏠 Напишите адрес дома:')

    elif action == 'nrh':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            STATE[uid] = {'mode': 'req_descr', 'house_id': h['id'], 'address': h['address']}
            await send(msg, f"➕ Заявка: {h['address']}\n\n📝 Опишите проблему одним сообщением:")

    elif action == 'rl':
        done = len(parts) > 1 and parts[1] == 'done'
        if done:
            rows = db.list_requests(statuses=(db.STATUS_DONE,))
            title = '✅ Выполненные заявки (последние 30):'
        else:
            rows = db.list_requests()
            title = '📋 Открытые заявки (новые и в работе):'
        if not rows:
            body = title + '\n\nПока пусто — отдыхаем, мальчики! ☕'
        else:
            body = title + '\n\n' + '\n'.join(
                f"№{r['id']} {db.STATUS_LABELS[r['status']].split()[0]} {r['address']} — "
                f"{r['description'][:60]}" for r in rows)
        kb = InlineKeyboardBuilder()
        for r in rows[:10]:
            kb.row(CallbackButton(text=f"№{r['id']} · {r['address'][:30]}", payload=f"r:{r['id']}"))
        kb.row(CallbackButton(text='✅ Выполненные' if not done else '📋 Открытые',
                              payload='rl:done' if not done else 'rl'),
               CallbackButton(text='➕ Новая', payload='nr'))
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, body, kb)

    elif action == 'r':
        r = db.get_request(int(parts[1]))
        if r:
            await send(msg, request_card_text(r), request_card_kb(r))

    elif action == 'rs':
        req_id, status = int(parts[1]), parts[2]
        if status in (db.STATUS_WORK, db.STATUS_DONE):
            db.set_request_status(req_id, status)
            r = db.get_request(req_id)
            if r:
                await send(msg, request_card_text(r), request_card_kb(r))

    elif action in ('wl', 'wlh'):
        if action == 'wlh':
            h = houses.HOUSES_BY_ID.get(int(parts[1]))
            works = db.list_works(house_id=h['id'], open_only=False)
            title = f"📅 Работы по дому {h['address']}:"
            add_payload = f"nw:{h['id']}"
        else:
            works = db.list_works(open_only=True)
            title = '📅 Открытые работы по всем домам (по срокам):'
            add_payload = None
        if not works:
            body = title + '\n\nПока пусто.'
        else:
            body = title + '\n\n' + '\n'.join(work_line(w) for w in works)
        kb = InlineKeyboardBuilder()
        for w in works[:10]:
            kb.row(CallbackButton(text=f"№{w['id']} · {w['title'][:35]}", payload=f"w:{w['id']}"))
        if add_payload:
            kb.row(CallbackButton(text='➕ Новая работа', payload=add_payload))
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, body, kb)

    elif action == 'nw':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            STATE[uid] = {'mode': 'work_title', 'house_id': h['id']}
            await send(msg, f"➕ Новая работа по дому {h['address']}.\n"
                            '🔧 Что нужно сделать? Например: «Опрессовка системы отопления», '
                            '«Сдача теплового узла», «Дефектовка розлива ХВС», «Закупить задвижки ДУ50 — 2 шт».')

    elif action == 'w':
        w = db.get_work(int(parts[1]))
        if w:
            await send(msg, work_card_text(w), work_card_kb(w))

    elif action == 'ws':
        work_id, status = int(parts[1]), parts[2]
        if status in (db.WORK_IN_PROGRESS, db.WORK_DONE):
            if status == db.WORK_DONE:
                from datetime import datetime
                db.update_work(work_id, status=status,
                               done_at=datetime.now(db.IRKUTSK_TZ).date().isoformat())
                STATE[uid] = {'mode': 'work_report', 'work_id': work_id}
            else:
                db.update_work(work_id, status=status)
            w = db.get_work(work_id)
            if w:
                await send(msg, work_card_text(w), work_card_kb(w))
                if status == db.WORK_DONE:
                    h = houses.HOUSES_BY_ID.get(w['house_id'])
                    addr = h['address'] if h else '?'
                    actor = db.get_user(uid)
                    actor_name = actor['name'] if actor else ''
                    # сообщаем постановщику работы
                    if w['created_by'] and w['created_by'] != uid:
                        await notify(event.bot, w['created_by'],
                                     f"✅ Работа №{w['id']} сдана: {addr} — {w['title']} ({actor_name})")
                    # если работа из задания по ЖК — отслеживаем общий прогресс
                    if w['campaign_id']:
                        camp = db.get_campaign(w['campaign_id'])
                        if camp:
                            done, total = db.campaign_progress(camp['id'])
                            if camp['created_by'] and camp['created_by'] != uid:
                                extra = ' 🎉 Задание выполнено полностью!' if done == total else ''
                                await notify(event.bot, camp['created_by'],
                                             f"📊 «{camp['title']}»: сдано {done} из {total}. "
                                             f'{addr} — готово ({actor_name}).{extra}')
                    await send(msg, '📝 Напишите пару слов, что сделано (отчёт попадёт '
                                    'в историю дома), или «-», если без отчёта.')

    elif action == 'wa':
        work_id, assignee_id = int(parts[1]), int(parts[2])
        u = db.get_user(assignee_id)
        if u:
            db.update_work(work_id, assignee=u['name'], assignee_id=assignee_id)
            w = db.get_work(work_id)
            if w:
                await send(msg, work_card_text(w), work_card_kb(w))
                if assignee_id != uid:
                    h = houses.HOUSES_BY_ID.get(w['house_id'])
                    await notify(event.bot, assignee_id,
                                 f"📬 Вам назначена работа №{w['id']}:\n"
                                 f"🏠 {h['address'] if h else '?'}\n"
                                 f"🔧 {w['title']}\n"
                                 f"⏳ Срок: {fmt_deadline(w['deadline'])}\n\n"
                                 'Открыть: меню → 🧰 Мои работы')

    elif action == 'wat':
        # запасной вариант: назначение из списка звена (пока человек не написал боту)
        work_id, member_id = int(parts[1]), int(parts[2])
        m = TEAM_BY_ID.get(member_id)
        if m:
            db.update_work(work_id, assignee=m['name'], assignee_id=None)
            w = db.get_work(work_id)
            if w:
                await send(msg, work_card_text(w), work_card_kb(w))

    elif action == 'hist':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            done_works = db.list_done_works(house_id=h['id'])
            lines = [f"📜 История работ — {h['address']}:", '']
            if not done_works:
                lines.append('Пока пусто: сданных работ по дому нет.')
            for w in done_works:
                when = ''
                if w['done_at']:
                    y, mo, d = w['done_at'].split('-')
                    when = f'{d}.{mo}.{y} — '
                line = f"✅ {when}{w['title']} ({w['assignee'] or '—'})"
                if w['report']:
                    line += f"\n   📝 {w['report']}"
                lines.append(line)
            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text='🏠 К дому', payload=f"h:{h['id']}"),
                   CallbackButton(text='🏠 Меню', payload='menu'))
            await send(msg, '\n'.join(lines), kb)

    elif action == 'aibrief':
        if _role(uid) not in BRIEFING_ROLES:
            await send(msg, '📊 Брифинг доступен руководству. Ваши задачи — в «🧰 Мои работы».')
            return
        if not ai.enabled():
            await send(msg, '🧠 ИИ пока не подключён: задайте переменную окружения '
                            'OPENROUTER_API_KEY при запуске бота.')
            return
        data = _brief_data_text()
        await send(msg, '🧠 Секунду, пишу сводку...')
        answer = await ai.ask(
            'Вот сводные данные по хозяйству на сегодня:\n\n' + data +
            '\n\nНапиши короткий утренний доклад для руководителя (5–8 предложений): '
            'что происходит, что сдано, где риски (просрочки, подозрения на утечки), '
            'на что обратить внимание. Без списков — живым связным текстом.')
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='📊 Полный брифинг', payload='brief'),
               CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, answer or '⚠️ Не получилось связаться с ИИ, попробуйте позже '
                                  'или откройте обычный брифинг.', kb)

    elif action == 'brief':
        if _role(uid) not in BRIEFING_ROLES:
            await send(msg, '📊 Брифинг доступен руководству: директору, инженеру, мастерам. '
                            'Ваши задачи — в «🧰 Мои работы».')
            return
        lines = _brief_lines()
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='🧠 Сводка от Люси (ИИ)', payload='aibrief'))
        kb.row(CallbackButton(text='📅 Все работы', payload='wl'),
               CallbackButton(text='📋 Заявки', payload='rl'))
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '\n'.join(lines), kb)

    elif action == 'myw':
        works = db.list_my_works(uid)
        if not works:
            body = '🧰 На вас пока нет открытых работ. Отдыхайте, пока можно! ☕'
        else:
            body = '🧰 Ваши работы (по срокам):\n\n' + '\n'.join(work_line(w) for w in works)
        kb = InlineKeyboardBuilder()
        for w in works[:10]:
            kb.row(CallbackButton(text=f"№{w['id']} · {w['title'][:35]}", payload=f"w:{w['id']}"))
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, body, kb)

    elif action == 'ppl':
        users = sorted(db.list_users(), key=lambda u: ROLE_ORDER.get(u['role'], 99))
        lines = ['👥 Кто есть в боте:', '']
        lines += [f"• {u['name'] or 'Без имени'} — {ROLES.get(u['role'], u['role'])}" for u in users]
        lines.append('')
        if _role(uid) in MANAGER_ROLES:
            lines.append('Нажмите на человека, чтобы назначить роль.')
        else:
            lines.append('Роли назначают админ, инженер и мастера.')
        kb = InlineKeyboardBuilder()
        if _role(uid) in MANAGER_ROLES:
            for u in users[:15]:
                kb.row(CallbackButton(text=f"{_short_name(u['name'])} · {ROLES.get(u['role'], '')}",
                                      payload=f"pplu:{u['user_id']}"))
        if _role(uid) in ('admin', 'engineer'):
            n_local = project_docs.downloaded_count()
            total = len(project_docs.CATALOG)
            kb.row(CallbackButton(text=f'📥 Документация с Диска ({n_local}/{total})',
                                  payload='pdsync'))
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '\n'.join(lines), kb)

    elif action == 'pplu':
        if _role(uid) not in MANAGER_ROLES:
            return
        u = db.get_user(int(parts[1]))
        if u:
            kb = InlineKeyboardBuilder()
            role_items = [(r, label) for r, label in ROLES.items() if r != 'none']
            for i in range(0, len(role_items), 2):
                kb.row(*[CallbackButton(text=label, payload=f"pplr:{u['user_id']}:{r}")
                         for r, label in role_items[i:i + 2]])
            kb.row(CallbackButton(text='◀️ К людям', payload='ppl'))
            await send(msg, f"👤 {u['name'] or 'Без имени'} — сейчас {ROLES.get(u['role'])}.\n"
                            'Какую роль назначить?', kb)

    elif action == 'pplr':
        if _role(uid) not in MANAGER_ROLES:
            return
        target_id, role = int(parts[1]), parts[2]
        if role in ROLES and role != 'none':
            db.set_user_role(target_id, role)
            u = db.get_user(target_id)
            await send(msg, f"✅ {u['name'] or 'Без имени'} теперь {ROLES[role]}.")
            if target_id != uid:
                await notify(event.bot, target_id,
                             f'👋 Вам назначена роль: {ROLES[role]}. Теперь вам можно поручать работы.')

    elif action == 'camp':
        if _role(uid) not in MANAGER_ROLES:
            await send(msg, '📢 Задания по ЖК могут давать админ, инженер и мастера. '
                            'Попросите назначить вам роль в разделе «👥 Люди».')
            return
        assigned = db.all_house_complexes()
        kb = InlineKeyboardBuilder()
        for c in COMPLEXES:
            n = sum(1 for cid in assigned.values() if cid == c['id'])
            kb.row(CallbackButton(text=f"{c['name']} ({n} домов)", payload=f"campc:{c['id']}"))
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '📢 Задание по всем домам ЖК (например, «Опрессовка», «Сдача тепловых узлов»).\n'
                        'По какому ЖК?', kb)

    elif action == 'campc':
        cid = parts[1]
        assigned = db.all_house_complexes()
        n = sum(1 for c in assigned.values() if c == cid)
        if not n:
            await send(msg, f'⚠️ К «{COMPLEX_NAMES.get(cid, cid)}» пока не привязан ни один дом. '
                            'Сначала привяжите дома (карточка дома → «Указать ЖК»).')
            return
        STATE[uid] = {'mode': 'camp_title', 'complex_id': cid}
        await send(msg, f'📢 {COMPLEX_NAMES[cid]} ({n} домов).\n'
                        '🔧 Что нужно сделать по каждому дому? Например: «Опрессовка системы отопления».')

    elif action == 'campv':
        camp = db.get_campaign(int(parts[1]))
        if camp:
            done, total = db.campaign_progress(camp['id'])
            works = [w for w in db.list_works(open_only=False, limit=200)
                     if w['campaign_id'] == camp['id']]
            lines = [f"📢 «{camp['title']}» — {COMPLEX_NAMES.get(camp['complex_id'], '')}",
                     f"⏳ Срок: {fmt_deadline(camp['deadline'])}",
                     f'📊 Сдано {done} из {total}', '']
            lines += [work_line(w) for w in works]
            kb = InlineKeyboardBuilder()
            for w in works[:10]:
                if w['status'] != db.WORK_DONE:
                    kb.row(CallbackButton(text=f"№{w['id']} · {houses.HOUSES_BY_ID[w['house_id']]['address'][:30]}",
                                          payload=f"w:{w['id']}"))
            kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
            await send(msg, '\n'.join(lines), kb)

    elif action == 'wd':
        STATE[uid] = {'mode': 'work_dl_edit', 'work_id': int(parts[1])}
        await send(msg, '⏳ Новый срок? Напишите дату («25.09.2026») или «-» — без срока.')

    elif action == 'wn':
        STATE[uid] = {'mode': 'work_note', 'work_id': int(parts[1])}
        await send(msg, '📝 Напишите заметку: материалы, кто закупает, объём и т.п. '
                        '(заменит прежнюю заметку).')

    elif action == 'rsl':
        addrs = risers_mod.all_addresses()
        lines = ['🚿 Раскладка квартир по стоякам есть по этим домам:', '']
        lines += [f'• {a}' for a in addrs]
        lines += ['', '💡 Напишите адрес и квартиру — скажу этаж, стояк и соседей '
                      'сверху/снизу.\nНапример: «Седова 65а/2 кв 47»']
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '\n'.join(lines), kb)

    elif action == 'rsv':
        block = risers_mod.BLOCKS_BY_ID.get(int(parts[1]))
        if block:
            lines = [f"🚿 {' / '.join(block['addresses'])}",
                     f"Стояков: {block['risers']}", '',
                     'Этаж: квартиры по стоякам (слева направо)', '']
            for floor in sorted(block['floors'], key=int):
                flats = block['floors'][floor]
                lines.append(f"  {floor:>2} эт.: {', '.join(map(str, flats))}")
            lines += ['', '💡 Напишите «адрес кв N» — покажу стояк и соседей по нему.']
            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text='🚿 Другие дома', payload='rsl'),
                   CallbackButton(text='🏠 Меню', payload='menu'))
            await send(msg, '\n'.join(lines), kb)

    elif action == 'chat':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            records = db.house_chat_records(h['id'])
            lines = [f"💬 Из рабочего чата — {h['address']}", '']
            if not records:
                lines.append('По этому дому в чате пока ничего не писали.')
            for r in records:
                mark = '🔴 ' if r['is_issue'] else ''
                files = ' 📎' if r['has_files'] else ''
                lines.append(f"{mark}{r['created_at']} · {r['user_name'] or '—'}{files}")
                if r['text']:
                    lines.append(f"   {r['text'][:160]}")
                if r['transcript']:
                    lines.append(f"   🎙 {r['transcript'][:300]}")
                elif not r['text']:
                    lines.append('   (вложение без текста)')
            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text='🔧 Техника', payload=f"tech:{h['id']}"),
                   CallbackButton(text='🏠 К дому', payload=f"h:{h['id']}"))
            await send(msg, '\n'.join(lines), kb)

    elif action == 'tech':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            data = db.get_passport(h['id'])
            filled = sum(1 for k, _ in PASSPORT_FIELDS if data.get(k))
            n_points = db.points_count(h['id'])
            overdue = sum(1 for p in db.list_points(h['id'])
                          if (d := db.active_device(p['id'])) and d['verified_until']
                          and d['verified_until'] < datetime_today().isoformat())
            meters = db.list_meters(h['id'])
            submitted = {r['meter_id'] for r in db.readings_for_period(current_period())}
            lines = [f"🔧 Техника — {h['address']}", '',
                     f'🗂 Паспорт заполнен: {filled} из {len(PASSPORT_FIELDS)}',
                     f'🔧 Точек на ТП: {n_points}' + (f' · ❌ поверка просрочена: {overdue}'
                                                     if overdue else ''),
                     f'🧮 Счётчиков: {len(meters)}' +
                     (f' · сдано за {fmt_period(current_period())}: '
                      f'{sum(1 for m in meters if m["id"] in submitted)}' if meters else ''),
                     f"📁 Документов: {len(db.list_docs(h['id']))} своих + "
                     f"{len(catalog_for_house(h['address']))} проектных"]
            await send(msg, '\n'.join(lines), tech_kb(h))

    elif action == 'eq':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            points = db.list_points(h['id'])
            lines = [f"🔧 Оборудование ТП — {h['address']}", '']
            if not points:
                lines.append('Точек установки пока нет.\n'
                             'Добавьте точку — это место на тепловом пункте '
                             '(например, «ТП №2, подача отопления»). Приборы в ней '
                             'потом будут меняться, а история сохранится.')
            else:
                lines += [point_line(p) for p in points]
                today = datetime_today().isoformat()
                overdue = sum(1 for p in points
                              if (d := db.active_device(p['id'])) and d['verified_until']
                              and d['verified_until'] < today)
                if overdue:
                    lines += ['', f'❌ Поверка просрочена: {overdue} шт.']
            kb = InlineKeyboardBuilder()
            for p in points:
                label = f"{p['tp'] + ' · ' if p['tp'] else ''}{p['place']}"
                kb.row(CallbackButton(text=label[:60], payload=f"eqp:{p['id']}"))
            kb.row(CallbackButton(text='➕ Добавить точку', payload=f"eqadd:{h['id']}"),
                   CallbackButton(text='🏠 К дому', payload=f"h:{h['id']}"))
            await send(msg, '\n'.join(lines), kb)

    elif action == 'eqadd':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            kb = InlineKeyboardBuilder()
            for i in range(0, len(TP_LIST), 2):
                kb.row(*[CallbackButton(text=t, payload=f"eqtp:{h['id']}:{j}")
                         for j, t in enumerate(TP_LIST[i:i + 2], start=i)])
            kb.row(CallbackButton(text='◀️ Назад', payload=f"eq:{h['id']}"))
            await send(msg, f"➕ Новая точка, {h['address']}.\nНа каком тепловом пункте?", kb)

    elif action == 'eqtp':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        tp = TP_LIST[int(parts[2])]
        if h:
            tp_val = '' if tp == 'без номера' else tp
            kb = InlineKeyboardBuilder()
            for i in range(0, len(PLACES), 2):
                kb.row(*[CallbackButton(text=pl, payload=f"eqpl:{h['id']}:{j}:{int(parts[2])}")
                         for j, pl in enumerate(PLACES[i:i + 2], start=i)])
            kb.row(CallbackButton(text='✏️ Своё место', payload=f"eqplc:{h['id']}:{int(parts[2])}"))
            await send(msg, f"{tp_val or 'Тепловой пункт'}: где стоит манометр?", kb)

    elif action in ('eqpl', 'eqplc'):
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if not h:
            return
        if action == 'eqplc':
            tp = TP_LIST[int(parts[2])]
            STATE[uid] = {'mode': 'eq_place', 'house_id': h['id'],
                          'tp': '' if tp == 'без номера' else tp}
            await send(msg, '✏️ Напишите место установки одной строкой, '
                            'например: «Обратка ГВС, после насоса».')
            return
        place = PLACES[int(parts[2])]
        tp = TP_LIST[int(parts[3])]
        point_id = db.add_point(h['id'], place, '' if tp == 'без номера' else tp, _uname_cb(event))
        STATE[uid] = {'mode': 'eq_serial', 'point_id': point_id}
        await send(msg, f"✅ Точка добавлена: {tp}, {place}.\n\n"
                        '📟 Теперь заводской номер манометра (или «-», если без номера):')

    elif action == 'eqp':
        p = db.get_point(int(parts[1]))
        if p:
            await send(msg, point_card_text(p), point_card_kb(p))

    elif action == 'eqnew':
        p = db.get_point(int(parts[1]))
        if p:
            STATE[uid] = {'mode': 'eq_serial', 'point_id': p['id']}
            await send(msg, f"📟 {p['place']}: заводской номер нового манометра "
                            '(или «-», если без номера):')

    elif action == 'eqhist':
        p = db.get_point(int(parts[1]))
        if p:
            hist = db.point_history(p['id'])
            lines = [f"📜 История точки: {p['tp'] + ', ' if p['tp'] else ''}{p['place']}", '']
            if not hist:
                lines.append('Приборов пока не было.')
            for d in hist:
                mark = ('▶️ стоит сейчас' if d['status'] == 'active'
                        else f"снят {d['removed_at'] or ''}")
                lines.append(f"• № {d['serial'] or '—'} — поверка до "
                             f"{fmt_date(d['verified_until'])}, поставил "
                             f"{d['installed_by'] or '—'} ({d['installed_at'] or '—'}) · {mark}")
            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text='◀️ К точке', payload=f"eqp:{p['id']}"))
            await send(msg, '\n'.join(lines), kb)

    elif action == 'eqph':
        dev = db.get_device(int(parts[1]))
        which = parts[2]
        path = dev['photo_device'] if which == 'device' else dev['photo_passport']
        if dev and path and os.path.exists(path):
            caption = 'Фото прибора' if which == 'device' else 'Фото паспорта'
            try:
                await msg.answer(text=f"{caption}: № {dev['serial'] or '—'}",
                                 attachments=[InputMedia(path)])
            except Exception:
                log.exception('Не удалось отправить фото %s', path)
                await send(msg, '⚠️ Не получилось отправить фото.')
        else:
            await send(msg, '📷 Фото не найдено.')

    elif action == 'eqphadd':
        dev = db.get_device(int(parts[1]))
        if dev:
            STATE[uid] = {'mode': 'eq_photo', 'device_id': dev['id'], 'slot': 'device'}
            await send(msg, '📷 Пришлите фото манометра (общий вид с номером). '
                            'Следом попрошу фото паспорта. Если фото нет — напишите «-».')

    elif action == 'mt':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            meters = db.list_meters(h['id'])
            lines = [f"🧮 Счётчики — {h['address']}:", '']
            if not meters:
                lines.append('Пока не заведено ни одного счётчика.\n'
                             'Добавьте один раз — дальше Люся будет помнить, где что стоит.')
            else:
                lines += [meter_line(m) for m in meters]
            kb = InlineKeyboardBuilder()
            for m in meters:
                kb.row(CallbackButton(text=f"✍️ {m['label'][:35]}", payload=f"mtr:{m['id']}"))
            kb.row(CallbackButton(text='➕ Добавить счётчик', payload=f"mta:{h['id']}"),
                   CallbackButton(text='🏠 К дому', payload=f"h:{h['id']}"))
            await send(msg, '\n'.join(lines), kb)

    elif action == 'mta':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            kb = InlineKeyboardBuilder()
            for kind, label in METER_KINDS.items():
                kb.row(CallbackButton(text=label, payload=f"mtak:{h['id']}:{kind}"))
            kb.row(CallbackButton(text='◀️ Назад', payload=f"mt:{h['id']}"))
            await send(msg, f"➕ {h['address']}: какой счётчик добавляем?", kb)

    elif action == 'mtak':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        kind = parts[2]
        if h and kind in METER_KINDS:
            STATE[uid] = {'mode': 'meter_label', 'house_id': h['id'], 'kind': kind}
            await send(msg, f'📟 Опишите счётчик одной строкой: где стоит и его номер.\n'
                            'Например: «Подвал, 2-й подъезд, ввод ХВС, №123456».\n'
                            'Люся запомнит — в следующие месяцы просто выберете его из списка.')

    elif action == 'mtr':
        m = db.get_meter(int(parts[1]))
        if m:
            h = houses.HOUSES_BY_ID.get(m['house_id'])
            STATE[uid] = {'mode': 'meter_value', 'meter_id': m['id']}
            last = db.meter_readings(m['id'], limit=1)
            last_line = (f"\nПрошлое: {fmt_value(last[0]['value'])} ({fmt_period(last[0]['period'])})"
                         if last else '')
            await send(msg, f"✍️ {h['address'] if h else ''} — {m['label']}.{last_line}\n"
                            'Напишите текущее показание числом (например: 1234,56):')

    elif action == 'mth':
        m = db.get_meter(int(parts[1]))
        if m:
            h = houses.HOUSES_BY_ID.get(m['house_id'])
            rs = db.meter_readings(m['id'])
            lines = [f"📈 {h['address'] if h else ''} — {m['label']}:", '']
            prev = None
            rows = list(reversed(rs))
            for i, r in enumerate(rows):
                delta = ''
                if i > 0:
                    delta = f' ({r["value"] - rows[i - 1]["value"]:+g})'
                lines.append(f"• {fmt_period(r['period'])}: {fmt_value(r['value'])}{delta} — {r['submitted_by_name'] or '—'}")
            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text='✍️ Новое показание', payload=f"mtr:{m['id']}"),
                   CallbackButton(text='🧮 Счётчики дома', payload=f"mt:{m['house_id']}"))
            await send(msg, '\n'.join(lines), kb)

    elif action == 'mtall':
        if _role(uid) not in BRIEFING_ROLES:
            await send(msg, '🧮 Сводка по всем домам доступна руководству. '
                            'Показания по своему дому подавайте через карточку дома → «Счётчики».')
            return
        period = current_period()
        rows = db.readings_for_period(period)
        with_meters = db.houses_with_meters()
        submitted_houses = {r['house_id'] for r in rows}
        lines = [f'🧮 ПОКАЗАНИЯ ЗА {fmt_period(period).upper()}', '']
        if not with_meters:
            lines.append('Счётчики ещё не заведены ни по одному дому.')
        else:
            lines.append(f'Сдано: {len(submitted_houses)} из {len(with_meters)} домов со счётчиками.')
            lines.append('')
            cur_house = None
            for r in rows:
                if r['house_id'] != cur_house:
                    cur_house = r['house_id']
                    h = houses.HOUSES_BY_ID.get(cur_house)
                    lines.append(f"🏠 {h['address'] if h else '?'}:")
                lines.append(f"   {METER_KINDS.get(r['kind'], '📟').split()[0]} {r['label']}: "
                             f"{fmt_value(r['value'])} ({r['submitted_by_name'] or '—'})")
            missing = [houses.HOUSES_BY_ID[hid]['address'] for hid in with_meters
                       if hid not in submitted_houses and hid in houses.HOUSES_BY_ID]
            if missing:
                lines.append('')
                lines.append('⏳ Ещё не сдали: ' + ', '.join(sorted(missing)))
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '\n'.join(lines), kb)

    elif action == 'dir':
        kb = InlineKeyboardBuilder()
        for sec in DIRECTORY:
            kb.row(CallbackButton(text=sec['title'], payload=f"d:{sec['id']}"))
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '📖 Справочник — выберите раздел:', kb)

    elif action == 'd':
        sec = next((s for s in DIRECTORY if s['id'] == parts[1]), None)
        if sec:
            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text='◀️ Справочник', payload='dir'),
                   CallbackButton(text='🏠 Меню', payload='menu'))
            await send(msg, sec['text'], kb)
