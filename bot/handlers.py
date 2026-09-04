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
from maxapi.enums.upload_type import UploadType
from maxapi.types import InputMedia, InputMediaBuffer
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from . import agent, ai, db, feminine, houses
from . import project_docs
from . import risers as risers_mod
from . import status as bot_status
from . import announce, backup, banter, checks, flats, golos as golos_mod, inventory, mat
from . import maxfix, passport, plan
from . import proverka, razbor, remind, report, somneniya
from . import stoyak as stoyak_mod, transcribe

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

async def welcome_newcomer(event, uid: int):
    """Показывает новичка тем, кто раздаёт роли.

    Написать Люсе может любой, кому дали её @имя, — это и хорошо, ради того
    и заводили. Но пришедший остаётся «без роли», и поручить ему работу
    нельзя, пока роль не назначат. Раньше об этом никто не узнавал.
    """
    bot = getattr(event, 'bot', None)
    if not bot:
        return
    imya = _uname(event) or 'Без имени'
    for u in db.list_users():
        if u['role'] not in MANAGER_ROLES or u['user_id'] == uid:
            continue
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='👤 Назначить роль', payload=f'pplu:{uid}'))
        try:
            await bot.send_message(
                user_id=u['user_id'],
                text=f'👋 В боте новый человек: {imya}.\n'
                     'Пока он «без роли» — работы поручать нельзя.',
                attachments=[kb.as_markup()])
        except Exception:
            log.warning('Не удалось показать новичка пользователю %s', u['user_id'])


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
           CallbackButton(text='🧮 Счётчики', payload='mtpick'))
    kb.row(CallbackButton(text='🚫 Перекрытые стояки', payload='stl'),
           CallbackButton(text='🧰 Опись', payload='inv'))
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
    f'💡 Просто напишите адрес (например: «{houses.examples(1)[0]}») — я всё найду. 😉\n\n'
    '🧰 Опись — что где лежит: «в инвентарь: мотопомпа, подвал, Седова 71». '
    'Потом достаточно спросить «где мотопомпа».\n\n'
    '⚡️ Чтобы не искать кнопки в ленте, наберите «/» — под полем ввода откроется '
    'быстрое меню: /счетчики, /дома, /сводка — показания за месяц, /опись, '
    '/заявки, /меню — сюда.'
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
    n_meters = len(db.list_meters(h['id']))
    if n_meters:
        sdano = {r['meter_id'] for r in db.readings_for_period(current_period())}
        gotovo = sum(1 for m in db.list_meters(h['id']) if m['id'] in sdano)
        lines.append(f'🧮 Счётчиков: {gotovo} из {n_meters} сдано')
    lines += findings_lines(h)
    return '\n'.join(lines)


def findings_lines(h, limit: int = 6) -> list:
    """Замечания по дому: критичное красным значком, мелочи жёлтым.

    Та же логика, что в приложении (bot/checks.py) — иначе бот и приложение
    начнут показывать разное.
    """
    found = checks.house_findings(h['id'], current_period())
    if not found:
        return []
    lines = ['']
    for f in found[:limit]:
        lines.append(('❗ ' if f['level'] == checks.RED else '⚠️ ') + f['text'])
    if len(found) > limit:
        lines.append(f'… и ещё {len(found) - limit}')
    return lines


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


def house_buttons(kb, hs, payload='h', counts=None):
    """Каждый дом — отдельной кнопкой.

    Списком текстом это не работает: набирать адрес руками, стоя в подвале
    с телефоном, невозможно, а заказчик именно так и пробовал.
    """
    for h in hs:
        n = (counts or {}).get(h['id'], 0)
        znak = '🧮' if (counts is not None and n) else ('➕' if counts is not None else '🏠')
        text = f"{znak} {h['address'][:32]}" + (f' ({n})' if n else '')
        kb.row(CallbackButton(text=text, payload=f"{payload}:{h['id']}"))
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
    n_inv = len(db.list_items(house_id=h['id']))
    n_flat = len(db.flat_notes(h['id'], limit=99))
    kb.row(CallbackButton(text=f'🧰 Что здесь лежит{f" ({n_inv})" if n_inv else ""}',
                          payload=f"invh:{h['id']}"),
           CallbackButton(text=f'🚪 По квартирам{f" ({n_flat})" if n_flat else ""}',
                          payload=f"fl:{h['id']}"))
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
    lines += hronika_lines(h)
    lines += flat_note_lines(h)
    lines += inventory_lines(h)
    lines += works_lines(h)
    lines.append(f'Заполнено: {filled}/{len(PASSPORT_FIELDS)}. '
                 'Нажмите «Редактировать», чтобы дополнить.')
    return '\n'.join(lines)


def works_lines(h) -> list:
    """Плановые работы по дому — часть паспорта, а не отдельный список.

    Паспорт для того и нужен, чтобы всё про дом было в одном месте: что
    стоит, что с ним делали и что запланировано.
    """
    raboty = db.list_works(house_id=h['id'], open_only=True, limit=15)
    if not raboty:
        return ['📅 Плановых работ нет.', '']
    lines = [f'📅 ПЛАНОВЫЕ РАБОТЫ ({len(raboty)}):']
    for w in raboty:
        srok = f" — до {fmt_deadline(w['deadline'])}" if w['deadline'] else ''
        kto = f", {w['assignee']}" if w['assignee'] else ''
        znak = db.WORK_LABELS.get(w['status'], '•').split()[0]
        lines.append(f"   {znak} {w['title']}{srok}{kto}")
    lines.append('')
    return lines


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


def item_line(it, s_adresom=True) -> str:
    """Строка описи: «🧰 Мотопомпа ×2 — Седова 71, подвал»."""
    skolko = f" ×{it['qty']}" if it['qty'] > 1 else ''
    gde = []
    if s_adresom and it['house_id']:
        dom = houses.HOUSES_BY_ID.get(it['house_id'])
        if dom:
            gde.append(dom['address'])
    if it['place']:
        gde.append(it['place'])
    hvost = ' — ' + ', '.join(gde) if gde else ' — место не указано'
    return f"🧰 {it['name']}{skolko}{hvost}"


def inventory_lines(h) -> list:
    """Что лежит в этом доме — прямо в паспорте.

    Отдельный раздел «опись» открывать никто не станет. А в паспорте это
    увидит тот, кто уже стоит в этом подвале.
    """
    veshchi = db.list_items(house_id=h['id'])
    if not veshchi:
        return []
    lines = [f'🧰 ЗДЕСЬ ЛЕЖИТ ({len(veshchi)}):']
    for it in veshchi:
        lines.append('   ' + item_line(it, s_adresom=False))
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
    # Что здесь уже находили. Смотрят карточку стояка перед выездом — значит,
    # и напомнить надо здесь, а не в отдельном разделе
    dom = houses.detect_house(addr)
    zametki = db.flat_notes(dom['id'], flat) if dom else []
    if zametki:
        lines += ['', f'⚠️ По этой квартире уже находили ({len(zametki)}):']
        for z in zametki[:3]:
            lines.append(f"   {z['created_at'][:10]} — {z['text'][:90]} "
                         f"({z['author'] or '—'})")
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


# Что реально учитывают: два холодных водомера — на дом и на офисы, —
# и теплосчётчики в нежилых. ГВС не снимают, тепло в жилых домах тоже:
# там у жильцов прямые договоры со сбытовой компанией.
METER_KINDS = {
    'hvs': '💧 ХВС — дом',
    'hvs_office': '🏢 ХВС — офисы',
    'heat': '♨️ Теплосчётчик',
    'other': '📟 Другой',
}

# Для показа старых записей: ГВС когда-то заводили, надписи ему всё ещё нужны
METER_LABELS = dict(METER_KINDS, gvs='🔥 ГВС')

# Подсказка названия, чтобы не придумывать с нуля
METER_HINTS = {
    'hvs': 'Например: «Подвал, ввод ХВС на дом, №123456»',
    'hvs_office': 'Например: «Подвал, ввод ХВС на офисы, №123456»',
    'heat': 'Например: «ИТП, теплосчётчик, №123456»',
    'other': 'Например: «ВСХд-15 в подвале, №123456»',
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
    line = f"{METER_LABELS.get(m['kind'], '📟')} {m['label']}"
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
    'hvs': ('хвс', 'холодная', 'холодную', 'холодной', 'хв', 'хол', 'домовой', 'домовый'),
    'hvs_office': ('офис', 'офисы', 'офисов', 'офисный', 'офисах', 'офисные'),
    'heat': ('тепло', 'теплосчетчик', 'теплосчётчик', 'отопление', 'отоплению', 'гкал'),
    # ГВС не учитываем, но слово узнаём — чтобы сказать об этом прямо,
    # а не молчать в ответ на присланное показание
    'gvs': ('гвс', 'горячая', 'горячую', 'горячей'),
}

_READING_RE = re.compile(r'(\d+(?:[.,]\d+)?)')

# Номер дома с корпусом: «65а/5», «126/3», «8/1». Показания так не пишут,
# а вот адрес — постоянно: «гвс на 65а/5 нужно подать» Люся прочитала как
# показание 65 по ГВС
_ADDRESS_NUM = re.compile(r'(?<![\w/])\d{1,3}\s*[а-яё]?\s*/\s*\d{1,3}(?![\w])',
                          re.IGNORECASE)

# Разговор о подаче, отключении и работах с ресурсом — не показания.
# «Если бэк сегодня не продлит, то гвс на 65а/5 нужно подать после 8 утра» —
# в сообщении нет ни одного показания, а Люся отвечала «ГВС мы не учитываем»
RESOURCE_WORK = re.compile(
    r'(?<![а-я])(подать|подад\w+|подач\w+|подаем|пода[её]м|подали|подан\w*|'
    r'продли\w+|продлен\w*|отключ\w+|включ\w+|выключ\w+|'
    r'запуст\w+|пустит\w*|пустил\w*|перекр\w+|заглуш\w+|'
    r'опрессов\w+|промыв\w+|промыт\w*|циркуляц\w+|задвижк\w+)(?![а-я])',
    re.IGNORECASE)

# Слова, по которым видно, что речь о приборе учёта. Без такого слова
# сообщение показаниями не считается: «Офис Корал Трэвэл 28 дом. Течь
# с потолка» Люся разобрала как показание 28 по офисному счётчику —
# «офис» она принимала за вид прибора сама по себе
_METER_MARKER = re.compile(
    r'(?<![а-я])(сч[её]тчик\w*|показани\w*|хвс|гвс|хв|гв|холодн\w*|горяч\w*|'
    r'тепло\w*|теплосч[её]тчик\w*|гкал|куб\w*|м3|водом[еэ]р\w*|прибор\w*\s+уч[её]та)'
    r'(?![а-я])', re.IGNORECASE)


def _ryadom(mezhdu: str) -> bool:
    """Стоит ли число вплотную к слову о счётчике.

    «Ремонт насоса ГВС. 14, 65/4, 22 дом» — это план работ, а не показание
    14 по ГВС: число в другом предложении и относится к домам. Показание
    пишут рядом с прибором: «хвс 1234», «гвс — 567».
    """
    if re.search(r'[.!?;\n]', mezhdu):
        return False            # разные предложения — разные вещи
    return len(mezhdu.split()) <= 2


def _tolko_tsifry_posle_vida(low: str) -> bool:
    """Похоже ли на строку показаний: «... домовой 1234, офисный 567».

    После первого слова о виде счётчика должны идти только числа и другие
    такие же слова. Любой связный текст — уже не показания.
    """
    vse_slova = [w for words in METER_WORDS.values() for w in words]
    pervoe = min((low.find(w) for w in vse_slova if re.search(
        rf'(?<![а-я]){re.escape(w)}(?![а-я])', low)), default=-1)
    if pervoe < 0:
        return False
    hvost = low[pervoe:]
    for w in sorted(vse_slova, key=len, reverse=True):
        hvost = re.sub(rf'(?<![а-я]){re.escape(w)}(?![а-я])', ' ', hvost)
    hvost = re.sub(r'[\d.,;:()\s-]+', '', hvost)
    return hvost == ''


def parse_readings(text: str):
    """Разбирает «Седова 71 хвс 1234, гвс 567» → ('Седова 71', [('hvs', 1234)]).

    Сантехник стоит у прибора с телефоном — ходить по меню ему неудобно.
    Пишет как говорит. Возвращаем отдельно кусок с адресом и пары
    «вид — число»: вид без числа и число без вида не считаются.

    Адрес отрезаем по первому слову о счётчике. Иначе в «Седова 71 хвс 67»
    показание 67 спуталось бы с номером дома — а дом Седова 67 существует.
    """
    low = text.lower().replace('ё', 'е')
    # «Офис» и «домовой» сами по себе про счётчик ничего не говорят: это
    # уточнение вида. «Офис Корал Трэвэл 28 дом. Течь с потолка» Люся
    # разобрала как показание 28 по офисному счётчику. Без явного слова
    # про прибор такое принимается, только если после вида не осталось
    # ничего, кроме чисел: «Седова 71 домовой 1234» — да, рассказ — нет
    if not _METER_MARKER.search(text) and not _tolko_tsifry_posle_vida(low):
        return '', []
    # находим позиции слов вида и чисел, дальше сопоставляем по порядку
    metki = []
    for kind, words in METER_WORDS.items():
        for w in words:
            for m in re.finditer(rf'(?<![а-я]){re.escape(w)}(?![а-я])', low):
                metki.append((m.start(), m.end(), 'kind', kind))
    adresa = [m.span() for m in _ADDRESS_NUM.finditer(low)]
    for m in _READING_RE.finditer(low):
        # число внутри «65а/5» — часть адреса, показанием быть не может
        if any(a <= m.start() < b for a, b in adresa):
            continue
        metki.append((m.start(), m.end(), 'value', m.group(1)))
    metki.sort()

    result, ozhidaet, konets = [], None, 0
    for nachalo, kon, tip, val in metki:
        if tip == 'kind':
            ozhidaet, konets = val, kon
        elif ozhidaet is not None:
            if _ryadom(low[konets:nachalo]):
                result.append((ozhidaet, float(val.replace(',', '.'))))
            ozhidaet = None

    pervoe = next((pos for pos, _, tip, _ in metki if tip == 'kind'), None)
    adres = text[:pervoe].strip(' ,;.—-') if pervoe is not None else ''
    return adres, result


# Заводской номер: либо назван прямо («№ 64380455», «зав. номер 12345»),
# либо это длинная цифровая группа. Пять цифр подряд — потому что в названии
# прибора цифры короткие: «ВСХд-15», «СТВ-50», «УТ-1».
_SERIAL_MARKED = re.compile(
    r'(?:зав(?:одск\w*)?\.?\s*)?(?:№|No|N°|номер\w*|s/?n)\s*[:.]?\s*'
    r'([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9/-]{2,})', re.IGNORECASE)
_SERIAL_LONG = re.compile(r'(?<![\w/-])(\d{5,})(?![\w/-])')

CLEAR = object()   # «-» — очистить поле, а не вписать в него прочерк


def split_name_serial(text: str):
    """Делит одну строку на название и заводской номер.

    Человек пишет «ВСХд-15 № 64380455» одной строкой — так быстрее, чем
    жать две кнопки и отвечать дважды. Раньше это целиком уезжало в название,
    и заводской номер оставался пустым.

    Возвращает (название, номер). Любое из двух — None, если его в строке
    нет; номер CLEAR — если просят очистить.
    """
    text = (text or '').strip()
    if text in ('-', '—'):
        return None, CLEAR

    serial = None
    m = _SERIAL_MARKED.search(text)
    if m and not re.search(r'\d', m.group(1)):
        m = None       # «Nord» — не номер: в заводском номере всегда есть цифры
    if m:
        serial = m.group(1).strip(' .,;')
        ostatok = text[:m.start()] + ' ' + text[m.end():]
    else:
        m = _SERIAL_LONG.search(text)
        if m:
            serial = m.group(1)
            ostatok = text[:m.start()] + ' ' + text[m.end():]
        else:
            ostatok = text

    # После вырезания номера остаются хвосты: «СТВ-50 ,», «номер», двойные пробелы
    label = re.sub(r'\b(зав(одской)?|номер\w*)\b|№', ' ', ostatok, flags=re.IGNORECASE)
    label = re.sub(r'\s+', ' ', label)
    label = re.sub(r'\s+([,;.])', r'\1', label).strip(' ,;.:—-')
    return (label or None), serial


async def apply_meter_edit(msg, meter_id: int, text: str):
    """Записывает название и номер из одной строки и говорит, что куда легло.

    Разбор может ошибиться — например, если в названии есть длинное число.
    Поэтому итог всегда показываем словами, а рядом кнопку «Исправить».
    """
    label, serial = split_name_serial(text)
    fields, itog = {}, []
    if label:
        fields['label'] = label
        itog.append(f'✏️ Название: {label}')
    if serial is CLEAR:
        fields['serial'] = None
        itog.append('🔢 Заводской номер очищен')
    elif serial:
        fields['serial'] = serial
        itog.append(f'🔢 Заводской номер: {serial}')
    if fields:
        db.update_meter(meter_id, **fields)

    m = db.get_meter(meter_id)
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text='✏️ Исправить', payload=f"mted:{meter_id}"),
           CallbackButton(text='🧮 К счётчику', payload=f"mtc:{meter_id}"))
    if not fields:
        await send(msg, '🤔 Не поняла, что записать. Напишите название, номер '
                        'или всё сразу: «ВСХд-15 № 64380455».', kb)
        return
    hvost = ''
    if len(fields) == 1:
        # Второе поле не тронуто — про него лучше сказать, чем оставить гадать
        hvost = ('\n\nНомер прибора пока не указан.' if 'serial' not in fields
                 and not m['serial'] else '')
        if 'label' not in fields:
            hvost = f"\n\nНазвание оставила прежним: «{m['label']}»."
    await send(msg, '✅ Записала.\n' + '\n'.join(itog) + hvost, kb)


def pick_meter(house_id: int, kind: str):
    """Счётчик дома нужного вида. None — если нет, список — если их несколько.

    Снятые на поверку из выбора не исключаем: человеку важно узнать, что
    прибора нет на месте, а не получить «счётчик не заведён».
    """
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
    # Заявка — не показания. «Течь с потолка, предположительно канализация
    # в кв. 3» разбиралось как показание по номеру квартиры
    if ISSUE_WORDS.search(text or ''):
        return False
    # Разговор о подаче и отключении ресурса — работа, а не учёт. Но чистую
    # строку показаний («открыл задвижку, хвс 1234») глушить нельзя, поэтому
    # запрет снимается, если после вида прибора остались одни числа
    if RESOURCE_WORK.search(text or '') and not _tolko_tsifry_posle_vida(
            (text or '').lower().replace('ё', 'е')):
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
                       f'«{METER_LABELS[kind]}». Куда записать {fmt_value(value)}?', kb)
            return True
        elif m['status'] == db.METER_REMOVED:
            await send(event.message,
                       f"🔧 «{m['label']}» числится снятым на поверку "
                       f"({m['status_at'] or ''}, {m['status_by'] or '—'}). "
                       'Показание не записала — сначала отметьте, что прибор на месте.',
                       InlineKeyboardBuilder().row(
                           CallbackButton(text='✅ Поставлен на место',
                                          payload=f"mtback:{m['id']}")))
            return True
        else:
            proshloe = db.meter_readings(m['id'], limit=1)
            if proshloe and value < proshloe[0]['value']:
                # Счётчики назад не крутятся. Значит, либо ошиблись цифрой,
                # либо это вообще не показание: молча такое писать нельзя
                kb = InlineKeyboardBuilder()
                kb.row(CallbackButton(text='✅ Да, записать',
                                      payload=f"mtyes:{m['id']}:{value:g}"))
                kb.row(CallbackButton(text='✖️ Нет, это не показание',
                                      payload=f"mtc:{m['id']}"))
                await send(event.message,
                           f"🤔 {h['address']} — {m['label']}: {fmt_value(value)} "
                           f"меньше прошлого ({fmt_value(proshloe[0]['value'])}). "
                           'Не записала. Так и было?', kb)
                continue
            otvety.append(await record_reading(event, m, value, uid))

    if otvety:
        await send(event.message, '\n'.join(otvety))
    ne_uchityvaem = [k for k, _ in nekuda if k not in METER_KINDS]
    zavesti = [k for k, _ in nekuda if k in METER_KINDS]
    if ne_uchityvaem:
        await send(event.message,
                   f"ℹ️ {', '.join(METER_LABELS[k] for k in ne_uchityvaem)} мы не учитываем — "
                   'показание не записала.')
    if zavesti:
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='➕ Завести счётчик', payload=f"mta:{h['id']}"))
        kb.row(CallbackButton(text='🧮 Счётчики дома', payload=f"mt:{h['id']}"))
        vidy = ', '.join(METER_LABELS[k] for k in zavesti)
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


async def _save_meter_photo(url: str, meter_id: int) -> bool:
    """Фото самого прибора: по нему потом сверяют номер, если возник спор."""
    folder = os.path.join(DOCS_DIR, 'meters')
    os.makedirs(folder, exist_ok=True)
    try:
        data = await _download(url)
    except Exception:
        log.exception('Не удалось скачать фото счётчика')
        return False
    path = os.path.join(folder, f'{meter_id}.jpg')
    with open(path, 'wb') as f:
        f.write(data)
    db.update_meter(meter_id, photo=path)
    return True


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
    novyy = db.upsert_user(_uid(event), _uname(event))
    STATE.pop(_uid(event), None)
    await send(event.message, MAIN_TEXT, main_menu_kb())
    if novyy:
        await welcome_newcomer(event, _uid(event))


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


# Постоянное меню у поля ввода: клавиатура в MAX привязана к сообщению,
# и её приходится искать в ленте. Команды всегда под рукой.
QUICK_COMMANDS = [
    ('меню', 'Главное меню', 'menu'),
    ('дома', 'Наши дома по ЖК', 'homes'),
    ('счетчики', 'Счётчики: выбрать дом', 'mtpick'),
    ('сводка', 'Показания за месяц и выгрузка', 'mtall'),
    ('поверка', 'Снятые на поверку', 'mtoffl'),
    ('заявки', 'Открытые заявки', 'rl'),
    ('работы', 'Все работы и сроки', 'wl'),
    ('мои', 'Мои работы', 'myw'),
    ('брифинг', 'Брифинг по хозяйству', 'brief'),
    ('справка', 'Справочник и нормативы', 'dir'),
    ('паспорта', 'Паспорта домов: что заполнено', 'plist'),
    ('напоминания', 'Что Люся должна напомнить', 'rem'),
    ('голос', 'Страница записи: наговорить Люсе', 'golos'),
    ('журнал', 'Что записано за день', 'jrnl'),
    ('проверка', 'Записи с чужими адресами', 'chk'),
    ('итоги', 'Разобрать день по домам', 'itogi'),
    ('опись', 'Что где лежит: имущество и инструмент', 'inv'),
    ('копия', 'Резервная копия и паспорта в Markdown', 'kopiya'),
]

# Те же команды латиницей. В меню они не показываются, но срабатывают:
# набирают их и с английской раскладки, и по памяти — а ещё это запасной
# список, если MAX не примет русские имена
ALIASES = {
    'меню': ('menu',),
    'дома': ('doma',),
    'счетчики': ('счётчики', 'schet'),   # с «ё» тоже: в MAX буква своя
    'сводка': ('svodka',),
    'поверка': ('poverka',),
    'заявки': ('zayavki',),
    'работы': ('raboty',),
    'мои': ('moi',),
    'брифинг': ('brief',),
    'справка': ('spravka',),
    'паспорта': ('pasporta',),
    'напоминания': ('napominaniya',),
    'голос': ('golos',),
    'журнал': ('jrnl', 'zhurnal'),
    'проверка': ('chk', 'proverka'),
    'итоги': ('itogi',),
    'опись': ('opis', 'inventar'),
    'копия': ('kopiya', 'backup'),
}


def _make_command_handler(payload):
    async def handler(event: MessageCreated):
        uid = _uid(event)
        db.upsert_user(uid, _uname(event))
        bot_status.note_update('команда')
        await run_action(payload, event.message, uid, event)
    return handler


def register_quick_commands():
    """Вешает обработчики на команды быстрого меню и на их двойники."""
    for name, _, payload in QUICK_COMMANDS:
        imena = [name, *ALIASES.get(name, ())]
        # у /menu уже есть свой обработчик
        imena = [n for n in imena if n != 'menu']
        if imena:
            dp.message_created(Command(imena))(_make_command_handler(payload))


register_quick_commands()


@dp.message_created(Command(['тихо', 'tiho']))
async def on_quiet(event: MessageCreated):
    """Выключить живые реплики в этом чате."""
    chat_id = getattr(event.message.recipient, 'chat_id', None)
    if not is_group(event) or chat_id is None:
        await send(event.message, '🤫 Это для рабочего чата: там я иногда '
                                  'отзываюсь не по делу. В личке отвечаю только вам.')
        return
    db.set_banter(chat_id, False)
    await send(event.message, '🤐 Поняла, молчу. По делу отвечать буду, '
                              'если позовут по имени. Вернуть — /болтай.')


@dp.message_created(Command(['дом', 'dom']))
async def on_bind_house(event: MessageCreated):
    """«/дом Седова 65а/3» изнутри чата — привязывает чат к дому.

    Имени чата MAX не присылает, узнать «а это чей чат» неоткуда. Поэтому
    привязку делает человек, один раз, изнутри нужного чата.
    """
    chat_id = getattr(event.message.recipient, 'chat_id', None)
    if not is_group(event) or chat_id is None:
        await send(event.message, '🏠 Эту команду наберите в самом чате дома — '
                                  'тогда я запомню, чей он.')
        return
    text = (event.message.body.text or '')
    text = re.sub(r'^/\S+\s*', '', text).strip()
    if not text:
        dom_id = db.chat_house(chat_id)
        dom = houses.HOUSES_BY_ID.get(dom_id) if dom_id else None
        await send(event.message,
                   f"🏠 Этот чат — дом {dom['address']}." if dom else
                   '🏠 Напишите адрес: «/дом Седова 65а/3». Тогда я буду знать, '
                   'куда отправлять объявления для жильцов.')
        return
    dom = houses.detect_house(text)
    if not dom:
        found = houses.search(text)
        dom = found[0] if len(found) == 1 else None
    if not dom:
        await send(event.message, f'🤔 Не нашла такой дом. Напишите адрес как в '
                                  f'справочнике, например «{_primer(0)}».')
        return
    db.bind_house_chat(chat_id, dom['id'], by_name=_uname(event))
    await send(event.message,
               f"🏠 Запомнила: этот чат — {dom['address']}.\n\n"
               'Сюда буду присылать объявления для жильцов: отключения воды, '
               'сроки работ. Только по подтверждению человека — сама ничего '
               'не пишу. Отвязать — /дом_нет.')


@dp.message_created(Command(['дом_нет', 'dom_net']))
async def on_unbind_house(event: MessageCreated):
    chat_id = getattr(event.message.recipient, 'chat_id', None)
    if not is_group(event) or chat_id is None:
        return
    db.unbind_house_chat(chat_id)
    await send(event.message, '🏠 Отвязала. Объявления сюда присылать не буду.')


@dp.message_created(Command(['болтай', 'boltay']))
async def on_banter_on(event: MessageCreated):
    """Вернуть живые реплики в этом чате."""
    chat_id = getattr(event.message.recipient, 'chat_id', None)
    if not is_group(event) or chat_id is None:
        await send(event.message, '🙂 Это для рабочего чата.')
        return
    db.set_banter(chat_id, True)
    banter.forget(chat_id)
    await send(event.message, '🙂 Хорошо, буду иногда вставлять слово — '
                              'по-доброму и нечасто. Надоем — /тихо.')


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


def zapomnit_dialog(event):
    """Запоминает chat_id личной переписки: из уведомления его не узнать.

    MAX про голосовое в личке присылает пустое уведомление, а забрать
    сообщение можно только зная чат. В списке чатов бота диалогов нет.
    """
    r = getattr(event.message, 'recipient', None)
    tip = getattr(getattr(r, 'chat_type', None), 'value', getattr(r, 'chat_type', None))
    if r is not None and tip == 'dialog' and getattr(r, 'chat_id', None):
        db.remember_dialog(r.chat_id, getattr(r, 'user_id', None))


def _chat_id(event):
    """Где идёт разговор. В личке — None: своей памятью личка и чат не делятся."""
    return getattr(getattr(event.message, 'recipient', None), 'chat_id', None) \
        if is_group(event) else None


def is_group(event) -> bool:
    """Групповой чат или канал (в отличие от лички)."""
    recipient = getattr(event.message, 'recipient', None)
    chat_type = getattr(recipient, 'chat_type', None)
    chat_type = getattr(chat_type, 'value', chat_type)
    return chat_type in ('chat', 'channel')


# Как к Люсе обращаются в чате: по имени, с @ или без
ADDRESS_RE = re.compile(
    r'^\s*@?(люс[яеию]|lusya|lyusya)\b[\s,:—-]*', re.IGNORECASE)

# Имя где угодно во фразе. Падежи перечислены целиком, чтобы не ловить
# «люстру» и «Люсьен»: обращение — это именно имя, а не начало слова
NAME_ANYWHERE = re.compile(
    r'(?<![\w-])(люся|люсе|люсю|люси|люсей|lusya|lyusya)(?![\w-])', re.IGNORECASE)


# Слова, по которым сообщение похоже на заявку/аварию
ISSUE_WORDS = re.compile(
    r'\b(теч[её]т|течь|подтека|подтапл|подтопл|кап[аи]ет|затопил|топит|залив|'
    r'прорв|порыв|свищ|'
    r'засор|забил|не\s+работает|нет\s+воды|нет\s+гвс|нет\s+хвс|нет\s+отоплен|'
    r'холодн[ыа]я\s+батаре|авари|сорвал|потоп|фонтан|срочно)', re.IGNORECASE)


SPEECH_TYPES = ('audio', 'video')


def _a_type(a) -> str:
    return getattr(getattr(a, 'type', None), 'value', getattr(a, 'type', None)) or ''


def speech_ready(body) -> str | None:
    """Расшифровка, которую MAX сделал сам.

    У голосовых MAX присылает поле transcription — уже готовый текст.
    Брать его надо первым: это быстрее, точнее по именам и не стоит денег.
    """
    for a in (getattr(body, 'attachments', None) or []):
        if _a_type(a) not in SPEECH_TYPES:
            continue
        gotovo = (getattr(a, 'transcription', None) or '').strip()
        if gotovo:
            return gotovo
    return None


def speech_url(body) -> str | None:
    """Ссылка на голосовое или видео во вложениях — то, что можно расшифровать.

    Ссылка лежит в разных местах: у голосового — в payload.url, у видео —
    в urls.mp4_*. Раньше смотрели только первое, и голосовые в личке
    молча пропадали.
    """
    for a in (getattr(body, 'attachments', None) or []):
        if _a_type(a) not in SPEECH_TYPES:
            continue
        payload = getattr(a, 'payload', None)
        url = getattr(payload, 'url', None) if payload else None
        if url:
            return url
        urls = getattr(a, 'urls', None)
        if urls:
            for pole in ('mp4_480', 'mp4_360', 'mp4_720', 'mp4_240', 'mp4_144',
                         'mp4_1080', 'hls'):
                ssylka = getattr(urls, pole, None)
                if ssylka:
                    return ssylka
    return None


def syroe_soobschenie(message) -> str:
    """Всё сообщение как есть — в лог, когда ничего не распозналось.

    MAX прислал сообщение без текста и без вложений, и что это было —
    снаружи не видно. Дальше гадать бессмысленно: смотрим, что реально
    пришло, а не что должно было прийти.
    """
    try:
        dump = message.model_dump_json(exclude_none=True)
    except Exception:
        try:
            dump = repr(message)
        except Exception:
            return '<не удалось прочитать>'
    return dump[:1500]


def peresylka(message):
    """Тело пересланного или процитированного сообщения, если оно есть.

    Пересылают голосовое — само сообщение приходит пустым, а запись лежит
    во вложенном.
    """
    link = getattr(message, 'link', None)
    return getattr(link, 'message', None) if link else None


def opisat_vlozheniya(body) -> str:
    """Что за вложения пришли — строкой в лог.

    Без этого «бот не ответил на голосовое» неотличимо от «бот не получил
    сообщение»: что именно присылает MAX, снаружи не видно.
    """
    opis = []
    for a in (getattr(body, 'attachments', None) or []):
        payload = getattr(a, 'payload', None)
        polya = [k for k in ('url', 'token') if getattr(payload, k, None)]
        if getattr(a, 'transcription', None):
            polya.append('transcription')
        if getattr(a, 'urls', None):
            polya.append('urls')
        opis.append(f"{_a_type(a)}({','.join(polya) or 'пусто'})")
    return ', '.join(opis) or 'вложений нет' 


# Задание модели на пересказ. Собрано из двух граблей сразу: сначала она
# дописывала в отчёт работы, которых не было, потом — писала дословно и
# тащила в паспорт дома всё, включая брань
SUMMARY_RULES = (
    'Ты инженер управляющей компании. Ниже расшифровка того, что сантехник '
    'наговорил на объекте — живой речью, с оговорками и обрывками.\n\n'
    '{text}\n\n'
    'Перескажи деловым языком, коротко — две-четыре строки. По возможности '
    'ответь на три вопроса: где именно, что обнаружено, что предлагается '
    'или требуется дальше.\n'
    'Правила, нарушать нельзя:\n'
    '— только то, что сказано. Не добавляй ни причин, ни работ, ни '
    'результатов, о которых не говорили;\n'
    '— «подтапливает» не превращай в «устранено», «посмотрю» — в «сделал», '
    '«надо бы» — в «сделано»;\n'
    '— брань и просторечие передавай нейтрально, профессиональным языком. '
    'Ругательства не воспроизводи;\n'
    '— если чего-то из трёх вопросов в записи нет — просто не пиши об этом;\n'
    '— голое число не называй ни квартирой, ни подъездом, ни этажом, если '
    'этого слова не было в записи: «71» может оказаться номером дома;\n'
    '— без вступлений, без адреса, без обращений и без оценок.'
)

# Если модель недоступна, показываем сказанное как есть — но не длиннее
VERBATIM_LIMIT = 400


async def short_summary(parts: list[str], address: str | None,
                        house=None, istochnik: str = somneniya.IZ_RECHI) -> str:
    """Короткий деловой пересказ видеоотчёта.

    Дословная стенограмма в чате не нужна никому: её долго читать, в ней
    оговорки и брань, а в паспорт дома она попадает как есть. Нужна суть:
    где, что, что дальше. При этом пересказ — то самое место, где модель
    однажды дописала работы, которых не было, поэтому правила жёсткие,
    температура нулевая, а дословная запись остаётся в базе и доступна
    по команде /chat.
    """
    parts = [p.strip() for p in parts if p and p.strip()]
    slova = ' '.join(parts)
    if house is None and address:
        house = next((h for h in houses.HOUSES if h['address'] == address), None)
    head = f'🎙 {address}' if address else '🎙 Видеоотчёт'
    if len(parts) > 1:
        head += f' · {len(parts)} видео'
    hvost = '' if address else '\n\n❓ Адрес не назвали — напишите, какой дом.'

    summary = await ai.ask(SUMMARY_RULES.format(text=slova),
                           max_tokens=300, temperature=0)
    if not summary:
        # Без модели лучше показать сказанное, чем не показать ничего
        korotko = mat.mask(slova)
        if len(korotko) > VERBATIM_LIMIT:
            korotko = korotko[:VERBATIM_LIMIT] + '…'
        return f'{head}\n{korotko}{hvost}'

    gotovo = mat.mask(feminine.fix(summary.strip()))
    # Пересказ проверяем по справочнику: парковка с квартирой 71 в нём не
    # сходится, и сказать об этом должна Люся, а не человек через сутки
    voprosy = somneniya.proverit(house, gotovo, slova, istochnik)
    if voprosy:
        hvost = '\n\n' + '\n'.join(voprosy)
    return (f'{head}\n{gotovo}\n'
            f'📄 Дословно — команда /chat.{hvost}')


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
        summary = await short_summary(
            series['parts'], series['address'],
            house=houses.HOUSES_BY_ID.get(series['house_id']),
            istochnik=series.get('istochnik', somneniya.IZ_RECHI))
        link = (NewMessageLink(type=MessageLinkType.REPLY, mid=series['first_mid'])
                if series['first_mid'] else None)
        await series['bot'].send_message(chat_id=series['chat_id'], text=summary, link=link)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception('Не удалось отправить пересказ серии')
        SERIES.pop(key, None)


def queue_series(key, text, house, is_issue, bot, chat_id, mid,
                 istochnik=somneniya.IZ_RECHI):
    """Копит расшифровки серии; ответ уйдёт, когда поток видео стихнет."""
    series = SERIES.get(key)
    if series is None:
        series = SERIES[key] = {
            'parts': [], 'address': None, 'house_id': None, 'is_issue': False,
            'pending': False, 'bot': bot, 'chat_id': chat_id,
            'first_mid': mid, 'task': None, 'istochnik': somneniya.IZ_RECHI,
        }
        series['task'] = asyncio.create_task(_flush_series(key))
    else:
        series['pending'] = True          # продлить ожидание: серия продолжается
    series['parts'].append(text)
    series['is_issue'] = series['is_issue'] or is_issue
    if house and not series['address']:
        series['address'] = house['address']
        series['house_id'] = house['id']
        series['istochnik'] = istochnik


async def transcribe_later(record_id: int, url: str | None, bot=None, chat_id=None,
                           mid=None, gotovo: str | None = None):
    """Расшифровывает голосовое/видео в фоне и дописывает текст к сообщению.

    Ответ в чат — только на аварийное и только один на серию роликов.
    """
    try:
        text = gotovo or await transcribe.transcribe_url(url)
        if not text:
            return
        record = db.get_chat_record(record_id)
        key = (chat_id, record['user_id'] if record else None)
        # Откуда взялся адрес — важно не меньше самого адреса: названный в
        # речи Люся печатает как факт, подобранный по соседям — переспрашивает
        house = houses.detect_house(text)
        istochnik = somneniya.IZ_RECHI
        # Адрес часто стоит в подписи к видео, а не в самой речи: «8/5 Салон
        # красоты». Он уже распознан при записи сообщения — незачем спрашивать
        # заново
        if not house and record and record['house_id']:
            house = houses.HOUSES_BY_ID.get(record['house_id'])
            istochnik = somneniya.IZ_PODPISI
        # Продолжения серии («перекрыли стояк», «всё готово») адреса не содержат —
        # цепляем их к дому, который назвали в начале отчёта.
        if not house:
            series = SERIES.get(key)
            if series and series.get('house_id') is not None:
                house = houses.HOUSES_BY_ID.get(series['house_id'])
                istochnik = somneniya.IZ_SERII
        # Адрес мог уйти отдельным сообщением прямо перед роликом
        if not house and record and chat_id:
            ryadom = db.recent_house_of(chat_id, record['user_id'])
            if ryadom:
                house = houses.HOUSES_BY_ID.get(ryadom)
                istochnik = somneniya.IZ_SOSEDNEGO
        is_issue = bool(ISSUE_WORDS.search(text))
        # В базу — без брани: расшифровку потом читают в паспорте дома,
        # в выгрузке инженеру и в отчёте руководителю
        db.set_chat_transcript(record_id, mat.mask(text),
                               house_id=house['id'] if house else None,
                               is_issue=is_issue)
        log.info('Расшифровано сообщение %s: %.80s', record_id, text)

        # Квартиру называют в подписи к ролику, а находку — голосом в кадре.
        # Поэтому ищем по подписи и расшифровке вместе
        zapis = db.get_chat_record(record_id)
        if zapis is not None:
            dom = house or houses.HOUSES_BY_ID.get(zapis['house_id'])
            vmeste = f"{zapis['text'] or ''} {text}".strip()
            otvet = zapisat_nahodku(record_id, dom, vmeste,
                                    zapis['user_id'], zapis['user_name'])
            if otvet and bot and chat_id:
                await bot.send_message(chat_id=chat_id, text=otvet)

        if bot and chat_id:
            queue_series(key, text, house, is_issue, bot, chat_id, mid, istochnik)
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
        if text and not fix_report_house(event, record_id, text):
            if house:
                attach_house_to_report(event, record_id, house, text)
        if text and house:
            otvet = zapisat_nahodku(record_id, house, text, _uid(event), _uname(event))
            if otvet:
                asyncio.create_task(send(event.message, otvet))
        # Голосовые и видеоотчёты расшифровываем фоном, чтобы не тормозить чат
        url = speech_url(body)
        gotovo = speech_ready(body)
        if not url and not gotovo:
            gotovo, url = maxfix.speech_from_raw(getattr(body, 'mid', None))
        if url or gotovo:
            asyncio.create_task(transcribe_later(
                record_id, url, bot=getattr(event, 'bot', None),
                chat_id=getattr(event.message.recipient, 'chat_id', None),
                mid=getattr(body, 'mid', None), gotovo=gotovo))
        elif getattr(body, 'attachments', None):
            log.info('Вложение без речи: %s', opisat_vlozheniya(body))
    except Exception:
        log.exception('Не удалось записать сообщение чата')


# «Не 28 дом, а 18 б», «это не Седова, а Трилиссера 8/5» — поправка адреса
_POPRAVKA = re.compile(r'(?<![а-я])не\b.{0,60}?(?<![а-я])а\b(.{2,60})$',
                       re.IGNORECASE | re.DOTALL)


def parse_correction(text: str):
    """Дом из поправки «не то, а это». None — если это не поправка."""
    m = _POPRAVKA.search((text or '').strip())
    if not m:
        return None
    return houses.detect_house(m.group(1))


def fix_report_house(event, record_id, text: str) -> bool:
    """Поправка адреса в чате меняет дом у последнего отчёта. True — если поправили.

    Человек пишет «не 28 дом, а 18 б» — и вправе считать, что этого хватило.
    Раньше Люся отвечала «записала», хотя записать ничего не могла: у неё
    все инструменты на чтение. Теперь адрес правда меняется, а Люся говорит,
    что именно и на что.
    """
    dom = parse_correction(text)
    if not dom:
        return False
    chat_id = getattr(event.message.recipient, 'chat_id', None)
    if chat_id is None:
        return False
    otchyot = db.last_report_of(chat_id, _uid(event))
    if not otchyot or otchyot['id'] == record_id or otchyot['house_id'] == dom['id']:
        return False
    bylo = houses.HOUSES_BY_ID.get(otchyot['house_id'])
    db.set_chat_house(otchyot['id'], dom['id'])
    log.info('Отчёт %s перепривязан: %s → %s', otchyot['id'],
             bylo['address'] if bylo else '—', dom['address'])
    otvet = send(event.message,
                 f"📌 Исправила: тот отчёт теперь {dom['address']}"
                 + (f" (было {bylo['address']})." if bylo else '.'))
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        otvet.close()
        return True
    asyncio.create_task(otvet)
    return True


# Сделанная работа: по такому сообщению видно, что на доме что-то менялось.
# Это и есть «ключевое», ради чего лента дома вообще нужна
WORK_FACT = re.compile(
    r'(?<![а-я])(замен\w+|поменя\w+|постав\w+|установ\w+|смонтирова\w+|'
    r'снял\w*|сняли|демонтирова\w+|промы\w+|прочист\w+|опрессова\w+|'
    r'включ\w+|отключ\w+|перекр\w+|запуст\w+|подключ\w+|подал\w*|подали|'
    r'устран\w+|отремонтирова\w+|починил\w*|заварил\w*|сварил\w*|'
    r'восстанов\w+|отогре\w+|заглуш\w+|сдела\w+|выполн\w+)(?![а-я])',
    re.IGNORECASE)


def hronika_lines(h, limit: int = 8) -> list:
    """Что происходило на доме — фактами, а не лентой сообщений.

    Раскладывает день по домам вечерний разбор: код видит одно сообщение,
    модель — весь день целиком.
    """
    from datetime import date

    fakty = db.house_facts(h['id'], limit=limit + 1)
    if not fakty:
        return []
    lines = ['📆 ХРОНИКА ДОМА:']
    for f in fakty[:limit]:
        den = f['day']
        try:
            den = date.fromisoformat(f['day']).strftime('%d.%m')
        except ValueError:
            pass
        lines.append(f"   {den} — {f['text'][:110]}")
    if len(fakty) > limit:
        lines.append(f'   … и ещё {len(fakty) - limit}')
    lines.append('')
    return lines


def _adres(house_id) -> str:
    dom = houses.HOUSES_BY_ID.get(house_id) if house_id else None
    return dom['address'] if dom else '—'


def journal_lines(den: str) -> list:
    """Что записано за день — одним экраном, по разделам.

    Каждая запись живёт в своей таблице, и «что Люся насохраняла за вчера»
    приходилось собирать по пяти экранам.
    """
    j = db.day_journal(den)
    lines = [f'📅 ЧТО ЗАПИСАНО ЗА {den}', '']

    if j['readings']:
        lines.append(f"🧮 Показания ({len(j['readings'])}):")
        for r in j['readings'][:12]:
            lines.append(f"   {_adres(r['house_id'])} — {r['label']}: "
                         f"{fmt_value(r['value'])} ({r['submitted_by_name'] or '—'})")
        lines.append('')
    if j['requests']:
        lines.append(f"📋 Заявки ({len(j['requests'])}):")
        for r in j['requests'][:10]:
            lines.append(f"   {r['address']} — {r['description'][:60]}")
        lines.append('')
    if j['works']:
        lines.append(f"📅 Работы ({len(j['works'])}):")
        for w in j['works'][:10]:
            lines.append(f"   {_adres(w['house_id'])} — {w['title'][:60]}")
        lines.append('')
    if j['flat_notes']:
        lines.append(f"🚪 Находки по квартирам ({len(j['flat_notes'])}):")
        for z in j['flat_notes'][:10]:
            lines.append(f"   {_adres(z['house_id'])}, кв. {z['flat']} — {z['text'][:60]}")
        lines.append('')
    if j['shutoffs']:
        lines.append(f"🚫 Перекрытия стояков ({len(j['shutoffs'])}):")
        for z in j['shutoffs']:
            kogda = 'открыт' if z['opened_at'] else 'ещё перекрыт'
            lines.append(f"   {_adres(z['house_id'])}, кв. {z['flat']} — {kogda}")
        lines.append('')
    if j['inventory']:
        lines.append(f"🧰 В опись ({len(j['inventory'])}):")
        for it in j['inventory'][:10]:
            lines.append('   ' + item_line(it)[2:])
        lines.append('')
    if j['passports']:
        lines.append(f"🗂 Паспорта домов ({len(j['passports'])} записей):")
        for p in j['passports'][:10]:
            lines.append(f"   {_adres(p['house_id'])} — "
                         f"{PASSPORT_LABELS.get(p['field'], p['field'])}")
        lines.append('')
    if j['meters']:
        lines.append(f"🧮 Заведены счётчики ({len(j['meters'])}):")
        for m in j['meters'][:8]:
            lines.append(f"   {_adres(m['house_id'])} — {m['label']}")
        lines.append('')
    if j['reminders']:
        lines.append(f"⏰ Напоминания ({len(j['reminders'])}):")
        for r in j['reminders'][:8]:
            lines.append(f"   {r['due_at']} — {r['text'][:50]}")
        lines.append('')

    if len(lines) == 2:
        lines.append('За этот день ничего не записано.')
        if j['chat']:
            lines.append(f'В ленте чата — {j["chat"]} сообщений, но записей из '
                         'них не вышло.')
        return lines
    lines.append(f'💬 Сообщений в ленте чата: {j["chat"]}')
    return lines


def flat_note_lines(h, limit: int = 8) -> list:
    """Что находили по квартирам этого дома."""
    zametki = db.flat_notes(h['id'], limit=limit + 1)
    if not zametki:
        return []
    lines = [f'🚪 НАХОДКИ ПО КВАРТИРАМ ({len(zametki)}):']
    for z in zametki[:limit]:
        lines.append(f"   кв. {z['flat']} — {z['text'][:90]} "
                     f"({z['created_at'][:10]}, {z['author'] or '—'})")
    if len(zametki) > limit:
        lines.append(f'   … и ещё {len(zametki) - limit}')
    lines.append('')
    return lines


def zapisat_nahodku(record_id, house, text: str, uid, uname) -> str | None:
    """«71/1, 105 квартира, нашёл подмес» — находка ложится в карточку квартиры.

    Заказчик: «вот эту информацию нужно сохранить — в этой квартире уже был
    обнаружен подмес, он может обнаружиться снова, опять забудут перекрыть
    краны». Дом, квартира и находка должны быть названы все три: без любой
    из них не пишем ничего.

    Возвращает текст ответа или None. Отправляет вызывающий: находку ловим
    и из живого сообщения, и из фоновой расшифровки, а там события нет.
    """
    if not house or not text:
        return None
    razbor = flats.parse_note(text, house)
    if not razbor:
        return None
    kvartira, chto = razbor
    # Находку по квартире, которой в доме нет, записывать нельзя: она осядет
    # в карточке дома и всплывёт через полгода как факт. Лучше переспросить
    somnenie = somneniya.proverit(house, f'кв. {kvartira} {chto}')
    if somnenie:
        return somnenie[0]
    kind = flats.kind_of(chto)
    if db.flat_note_exists(house['id'], kvartira, kind):
        return None         # подпись и голосовое об одном и том же выезде

    bylo = db.flat_notes(house['id'], kvartira, limit=3)
    db.add_flat_note(house['id'], kvartira, mat.mask(flats.summary(text)), kind=kind,
                     record_id=record_id, author_id=uid, author=uname)
    log.info('Находка: %s кв.%s — %s', house['address'], kvartira, chto)

    stroki = [f"📌 {house['address']}, кв. {kvartira} — записала: {chto}."]
    # Ради этого всё и затевалось: сказать, что здесь такое уже находили
    povtor = [z for z in bylo if z['kind'] == kind]
    if povtor:
        stroki.append(f"⚠️ Тут это уже находили — {povtor[0]['created_at'][:10]}, "
                      f"{povtor[0]['author'] or '—'}.")
    elif bylo:
        stroki.append(f"Раньше по этой квартире было: {bylo[0]['text'][:80]} "
                      f"({bylo[0]['created_at'][:10]}).")
    return '\n'.join(stroki)


def znachimo(rec) -> bool:
    """Стоит ли запись в ленте дома. Остальное — разговор, он в архиве.

    Заказчик: «пусть собирает, но лишь бы мусор не собирала, а фиксировала
    только ключевое». Ключевое — это отчёт, авария или сделанная работа.
    «Крутая УК)))» в паспорт дома попадать не должно.
    """
    if rec['transcript'] or rec['is_issue'] or rec['has_files']:
        return True
    return bool(rec['text'] and WORK_FACT.search(rec['text']))


# Мелочь вокруг адреса, которая не мешает считать сообщение ответом:
# «это Седова 71», «по Советской 30»
_ADRES_MUSOR = re.compile(
    r'(?<![а-я])(это|вот|дом|дома|адрес|адресу|по|на|в|во|у|же|там|тут)(?![а-я])',
    re.IGNORECASE)


def tolko_adres(text: str, house) -> bool:
    """Сообщение состоит из одного адреса и ничего больше.

    Костя написал «Только что включил 65/3,4» — обычная рабочая реплика, а
    Люся вытащила из неё номер дома и приклеила к ней чужой видеоотчёт.
    Ответ на вопрос «какой адрес?» выглядит иначе: в нём нет ничего, кроме
    самого адреса.
    """
    ostatok = inventory.ubrat_adres(text or '', house)
    ostatok = _ADRES_MUSOR.sub(' ', ostatok)
    ostatok = re.sub(r'[^А-Яа-яЁёA-Za-z]+', '', ostatok)
    return len(ostatok) <= 2


def attach_house_to_report(event, record_id, house, text: str):
    """Ответ «Советская 30» на вопрос об адресе — привязывает отчёт к дому.

    Иначе адрес остаётся отдельной строкой в ленте, а видеоотчёт — ничьим.

    Условий три, и все обязательны: сообщение — только адрес, отчёт свежий
    и он того же человека. Без них Люся цепляла ролики к любой реплике,
    где мелькнул номер дома, и каждый раз об этом объявляла.

    Привязывает молча. Это служебное действие, а не новость: увидеть его
    можно в ленте дома, а поправить — обычным «не 28, а 18б».
    """
    if not tolko_adres(text, house):
        return
    chat_id = getattr(event.message.recipient, 'chat_id', None)
    if chat_id is None:
        return
    otchyot = db.orphan_report(chat_id, user_id=_uid(event))
    if not otchyot or otchyot['id'] == record_id:
        return
    db.set_chat_house(otchyot['id'], house['id'])
    log.info('Отчёт %s привязан к дому %s по ответу в чате',
             otchyot['id'], house['address'])


# «сохрани», «запиши», «занеси» — просьба сохранить присланный список
SAVE_TRIGGER = re.compile(
    r'(?<![а-я])(сохран(и|ите|ить)|запиши|запишите|записать|занеси|занесите|'
    r'внеси|внесите)(?![а-я])', re.IGNORECASE)


async def show_plan_screen(msg, punkty: list, vybrano: set):
    """Перерисовывает список на месте, чтобы чат не заваливало копиями."""
    text_ekrana, kb = plan_screen(punkty, vybrano)
    try:
        await msg.edit(text=text_ekrana, attachments=[kb.as_markup()])
    except Exception:
        log.warning('Не вышло обновить список — отправляю заново', exc_info=True)
        await send(msg, text_ekrana, kb)


async def handle_plan_choice(event, text: str, uid: int) -> bool:
    """«Первые 4 пункта сохрани» — выбор словами, а не только галочками.

    Так написал заказчик, и это самый естественный способ. Раньше Люся не
    понимала и пыталась разобрать свой же список заново.
    """
    state = STATE.get(uid)
    if not state or state.get('mode') != 'plan_confirm':
        return False
    vybor = plan.parse_choice(text, len(state['punkty']))
    if vybor is None:
        return False
    # Пункты без дома записать нельзя, как их ни выбирай
    state['vybrano'] = {i for i in vybor if state['punkty'][i]['house']}
    await show_plan_screen(event.message, state['punkty'], state['vybrano'])
    return True


async def resume_passport_house(event, text: str, uid: int) -> bool:
    """Ответ на вопрос «по какому дому записать». True — если разобрались.

    Работает и в личке, и в чате: спросили в чате — там же и отвечают,
    коротким сообщением с адресом, не называя Люсю по имени.
    """
    state = STATE.get(uid)
    if not state or state.get('mode') != 'pass_house':
        return False
    dom = houses.detect_house(text)
    if not dom:
        found = houses.search(text)
        dom = found[0] if len(found) == 1 else None
    if not dom:
        await send(event.message,
                   f'🏠 Не поняла адрес. Напишите, например, «{_primer(0)}».')
        return True
    STATE.pop(uid, None)
    await save_passport_note(event, dom, state['text'], uid)
    return True


async def handle_passport_note(event, text: str, uid: int) -> bool:
    """«Розлив нижний, сталь ДУ50. В паспорт» — сведения ложатся в паспорт дома.

    Дом назван прямо — записываем молча. Не назван — спрашиваем какой и
    ждём ответа: подставлять дом наугад в паспорт нельзя, потом с этим
    поедет бригада.
    """
    if not passport.wants_passport(text):
        return False
    # Сведения могли прислать сообщением выше, а просьбу — ответом на него
    svedeniya = passport.strip_trigger(text)
    citata = quoted_text(event)
    if citata and len(svedeniya) < 15:
        svedeniya = citata
    if len(svedeniya) < 5:
        await send(event.message, '🗂 Что записать в паспорт? Напишите сведения '
                                  'одной фразой или ответьте на нужное сообщение.')
        return True

    dom = houses.detect_house(svedeniya) or (houses.detect_house(citata) if citata else None)
    if not dom:
        STATE[uid] = {'mode': 'pass_house', 'text': svedeniya}
        await send(event.message, '🏠 По какому дому записать? Напишите адрес.')
        return True

    await save_passport_note(event, dom, svedeniya, uid)
    return True


async def save_passport_note(event, dom, svedeniya: str, uid: int):
    """Определяет раздел паспорта и дописывает сведения."""
    razobrano = await passport.pick_field(svedeniya)
    if not razobrano:
        field, znachenie = 'notes', svedeniya
    else:
        field, znachenie = razobrano
    znachenie = mat.mask(znachenie)

    bylo = (db.get_passport(dom['id']) or {}).get(field)
    # Дописываем, а не затираем: в графе может быть чужая работа
    novoe = f'{bylo}\n{znachenie}' if bylo and znachenie not in bylo else znachenie
    db.set_passport_field(dom['id'], field, novoe, _uname(event))

    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text='🗂 Паспорт дома', payload=f"p:{dom['id']}"))
    dopisano = ' (дописала к прежнему)' if bylo and novoe != znachenie else ''
    await send(event.message,
               f"🗂 {dom['address']} → {PASSPORT_LABELS.get(field, field)}"
               f"{dopisano}:\n{znachenie}", kb)


async def handle_shutoff(event, text: str, uid: int) -> bool:
    """«Перекрыл стояк по 105 квартире на 65а/3» — чат узнаёт, кого отключили.

    Сантехник перекрывает по одной квартире, а без воды остаётся весь столб.
    Список квартир Люся берёт из шахматки — человеку остаётся подтвердить
    отправку: сообщение уходит бригаде, и ошибиться в нём дороже, чем нажать
    кнопку.
    """
    razbor_teksta = stoyak_mod.parse(text)
    if not razbor_teksta:
        return False
    chto, dom, kvartira, res = razbor_teksta

    if chto == 'otkryl':
        return await _stoyak_otkryt(event, dom, kvartira, uid, res)

    if not dom or kvartira is None:
        # «перекрыл стояк» без адреса — без него шахматку не поднять
        STATE[uid] = {'mode': 'stoyak_zakryt', 'res': res}
        await send(event.message, '🚫 По какому дому и квартире перекрыли? '
                                  'Напишите, например «65а/3, кв. 105».')
        return True

    naydeno = stoyak_mod.naydi_stoyak(dom['address'], kvartira)
    if not naydeno:
        return await _stoyak_bez_shahmatki(event, dom, kvartira, res, uid, text)
    adres, etazh, nomer, kvartiry = naydeno

    sid = db.add_shutoff(dom['id'], kvartira, nomer, etazh, kvartiry,
                         by_id=uid, by_name=_uname(event), res=res, original=text)
    await _vydat_teksty(event, sid, adres, kvartira, kvartiry, _uname(event),
                        res, dom['id'], nomer, zakryt=True)
    return True


async def _stoyak_bez_shahmatki(event, dom, kvartira: int, res: str,
                                uid: int, text: str) -> bool:
    """Шахматки нет — но текст жильцам всё равно нужен.

    Раньше на оба случая был один ответ: «нет шахматки или в ней нет
    квартиры — напишите в чат сами». Случая два, и они разные.

    Квартиры нет в шахматке — почти всегда ошибка в номере, и подсказать
    надо диапазон. А вот шахматки нет вовсе на семи жилых домах из
    двадцати пяти, и ждать, пока их оцифруют, незачем: список квартир
    назовёт человек, а оформит его Люся — ради этого всё и делалось.
    """
    if somneniya.nezhiloy(dom):
        # Просить у человека список квартир парковки — отдельный вид чуши
        chto = (dom.get('note') or 'нежилое здание').split('.')[0].strip()
        await send(event.message,
                   f"🤔 {dom['address']} — {chto}, квартир там нет. "
                   'Это точно тот дом?')
        return True
    if risers_mod.find_blocks(dom['address']):
        ran = somneniya.diapazon(dom['address'])
        hvost = f' — там квартиры с {ran[0]} по {ran[1]}' if ran else ''
        await send(event.message,
                   f"🤔 В доме {dom['address']} квартиры {kvartira} нет{hvost}. "
                   'Проверьте номер.')
        return True
    STATE[uid] = {'mode': 'stoyak_kvartiry', 'res': res, 'dom_id': dom['id'],
                  'kvartira': kvartira, 'original': text}
    await send(event.message,
               f"🤔 Шахматки на {dom['address']} у меня нет — сама стояк не "
               'посчитаю. Перечислите квартиры стояка через запятую, и я '
               'соберу оба текста: «12, 21, 30, 39».')
    return True


async def resume_stoyak_kvartiry(event, text: str, uid: int, state) -> bool:
    """Квартиры стояка, названные руками, — для домов без шахматки."""
    kvartiry = stoyak_mod.spisok_kvartir(text)
    if not kvartiry:
        await send(event.message, '🚫 Не разобрала номера. Перечислите через '
                                  'запятую: «12, 21, 30, 39».')
        return True
    STATE.pop(uid, None)
    dom = houses.HOUSES_BY_ID.get(state['dom_id'])
    if not dom:
        return True
    kvartira, res = state['kvartira'], state.get('res', 'вода')
    sid = db.add_shutoff(dom['id'], kvartira, 0, 0, kvartiry,
                         by_id=uid, by_name=_uname(event), res=res,
                         original=state.get('original', ''))
    await _vydat_teksty(event, sid, dom['address'], kvartira, kvartiry,
                        _uname(event), res, dom['id'], zakryt=True)
    return True


def _pro_obshchuyu_shahmatku(adres: str) -> str:
    """Предупреждение, если стояк посчитан по схеме, общей на несколько домов.

    Заказчик: «Это разные дома. Но они в одном ЖК». В таблице стояков схема
    у них одна на троих, и список квартир для Седова 71 считается по ней же.
    Пока не сверены планировки, список уходит жильцам под честную оговорку,
    а не как проверенный факт.
    """
    sosedi = risers_mod.sosedi_po_shahmatke(adres)
    if not sosedi:
        return ''
    return ('\n\n⚠️ Шахматка общая на несколько домов: '
            + ', '.join([adres] + sosedi)
            + '. Если планировки разошлись, список квартир может не совпасть — '
              'гляньте перед отправкой.')


async def _vydat_teksty(event, sid, adres, kvartira, kvartiry, kto, res,
                        house_id, nomer=None, zakryt=True, skolko=''):
    """Шапка с кнопками, потом два чистых текста — их пересылают как есть.

    Заказчик пересылает готовое сообщение руками, пока домовые чаты не
    привязаны. Значит, в пересылаемом сообщении не должно быть ни одного
    служебного слова: «вот что напишу» уедет вместе с текстом.
    """
    # Номера стояка нет, когда квартиры назвали руками: дом без шахматки
    chey = f'Стояк {nomer}-й, ' if nomer else 'Стояк с ваших слов: '
    shapka = (f'{chey}{len(kvartiry)} квартир. Ниже два готовых текста — '
              'для обслуживания и для жильцов. Перешлите нужный или отправьте кнопкой.'
              if zakryt else
              f'Стояк был перекрыт {skolko}. Ниже два готовых текста.')
    shapka += _pro_obshchuyu_shahmatku(adres)
    await send(event.message, shapka, _stoyak_kb(sid, house_id, otkryt=not zakryt))
    await send(event.message, stoyak_mod.soobschenie(
        adres, kvartira, kvartiry, kto, db.now()[-5:], zakryt=zakryt,
        skolko=skolko, res=res))
    await send(event.message, stoyak_mod.zhiltsam(
        adres, kvartiry, db.now()[-5:], zakryt=zakryt, res=res))


def _stoyak_kb(sid: int, house_id: int, otkryt: bool = False) -> InlineKeyboardBuilder:
    """Кнопки под черновиком: рабочий чат, домовой чат, отмена."""
    hvost = ':o' if otkryt else ''
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text='📣 В чат обслуживания', payload=f'stsend:{sid}{hvost}'))
    if db.house_chat(house_id):
        kb.row(CallbackButton(text='🏠 И жильцам в чат дома',
                              payload=f'stdom:{sid}{hvost}'))
    if not otkryt:
        kb.row(CallbackButton(text='✍️ Жильцам — с моими словами',
                              payload=f'stwords:{sid}'))
    kb.row(CallbackButton(text='✖️ Не отправлять',
                          payload=f'stdrop:{sid}' if not otkryt else 'menu'))
    return kb


async def _stoyak_otkryt(event, dom, kvartira, uid: int, res: str = 'вода') -> bool:
    """«Открыл стояк» — закрывает запись и сообщает в чат, что вода есть.

    Адрес называть не обязательно: «открыл стояк ещё вчера, забыл сказать» —
    законная фраза. Люся сама напоминала, о каком стояке речь, и если
    перекрытый один, гадать не о чем.
    """
    if dom is None or kvartira is None:
        otkrytye = db.open_shutoffs()
        if not otkrytye:
            await send(event.message, '✅ Перекрытых стояков у меня не записано — '
                                      'значит, всё уже открыто.')
            return True
        if len(otkrytye) == 1:
            zapis = otkrytye[0]
            dom = houses.HOUSES_BY_ID.get(zapis['house_id'])
            kvartira = zapis['flat']
        else:
            STATE[uid] = {'mode': 'stoyak_otkryt',
                          'ids': [z['id'] for z in otkrytye]}
            lines = ['🚫 Перекрытых стояков несколько. Какой открыли?', '']
            for z in otkrytye:
                lines.append(f"• {_adres(z['house_id'])}, кв. {z['flat']}")
            lines.append('')
            lines.append('Напишите адрес и квартиру — например «71 - 1».')
            await send(event.message, '\n'.join(lines))
            return True
    zapis = db.find_shutoff(dom['id'], kvartira) if dom else None
    if not zapis:
        await send(event.message,
                   f"🤔 По {dom['address'] if dom else 'этому дому'} перекрытых "
                   'стояков у меня не записано. Если перекрывали не через меня — '
                   'так и есть, ничего страшного.')
        return True
    kvartiry = [int(x) for x in (zapis['flats'] or '').split(',') if x.strip().isdigit()]
    db.close_shutoff(zapis['id'])
    skolko = stoyak_mod.dlitelnost(_minut_s(zapis['closed_at']))
    res = zapis['res'] or res
    await _vydat_teksty(event, zapis['id'], dom['address'], zapis['flat'], kvartiry,
                        _uname(event), res, dom['id'], zakryt=False, skolko=skolko)
    return True


def _minut_s(kogda: str) -> int:
    """Сколько минут прошло с «ДД.ММ.ГГГГ ЧЧ:ММ»."""
    from datetime import datetime as dt

    try:
        bylo = dt.strptime(kogda, '%d.%m.%Y %H:%M').replace(tzinfo=db.IRKUTSK_TZ)
    except (TypeError, ValueError):
        return 0
    return max(0, int((dt.now(db.IRKUTSK_TZ) - bylo).total_seconds() // 60))


async def _rasshifrovat_lichnoe(event, url: str | None,
                                gotovo: str | None = None) -> str:
    """Расшифровывает голосовое из лички.

    Услышанное вслух не повторяем: человек и так знает, что наговорил, а
    лишние два сообщения засоряют ленту. Расшифровка остаётся в логе — там
    её видно, если Люся вдруг ответит не о том.
    """
    if gotovo:
        log.info('Расшифровку прислал сам MAX: %.80s', gotovo)
        text = gotovo
    else:
        await pechataet(event)
        try:
            text = await transcribe.transcribe_url(url)
        except Exception:
            log.exception('Не удалось расшифровать голосовое из лички')
            text = None
    if not text:
        await send(event.message, '🎙 Не разобрала запись. Попробуйте ещё раз '
                                  'или напишите текстом.')
        return ''
    text = mat.mask(text)
    log.info('Расшифровано голосовое в личке: %.80s', text)
    return text


async def pechataet(event):
    """Показывает «печатает» вместо сообщения-заглушки.

    Расшифровка занимает несколько секунд, и молчать всё это время не
    стоит. Но и писать «слушаю» — значит оставить в переписке строчку,
    которую потом никто не перечитает.
    """
    bot = getattr(event, 'bot', None)
    r = getattr(event.message, 'recipient', None)
    chat_id = getattr(r, 'chat_id', None) if r is not None else None
    if bot is None or not chat_id:
        return
    try:
        await bot.send_action(chat_id=chat_id)
    except Exception:
        log.debug('Не удалось показать «печатает»', exc_info=True)


async def handle_announcement(event, text: str, uid: int) -> bool:
    """«Сделай объявление жильцам» — переписывает наговоренное деловым языком.

    Заказчик наговаривает как думает, а в домовой чат нужно как положено.
    Цифры, адреса и сроки при этом остаются его: модель переписывает тон,
    а не содержание.
    """
    if not announce.wants_announcement(text):
        return False
    await send(event.message, '✍️ Составляю объявление…')
    try:
        gotovo = await announce.sostavit(text)
    except Exception:
        log.exception('Не удалось составить объявление')
        gotovo = None
    if not gotovo:
        await send(event.message,
                   '🤔 Не поняла, что объявить. Наговорите или напишите суть: '
                   'что, где и когда — а я переложу деловым языком.')
        return True

    dom = houses.detect_house(text)
    await pokazat_obyavu(event, uid, gotovo, dom)
    return True


async def pokazat_obyavu(event, uid: int, gotovo: str, dom):
    """Показывает объявление и запоминает его — чтобы можно было поправить.

    Запоминаем всегда, даже когда чат дома не привязан: «убери пункт про
    шахту» человек говорит независимо от того, кто будет отправлять.
    """
    import time as _t

    chat_id = db.house_chat(dom['id']) if dom else None
    STATE[uid] = {'mode': 'obyava', 'text': gotovo, 'chat_id': chat_id,
                  'house_id': dom['id'] if dom else None, 'kogda': _t.monotonic()}
    kb = InlineKeyboardBuilder()
    if chat_id:
        kb.row(CallbackButton(text=f"🏠 Отправить в чат {dom['address']}",
                              payload='obsend'))
        kb.row(CallbackButton(text='✖️ Не отправлять', payload='obdrop'))
        hvost = ''
    else:
        adres = f" по {dom['address']}" if dom else ''
        hvost = (f'\n\n———\nЧат дома{adres} мне не привязан, отправить сама не могу — '
                 'скопируйте текст. Чтобы отправляла я: наберите в том чате '
                 '«/дом Седова 65а/3».')
    await send(event.message, gotovo + hvost, kb if chat_id else None)


PRAVKA_OKNO = 30 * 60      # сколько объявление ещё можно поправить словами


async def handle_pravka_obyavy(event, text: str, uid: int) -> bool:
    """«Убери пункт про шахту» — правит последнее объявление.

    Раньше Люся отвечала, что не умеет редактировать списки: правку
    объявления она принимала за правку плана работ. Текст она составила
    сама — значит, и поправить его должна сама.
    """
    import time as _t

    state = STATE.get(uid)
    if not state or state.get('mode') != 'obyava':
        return False
    if not announce.wants_pravka(text):
        return False
    # Через полчаса «убери» относится уже к чему-то другому
    if _t.monotonic() - state.get('kogda', 0) > PRAVKA_OKNO:
        STATE.pop(uid, None)
        return False
    await send(event.message, '✍️ Правлю…')
    try:
        novoe = await announce.popravit(state['text'], text)
    except Exception:
        log.exception('Не удалось поправить объявление')
        novoe = None
    if not novoe:
        await send(event.message, '🤔 Не получилось поправить. Скажите иначе — '
                                  'например «убери пункт про шахту».')
        return True
    dom = houses.HOUSES_BY_ID.get(state['house_id']) if state.get('house_id') else None
    await pokazat_obyavu(event, uid, novoe, dom)
    return True


async def resume_stoyak(event, text: str, uid: int, state) -> bool:
    """Ответ на вопрос «какой стояк» — коротким «71 - 1» или адресом."""
    dom, kvartira = stoyak_mod.dom_i_kvartira(text)
    if dom is None or kvartira is None:
        await send(event.message, '🚫 Не поняла адрес. Напишите дом и квартиру — '
                                  'например «65а/3, кв. 105».')
        return True
    rezhim = state['mode']
    STATE.pop(uid, None)
    if rezhim == 'stoyak_otkryt':
        return await _stoyak_otkryt(event, dom, kvartira, uid)
    return await handle_shutoff(
        event, f"перекрыл стояк {state.get('res', '')} "
               f"по {kvartira} квартире на {dom['address']}", uid)


async def handle_inventory(event, text: str, uid: int) -> bool:
    """«В инвентарь: мотопомпа, подвал, Седова 71» — вещь встаёт на учёт.

    Разбираем кодом, а не моделью: название вещи должно попасть в опись
    ровно так, как его сказал человек. «Мотопомпа Хонда» — это и есть
    название, переписывать его нельзя, иначе потом не найдётся.
    """
    if not inventory.wants_add(text):
        return False
    razbor = inventory.parse_add(text)
    if not razbor:
        await send(event.message,
                   '🧰 Что записать? Напишите одной строкой: '
                   '«мотопомпа, подвал, Седова 71».')
        return True
    nazvanie, mesto, dom, skolko = razbor
    item_id = db.add_item(mat.mask(nazvanie), mesto or None,
                          dom['id'] if dom else None, skolko,
                          user_id=uid, user_name=_uname(event))
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text='🧰 Вся опись', payload='inv'))
    if dom:
        kb.row(CallbackButton(text=f"🏠 {dom['address']}", payload=f"invh:{dom['id']}"))
    await send(event.message, '🧰 Записала в опись:\n'
               + item_line(db.get_item(item_id)), kb)
    return True


async def resume_inventory(event, text: str, uid: int, state) -> bool:
    """Ответ на «что и где лежит» и на «куда переехало»."""
    from . import houses as houses_mod

    if state['mode'] == 'inv_add':
        razbor = inventory.parse_add(text)
        if not razbor:
            await send(event.message, '🧰 Не разобрала. Напишите вещь и место: '
                                      '«мотопомпа, подвал, Седова 71».')
            return True
        nazvanie, mesto, dom, skolko = razbor
        house_id = dom['id'] if dom else state.get('house_id')
        STATE.pop(uid, None)
        item_id = db.add_item(mat.mask(nazvanie), mesto or None, house_id, skolko,
                              user_id=uid, user_name=_uname(event))
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='➕ Ещё вещь', payload='invadd'),
               CallbackButton(text='🧰 Вся опись', payload='inv'))
        await send(event.message, '🧰 Записала:\n' + item_line(db.get_item(item_id)), kb)
        return True

    it = db.get_item(state['item_id'])
    if not it:
        STATE.pop(uid, None)
        await send(event.message, '🧰 Такой записи уже нет.')
        return True
    dom = houses_mod.detect_house(text)
    mesto = inventory.strip_trigger(text)
    if dom:
        mesto = inventory.ubrat_adres(mesto, dom)
    STATE.pop(uid, None)
    db.move_item(it['id'], dom['id'] if dom else None, mesto or None)
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text='🧰 Вся опись', payload='inv'))
    await send(event.message, '🚚 Переставила:\n' + item_line(db.get_item(it['id'])), kb)
    return True


async def handle_where(event, text: str, uid: int) -> bool:
    """«Где мотопомпа?» — ищем по описи. False — вопрос не про имущество.

    Молчим, когда ничего не нашли: «где Костя» и «где ключи от 22-го» —
    это к людям и к паспорту, туда вопрос и уйдёт дальше по цепочке.
    """
    chto = inventory.chto_ishchut(text)
    if not chto:
        return False
    nashlos = [it for it in db.list_items()
               if inventory.matches(chto, it['name'], it['place'] or '')]
    if not nashlos:
        return False
    lines = [f'🧰 Нашла по описи ({len(nashlos)}):', '']
    kb = InlineKeyboardBuilder()
    for it in nashlos[:8]:
        lines.append(item_line(it))
        kb.row(CallbackButton(text=f"🧰 {it['name'][:32]}", payload=f"invx:{it['id']}"))
    if len(nashlos) > 8:
        lines.append(f'…и ещё {len(nashlos) - 8}.')
    await send(event.message, '\n'.join(lines), kb)
    return True


async def handle_save_plan(event, text: str, uid: int) -> bool:
    """«Люся, это план работ. Сохрани» — раскладывает список по домам в работы.

    Сама запись — после подтверждения человеком. Модель может слепить два
    пункта в один или потерять адрес, и увидеть это надо до того, как в
    работах появятся неверные задачи.
    """
    if not SAVE_TRIGGER.search(text or ''):
        return False
    # Сохраняем то, на что ответили: сам план прислал мастер, а просьба
    # приходит ответом на его сообщение
    plan_text = quoted_text(event)
    if not plan_text:
        return False
    if quoted_is_mine(event):
        return False        # это ответ на её же разбор, а не новый план
    if not plan.looks_like_plan(plan_text):
        return False

    await send(event.message, '📋 Разбираю список…')
    punkty = await plan.parse_plan(plan_text)
    if not punkty:
        await send(event.message,
                   '🤔 Не смогла разложить это на работы. Пришлите списком: '
                   'адрес — что сделать, по строке на пункт.')
        return True

    # По умолчанию отмечено всё, что удалось привязать к дому
    vybrano = {i for i, p in enumerate(punkty) if p['house']}
    STATE[uid] = {'mode': 'plan_confirm', 'punkty': punkty, 'vybrano': vybrano}
    text_ekrana, kb = plan_screen(punkty, vybrano)
    await send(event.message, text_ekrana, kb)
    return True


def plan_screen(punkty: list, vybrano: set):
    """Список пунктов с галочками: что записывать, человек решает сам.

    Модель раскладывает как умеет, и часть пунктов почти всегда лишняя —
    «гидравлика держит» работой не является. Поэтому каждый пункт можно
    снять нажатием, а не переписывать весь список заново.
    """
    lines = [f'📋 Разобрала {len(punkty)} пунктов. Отметьте, что записать:', '']
    kb = InlineKeyboardBuilder()
    for i, p in enumerate(punkty):
        galka = '☑️' if i in vybrano else '⬜️'
        if p['house']:
            lines.append(f"{galka} {i + 1}. {p['house']['address']} — {p['work']}")
            kb.row(CallbackButton(text=f"{galka} {i + 1}. {p['work'][:30]}",
                                  payload=f'plantog:{i}'))
        else:
            nazvan = f" ({p['address']})" if p['address'] else ''
            lines.append(f"⚠️ {i + 1}. дом не опознан{nazvan} — {p['work']}")
    lines.append('')
    lines.append('Можно и словами: «первые 4», «1-4», «кроме 5», «все».')
    kb.row(CallbackButton(text=f'✅ Записать отмеченные ({len(vybrano)})',
                          payload='plansave'))
    kb.row(CallbackButton(text='☑️ Все', payload='planall'),
           CallbackButton(text='⬜️ Снять', payload='plannone'))
    kb.row(CallbackButton(text='✖️ Отмена', payload='plancancel'))
    return '\n'.join(lines), kb


async def handle_reminder(event, text: str, uid: int) -> bool:
    """«Напомни завтра в 9 про опрессовку» — ставит напоминание. True, если взяла.

    Разбираем кодом, а не моделью: время — то место, где догадка недопустима.
    И записываем по-настоящему. Раньше на такую просьбу Люся отвечала что-то
    вежливое, а назавтра молчала: механизма напоминаний по просьбе не было.
    """
    from datetime import datetime

    razobrano = remind.parse_reminder(text, datetime.now(db.IRKUTSK_TZ))
    if not razobrano:
        # Слово «напомни» есть, а срока нет — переспросим, но не промолчим
        if remind.TRIGGER.search(text or ''):
            await send(event.message,
                       '⏰ Скажите когда — «завтра в 9», «в понедельник», '
                       '«через 2 часа», «25 августа». Например:\n'
                       '«напомни завтра в 8 про опрессовку на Седова 71».')
            return True
        return False

    when, o_chyom = razobrano
    seychas = datetime.now(db.IRKUTSK_TZ)
    if when <= seychas:
        await send(event.message,
                   f'⏰ {remind.fmt_when(when, seychas).capitalize()} — это уже прошло. '
                   'Скажите другое время.')
        return True
    if not o_chyom:
        await send(event.message, '⏰ О чём напомнить? Допишите одной фразой.')
        return True

    chat_id = _chat_id(event)
    db.add_reminder(uid, _uname(event), o_chyom,
                    when.strftime('%d.%m.%Y %H:%M'), chat_id=chat_id)
    kuda = 'сюда, в чат' if chat_id else 'вам в личку'
    await send(event.message,
               f'⏰ Поставила на {remind.fmt_when(when, seychas)}: {o_chyom}.\n'
               f'Напишу {kuda}. Посмотреть и отменить — /напоминания.')
    return True


# Обращение по имени в начале или в конце фразы: «Андрей, умница!»,
# «Спасибо, Костя». Если названа не Люся — говорят не с ней
_ZOVUT = re.compile(r'^\s*([А-ЯЁ][а-яё]{2,14})\s*[,!]'
                    r'|[,!]\s*([А-ЯЁ][а-яё]{2,14})\s*[!.)]*$')

# Кого ещё благодарят в рабочем чате, кроме Люси
_LYUDI = re.compile(
    r'(?<![а-я])(мастер\w*|сантехник\w*|электрик\w*|дворник\w*|слесар\w*|'
    r'ребят\w*|парн\w+|пацан\w*|мужик\w+|бригад\w+|звен\w+)(?![а-я])',
    re.IGNORECASE)

_LYUSYA = ('люся', 'люсь', 'люсенька', 'люська')


# Поводы, которые всегда кому-то адресованы. «Доброе утро, мужики» —
# это всем сразу, а вот «спасибо» и «умница» — конкретному человеку
_LICHNOE = ('spasibo', 'pohvala', 'gotovo')


def banter_umestna(event, text: str, povod: str = '') -> bool:
    """Реплика невпопад хуже молчания.

    Жанна поблагодарила мастера, Андрей написал «Андрей, умница!» — и Люся
    влезла с «Спасибо! Учусь у вас». Маша с Костей перешучивались между
    собой — Люся вставила «Всегда рада». Благодарность и похвала всегда
    кому-то адресованы, и чаще всего не ей.
    """
    link = getattr(event.message, 'link', None)
    if link is not None:
        # Ответ или пересылка — это разговор с кем-то другим. К ней обращаются
        # ответом на её же сообщение, а такие сюда не доходят
        return False
    m = _ZOVUT.search(text or '')
    if m and (m.group(1) or m.group(2) or '').lower() not in _LYUSYA:
        return False
    if povod in _LICHNOE and _LYUDI.search(text or ''):
        return False
    return True


def v_letopisi(text: str) -> bool:
    """Легло ли это сообщение в ленту дома — то же условие, что у znachimo.

    Нужно, чтобы «Записала в летопись» было правдой, а не вежливостью:
    без дома запись остаётся в архиве и в летописи её никто не увидит.
    """
    if not text or not WORK_FACT.search(text):
        return False
    return houses.detect_house(text) is not None


async def maybe_banter(event, text: str):
    """Живая реплика в чат — если есть повод и давно не было.

    Всё, что решает «когда», лежит в banter: здесь только проверка, что
    в этом чате Люсе вообще разрешили открывать рот не по делу и что
    говорят действительно с ней.
    """
    chat_id = getattr(event.message.recipient, 'chat_id', None)
    if chat_id is None or not db.banter_on(chat_id):
        return
    povod = banter.pick(text)
    if not povod or not banter_umestna(event, text, povod[0]):
        return
    line = banter.reply(chat_id, text, v_letopisi=v_letopisi(text))
    if line:
        log.info('Реплика в чат %s: %s', chat_id, line)
        await send(event.message, line)


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


def replied_to_me(event) -> bool:
    """Ответили ли на сообщение самой Люси.

    Ответ на её же сообщение — обращение без вариантов: человек нажал
    «Ответить» именно на неё. Молчать в таком случае неприлично.
    """
    link = getattr(event.message, 'link', None)
    if not link:
        return False
    link_type = getattr(getattr(link, 'type', None), 'value', getattr(link, 'type', None))
    if link_type != 'reply':
        return False
    me = BOT_ME.get('user_id')
    sender_id = getattr(getattr(link, 'sender', None), 'user_id', None)
    # Отправителя MAX иногда не присылает: тогда судим по тому, что ответили
    # на сообщение с нашей разметкой — но лучше ответить лишний раз, чем молча
    return me is None or sender_id is None or sender_id == me


def quoted_is_mine(event) -> bool:
    """Точно ли процитировано сообщение самой Люси.

    Отличается от replied_to_me: там при неизвестном отправителе лучше
    ответить лишний раз, а здесь наоборот — сомневаешься, значит чужое.
    Иначе она начнёт разбирать собственные разборы.
    """
    me = BOT_ME.get('user_id')
    link = getattr(event.message, 'link', None)
    sender_id = getattr(getattr(link, 'sender', None), 'user_id', None)
    return bool(me) and sender_id == me


def quoted_text(event) -> str:
    """Текст сообщения, на которое ответили — контекст для ответа."""
    link = getattr(event.message, 'link', None)
    body = getattr(link, 'message', None) if link else None
    return (getattr(body, 'text', None) or '').strip()


def strip_address(text: str) -> tuple[bool, str]:
    """Позвали ли Люсю и что осталось от вопроса без обращения."""
    if not text:
        return False, ''
    m = ADDRESS_RE.match(text)
    if m:
        return True, text[m.end():].strip()
    # обращение в конце: «что по нормативам ГВС, Люся?». Запятая обязательна:
    # в «Мне очень нравится Люся» имя — часть фразы, и вопрос без него теряет смысл
    m = re.search(r'[,—-]\s*@?(люс[яеию]|lusya|lyusya)\s*[?!.]*\s*$', text, re.IGNORECASE)
    if m:
        return True, text[:m.start()].strip(' ,')
    if BOT_ME.get('username') and f"@{BOT_ME['username']}".lower() in text.lower():
        return True, re.sub(f"@{BOT_ME['username']}", '', text, flags=re.IGNORECASE).strip()
    # Имя в любом месте фразы — тоже к ней: «Мне очень нравится Люся»,
    # «надо у Люси спросить». Такое сообщение она молча пропускала, и со
    # стороны это выглядело как невоспитанность. Текст оставляем целиком:
    # своё имя в вопросе ей не мешает
    if NAME_ANYWHERE.search(text):
        return True, text.strip()
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
    if not group:
        zapomnit_dialog(event)
    bot_status.note_update('чат' if group else 'личка')
    if not group:
        log.info('Личка от %s: %.60s', uid, text or '<без текста>')

    # В группах Люся молчит, пока её не позвали по имени: реагировать на каждое
    # сообщение рабочего чата — верный способ, чтобы бота оттуда выгнали.
    if group:
        log.info('Сообщение из чата %s: %.60s',
                 getattr(event.message.recipient, 'chat_id', '?'), text)
        record_chat_message(event, text)
        # Люся спросила адрес — ответ придёт сюда же, обычным сообщением
        if await handle_plan_choice(event, text, uid):
            return
        if await resume_passport_house(event, text, uid):
            return
        # Показания, присланные в чат, попадают в учёт наравне с личкой:
        # сантехник пишет туда, где удобно
        if await handle_readings(event, text, uid):
            return
        addressed, text = strip_address(text)
        otvet_ey = replied_to_me(event)
        if not addressed and not otvet_ey and not mentioned_in_markup(event):
            # Не позвали — но иногда можно и просто по-человечески отозваться
            await maybe_banter(event, text)
            return
        db.upsert_user(uid, _uname(event))
        # Сохранить присланный план и поставить напоминание Люся должна
        # по-настоящему, а не ответить что-то вежливое
        if await handle_passport_note(event, text, uid):
            return
        if await handle_shutoff(event, text, uid):
            return
        if await handle_inventory(event, text, uid):
            return
        # «Где мотопомпа» — если по описи нашлось, отвечаем сразу; если нет,
        # вопрос идёт дальше, к ИИ: «где Костя» описи не касается
        if await handle_where(event, text, uid):
            return
        if await handle_save_plan(event, text, uid):
            return
        if await handle_reminder(event, text, uid):
            return
        # Отвечают на её сообщение — она должна понимать, на какое именно
        vopros = text
        if otvet_ey:
            bylo = quoted_text(event)
            if bylo:
                vopros = (f'Ты писала в чат: «{bylo[:400]}»\n\n'
                          f'{_uname(event)} отвечает на это: {text or "(без текста)"}')
        try:
            # chat_id — чтобы Люся отвечала по этому чату, а не по личной
            # переписке: они у неё были общей памятью
            reply = (await agent.answer(uid, _uname(event), vopros,
                                        chat_id=_chat_id(event))
                     if vopros else None)
        except agent.TooSlow:
            await send(event.message, SLOW_REPLY)
            return
        if not reply:
            # ИИ мог не ответить, но промолчать нельзя: к ней обратились.
            # Если повод понятный — отзовёмся по-человечески, а не отпиской
            povod = banter.pick(text)
            reply = povod[1] if povod else (
                f'{BOT_NAME} на связи 🙂 Спроси что-нибудь по домам, '
                'заявкам или нормативам.')
        await send(event.message, reply)
        return

    if db.upsert_user(uid, _uname(event)):
        await welcome_newcomer(event, uid)

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
        # Голосовое в личку — тот же текст, просто сказанный вслух. Раньше
        # Люся молча пропускала такие сообщения: расшифровка была заведена
        # только для рабочего чата, а заказчик диктует ей за рулём
        body = event.message.body
        # Голосовое могли и переслать: тогда запись лежит во вложенном
        vnutri = peresylka(event.message)
        gotovo = speech_ready(body) or (speech_ready(vnutri) if vnutri else None)
        url = speech_url(body) or (speech_url(vnutri) if vnutri else None)
        # Библиотека вложение могла и не разобрать — тогда читаем сырое
        if not gotovo and not url:
            gotovo, url = maxfix.speech_from_raw(getattr(body, 'mid', None))
        if gotovo or url:
            text = await _rasshifrovat_lichnoe(event, url, gotovo)
            if not text:
                return
        else:
            log.info('Молчу: сообщение без текста. Вложения: %s | сырое: %s',
                     opisat_vlozheniya(body), syroe_soobschenie(event.message))
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

    if await handle_plan_choice(event, text, uid):
        return

    if await resume_passport_house(event, text, uid):
        return

    if state and state['mode'] in ('stoyak_otkryt', 'stoyak_zakryt'):
        if await resume_stoyak(event, text, uid, state):
            return

    if state and state['mode'] == 'stoyak_kvartiry':
        if await resume_stoyak_kvartiry(event, text, uid, state):
            return

    if state and state['mode'] in ('inv_add', 'inv_move'):
        if await resume_inventory(event, text, uid, state):
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
        # Заводим счётчик одной строкой: «ВСХд-15 № 64380455». Номер сразу
        # ложится в своё поле — иначе он навсегда останется частью названия
        label, serial = split_name_serial(text)
        label = label or METER_LABELS[state['kind']]
        m_id = db.add_meter(state['house_id'], state['kind'], label, _uname(event))
        if serial and serial is not CLEAR:
            db.update_meter(m_id, serial=serial)
        db.remember_meter(uid, m_id)
        STATE.pop(uid, None)
        h = houses.HOUSES_BY_ID[state['house_id']]
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='✍️ Внести первое показание', payload=f'mtr:{m_id}'),
               CallbackButton(text='➕ Ещё счётчик', payload=f"mta:{h['id']}"))
        kb.row(CallbackButton(text='✏️ Название и номер', payload=f'mted:{m_id}'),
               CallbackButton(text='🧮 Счётчики дома', payload=f"mt:{h['id']}"))
        nomer = (f'\n🔢 Заводской номер: {serial}' if serial and serial is not CLEAR
                 else '\n🔢 Заводской номер не указан — можно дописать кнопкой.')
        await send(event.message,
                   f"✅ Запомнила: {METER_LABELS[state['kind']]} — «{label}» "
                   f"({h['address']}).{nomer}", kb)
        return

    # Три входа — «Название», «Номер» и общая правка — разбираются одинаково:
    # человек пишет как удобно, а не как спросили. Раньше «ВСХд-15 № 64380455»
    # в ответ на вопрос о названии целиком уезжало в название.
    if state and state['mode'] in ('meter_rename', 'meter_serial', 'meter_edit'):
        STATE.pop(uid, None)
        await apply_meter_edit(event.message, state['meter_id'], text)
        return

    if state and state['mode'] == 'meter_photo':
        url = next((getattr(a.payload, 'url', None)
                    for a in (event.message.body.attachments or [])
                    if getattr(a.payload, 'url', None)), None)
        if not url:
            await send(event.message, '📷 Нужно именно фото. Пришлите снимок счётчика.')
            return
        m = db.get_meter(state['meter_id'])
        await send(event.message, '👀 Смотрю фото…')
        prochitano = await transcribe.read_meter_photo(url)
        await _save_meter_photo(url, m['id'])
        STATE.pop(uid, None)
        if not prochitano:
            await send(event.message,
                       '🤔 С фото ничего не разобрала — бывает, если блики или '
                       'снято под углом. Фото сохранила, номер и показание '
                       'можно вписать кнопками.',
                       InlineKeyboardBuilder().row(
                           CallbackButton(text='🧮 К счётчику', payload=f"mtc:{m['id']}")))
            return
        # Цифры распознаются с ошибками — записываем только после подтверждения
        STATE[uid] = {'mode': 'meter_confirm', 'meter_id': m['id'],
                      'serial': prochitano['serial'], 'value': prochitano['value']}
        lines = ['👀 Вот что разобрала на фото:']
        if prochitano['serial']:
            lines.append(f"🔢 Заводской номер: {prochitano['serial']}")
        if prochitano['value'] is not None:
            lines.append(f"📈 Показание: {fmt_value(prochitano['value'])}")
        lines.append('')
        lines.append('Проверьте по фото: цифры распознаются с ошибками.')
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='✅ Всё верно, записать',
                              payload=f"mtok:{m['id']}"))
        kb.row(CallbackButton(text='✏️ Название и номер', payload=f"mted:{m['id']}"))
        kb.row(CallbackButton(text='✍️ Показание вручную', payload=f"mtr:{m['id']}"))
        await send(event.message, '\n'.join(lines), kb)
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

    if await handle_pravka_obyavy(event, text, uid):
        return

    if await handle_announcement(event, text, uid):
        return

    if await handle_passport_note(event, text, uid):
        return

    if await handle_shutoff(event, text, uid):
        return

    if await handle_inventory(event, text, uid):
        return

    if await handle_where(event, text, uid):
        return

    if await handle_reminder(event, text, uid):
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


async def _obrabotat_podobrannoe(event):
    """Сообщение, которого MAX не прислал в уведомлении, — обычным путём."""
    try:
        await on_text(event)
    except Exception:
        log.exception('Не удалось обработать подобранное сообщение')


maxfix.ON_RECOVERED = _obrabotat_podobrannoe


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
    await run_action(payload, event.message, uid, event)


async def run_action(payload: str, msg, uid: int, event):
    """Открывает экран по его коду.

    Вызывается и с кнопки, и с команды из меню MAX: экран должен быть один
    и тот же, иначе они разъедутся.
    """
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
        kb = InlineKeyboardBuilder()
        # Дом — кнопка, а не строка списка: набирать адрес руками, стоя
        # в подвале, невозможно
        house_buttons(kb, hs)
        kb.row(CallbackButton(text='◀️ К списку ЖК', payload='homes'),
               CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, f'{title} — домов: {len(hs)}. Выберите дом:'
                        if hs else f'{title}: домов пока нет.', kb)

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
        await send(msg, feminine.fix(answer) if answer else
                   '⚠️ Не получилось связаться с ИИ, попробуйте позже '
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
        # Чтобы позвать человека, нужно чем-то поделиться: имя бота под рукой
        if BOT_ME.get('username'):
            lines += ['', f"➕ Позвать нового: дайте ему @{BOT_ME['username']} — "
                          'пусть напишет боту сам. Как напишет, я покажу его здесь '
                          'и попрошу назначить роль.']
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
            vse = db.house_chat_records(h['id'])
            records = [r for r in vse if znachimo(r)]
            boltovnya = len(vse) - len(records)
            lines = [f"💬 Из рабочего чата — {h['address']}", '']
            if not vse:
                lines.append('По этому дому в чате пока ничего не писали.')
            elif not records:
                lines.append('Отчётов и работ по этому дому в чате не было.')
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
            if boltovnya:
                lines.append('')
                lines.append(f'Ещё {boltovnya} сообщений без работ — они в общей '
                             'ленте, командой /chat.')
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
                nomer = '' if m['serial'] else ' · без номера'
                kb.row(CallbackButton(text=f"🧮 {m['label'][:30]}{nomer}",
                                      payload=f"mtc:{m['id']}"))
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
            hint = ('' if h.get('kind') == 'nonres' else
                    '\n\nℹ️ В жилых домах тепло обычно не снимаем: у жильцов '
                    'прямые договоры со сбытовой компанией.')
            await send(msg, f"➕ {h['address']}: какой счётчик добавляем?{hint}", kb)

    elif action == 'mtak':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        kind = parts[2]
        if h and kind in METER_KINDS:
            STATE[uid] = {'mode': 'meter_label', 'house_id': h['id'], 'kind': kind}
            await send(msg, f'📟 {METER_LABELS[kind]}. Напишите одной строкой — '
                            'где стоит и заводской номер:\n'
                            f'{METER_HINTS.get(kind, "")}\n\n'
                            'Номер сама положу в своё поле. Не знаете его — '
                            'напишите только название, допишем потом.')

    elif action == 'mtr':
        m = db.get_meter(int(parts[1]))
        if m:
            db.remember_meter(uid, m['id'])
            h = houses.HOUSES_BY_ID.get(m['house_id'])
            STATE[uid] = {'mode': 'meter_value', 'meter_id': m['id']}
            last = db.meter_readings(m['id'], limit=1)
            last_line = (f"\nПрошлое: {fmt_value(last[0]['value'])} ({fmt_period(last[0]['period'])})"
                         if last else '')
            await send(msg, f"✍️ {h['address'] if h else ''} — {m['label']}.{last_line}\n"
                            'Напишите текущее показание числом (например: 1234,56):')

    elif action == 'rem':
        moi = db.list_reminders(uid)
        if not moi:
            await send(msg, '⏰ Напоминаний нет.\n\n'
                            'Поставить: напишите «напомни завтра в 8 про опрессовку» — '
                            'здесь или в рабочем чате. В чате напомню там же, всем.',
                       main_menu_kb())
            return
        from datetime import datetime
        seychas = datetime.now(db.IRKUTSK_TZ)
        lines = ['⏰ Напоминания:', '']
        kb = InlineKeyboardBuilder()
        for r in moi:
            try:
                when = datetime.strptime(r['due_at'], '%d.%m.%Y %H:%M').replace(
                    tzinfo=db.IRKUTSK_TZ)
                kogda = remind.fmt_when(when, seychas)
            except ValueError:
                kogda = r['due_at']
            gde = ' (в чат)' if r['chat_id'] else ''
            lines.append(f"• {kogda}{gde} — {r['text']}")
            kb.row(CallbackButton(text=f"✖️ {r['text'][:30]}",
                                  payload=f"remx:{r['id']}"))
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '\n'.join(lines), kb)

    elif action == 'remx':
        db.cancel_reminder(int(parts[1]))
        await send(msg, '✖️ Отменила.', InlineKeyboardBuilder().row(
            CallbackButton(text='⏰ Напоминания', payload='rem'),
            CallbackButton(text='🏠 Меню', payload='menu')))

    elif action == 'stwords':
        zapis = db.get_shutoff(int(parts[1]))
        if not zapis or not zapis['original']:
            await send(msg, '🤔 Исходных слов у меня не осталось. Наговорите '
                            'объявление отдельно: «сделай объявление жильцам…».')
            return
        await send(msg, '✍️ Перекладываю вашими словами…')
        kvartiry = [int(x) for x in (zapis['flats'] or '').split(',')
                    if x.strip().isdigit()]
        try:
            gotovo = await announce.sostavit(zapis['original'], kvartiry)
        except Exception:
            log.exception('Не удалось составить объявление по стояку')
            gotovo = None
        if not gotovo:
            await send(msg, '🤔 Не получилось. Текст выше можно переслать как есть.')
            return
        dom = houses.HOUSES_BY_ID.get(zapis['house_id'])
        chat_id = db.house_chat(zapis['house_id'])
        kb = InlineKeyboardBuilder()
        if chat_id:
            STATE[uid] = {'mode': 'obyava', 'text': gotovo, 'chat_id': chat_id,
                          'house_id': zapis['house_id']}
            kb.row(CallbackButton(text=f"🏠 Отправить в чат {dom['address']}"
                                  if dom else '🏠 Отправить жильцам', payload='obsend'))
            kb.row(CallbackButton(text='✖️ Не отправлять', payload='obdrop'))
        await send(msg, gotovo, kb if chat_id else None)

    elif action in ('obsend', 'obdrop'):
        state = STATE.pop(uid, None)
        if not state or state.get('mode') != 'obyava':
            await send(msg, '🤔 Текст уже неактуален, составьте заново.')
            return
        if action == 'obdrop':
            await send(msg, '✖️ Не отправила.', main_menu_kb())
            return
        try:
            await event.bot.send_message(chat_id=state['chat_id'], text=state['text'])
        except Exception:
            log.exception('Не удалось отправить объявление жильцам')
            await send(msg, '⚠️ Не получилось отправить. Скопируйте текст и '
                            'выложите сами.')
            return
        dom = houses.HOUSES_BY_ID.get(state['house_id'])
        kuda = f" — {dom['address']}" if dom else ''
        await send(msg, f'🏠 Отправила жильцам{kuda}.', main_menu_kb())

    elif action in ('stsend', 'stdrop', 'stdom'):
        sid = int(parts[1])
        zapis = db.get_shutoff(sid)
        if not zapis:
            await send(msg, '🤔 Эта запись уже неактуальна.')
            return
        if action == 'stdrop':
            db.delete_shutoff(sid)
            await send(msg, '✖️ Не отправила, запись убрала.', main_menu_kb())
            return
        otkryt = len(parts) > 2 and parts[2] == 'o'
        dom = houses.HOUSES_BY_ID.get(zapis['house_id'])
        kvartiry = [int(x) for x in (zapis['flats'] or '').split(',')
                    if x.strip().isdigit()]
        res = zapis['res'] or 'вода'
        zhiltsam = action == 'stdom'
        chat_id = (db.house_chat(zapis['house_id']) if zhiltsam else db.main_chat())
        if not chat_id:
            await send(msg, '🤔 Чат дома ещё не привязан. Наберите в нём '
                            '«/дом Седова 65а/3» — и я запомню, чей он.'
                       if zhiltsam else
                       '🤔 Я пока не знаю рабочего чата — напишите там '
                       'что-нибудь, и я его запомню.')
            return
        skolko = (stoyak_mod.dlitelnost(_minut_s(zapis['closed_at']))
                  if otkryt else '')
        adres = dom['address'] if dom else '—'
        if zhiltsam:
            # Жильцам — деловым языком, без имён сантехников и без номера
            # квартиры, из-за которой перекрывали
            text_v_chat = stoyak_mod.zhiltsam(adres, kvartiry, db.now()[-5:],
                                              zakryt=not otkryt, res=res)
        else:
            text_v_chat = stoyak_mod.soobschenie(
                adres, zapis['flat'], kvartiry,
                zapis['by_name'] or _uname_cb(event), db.now()[-5:],
                zakryt=not otkryt, skolko=skolko, res=res)
        try:
            await event.bot.send_message(chat_id=chat_id, text=text_v_chat)
        except Exception:
            log.exception('Не удалось отправить объявление о стояке')
            await send(msg, '⚠️ Не получилось отправить. Напишите сами.')
            return
        db.mark_shutoff_announced(sid)
        kb = InlineKeyboardBuilder()
        if not zhiltsam and db.house_chat(zapis['house_id']):
            kb.row(CallbackButton(text='🏠 И жильцам в чат дома',
                                  payload=f'stdom:{sid}' + (':o' if otkryt else '')))
        kb.row(CallbackButton(text='🚫 Перекрытые стояки', payload='stl'),
               CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '🏠 Отправила жильцам в чат дома.' if zhiltsam
                   else '📣 Отправила в чат обслуживания.', kb)

    elif action == 'stl':
        zapisi = db.open_shutoffs()
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        if not zapisi:
            await send(msg, '✅ Перекрытых стояков нет.\n\n'
                            'Напишите мне «перекрыл стояк по 105 квартире на 65а/3» — '
                            'найду весь стояк и объявлю в чат.', kb)
            return
        lines = [f'🚫 Перекрытые стояки — {len(zapisi)}', '']
        for z in zapisi:
            dom = houses.HOUSES_BY_ID.get(z['house_id'])
            skolko = stoyak_mod.dlitelnost(_minut_s(z['closed_at']))
            lines.append(f"• {dom['address'] if dom else '—'}, кв. {z['flat']} — "
                         f"{skolko} назад, {z['by_name'] or '—'}")
        await send(msg, '\n'.join(lines), kb)

    elif action == 'chk':
        if _role(uid) not in ('admin', 'engineer', 'director'):
            await send(msg, '🔍 Проверка записей — для руководства и инженера.')
            return
        nayden = proverka.podozritelnye(db.all_chat_records(limit=1000))
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        if not nayden:
            await send(msg, '✅ Записей с чужими адресами не нашла.\n\n'
                            'Проверяю всё, что похоже на «улица номер», и '
                            'оставляю то, чего нет в справочнике домов.', kb)
            return
        lines = [f'🔍 Записи с чужими адресами — {len(nayden)}', '',
                 '❗ «нет в справочнике» — скорее всего, модель дописала сама.',
                 '⚠️ «не в работе» — дом есть, но участок не наш.', '']
        kb = InlineKeyboardBuilder()
        for r, adresa in nayden[:10]:
            chto = (r['transcript'] or r['text'] or '').strip()
            lines.append(f"▪️ {r['created_at']} · {r['user_name'] or '—'}")
            for adres, vid in adresa:
                znak = '❗' if vid == proverka.VYDUMKA else '⚠️'
                lines.append(f'   {znak} {adres} — {vid}')
            lines.append(f"   {chto[:160]}")
            lines.append('')
            imena = ', '.join(a for a, _ in adresa)
            kb.row(CallbackButton(text=f"🗑 {r['created_at'][:10]} · {imena[:24]}",
                                  payload=f"chkdel:{r['id']}"))
        if len(nayden) > 10:
            lines.append(f'… и ещё {len(nayden) - 10}.')
        kb.row(CallbackButton(text='🔍 Проверить заново', payload='chk'),
               CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '\n'.join(lines), kb)

    elif action == 'chkdel':
        if _role(uid) not in ('admin', 'engineer', 'director'):
            return
        rec = db.get_chat_record(int(parts[1]))
        if not rec:
            await send(msg, '🤔 Эта запись уже удалена.')
            return
        db.delete_chat_record(rec['id'])
        log.info('Удалена запись ленты %s по проверке адресов', rec['id'])
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='🔍 Остальные', payload='chk'),
               CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, f"🗑 Удалила запись от {rec['created_at']}.", kb)

    elif action == 'jrnl':
        from datetime import datetime as dt, timedelta
        seychas = dt.now(db.IRKUTSK_TZ)
        vchera = len(parts) > 1 and parts[1] == 'v'
        den = (seychas - timedelta(days=1) if vchera else seychas).strftime('%d.%m.%Y')
        lines = journal_lines(den)
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='📅 За сегодня', payload='jrnl'),
               CallbackButton(text='📅 За вчера', payload='jrnl:v'))
        kb.row(CallbackButton(text='📆 Итоги по домам', payload='itogi' + (':v' if vchera else '')),
               CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '\n'.join(lines), kb)

    elif action == 'golos':
        if is_group(event):
            await send(msg, '🎙 Страница записи личная — напишите мне «голос» '
                            'в личку, пришлю вашу ссылку.')
            return
        ssylka = golos_mod.ssylka(uid)
        if not ssylka:
            await send(msg, '🎙 Публичный адрес приложения не настроен — '
                            'страницу записи отдать не могу.')
            return
        kb = InlineKeyboardBuilder()
        kb.row(LinkButton(text='🎙 Открыть страницу записи', url=ssylka))
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg,
                   '🎙 Ваша страница записи:\n' + ssylka + '\n\n'
                   'MAX не отдаёт ботам голосовые, поэтому голос идёт мимо него. '
                   'Откройте страницу, нажмите «Говорить», наговорите и нажмите '
                   'ещё раз — я расшифрую и сделаю то же, что сделала бы по '
                   'тексту. Ответ придёт и на странице, и сюда.\n\n'
                   '💡 Добавьте её на домашний экран телефона — тогда до кнопки '
                   'одно касание. Ссылка личная, никому не давайте.', kb)

    elif action == 'itogi':
        if _role(uid) not in ('admin', 'engineer', 'director'):
            await send(msg, '📆 Итоги дня собираю для руководства и инженера.')
            return
        from datetime import datetime as dt, timedelta
        seychas = dt.now(db.IRKUTSK_TZ)
        vchera = len(parts) > 1 and parts[1] == 'v'
        den = (seychas - timedelta(days=1) if vchera else seychas).strftime('%d.%m.%Y')
        zanovo = len(parts) > 1 and parts[1] == 'z'
        if zanovo:
            den = parts[2] if len(parts) > 2 else den

        kb = InlineKeyboardBuilder()
        if not zanovo and db.day_already_parsed(razbor._iso(den)):
            kb.row(CallbackButton(text='🔄 Разобрать заново', payload=f'itogi:z:{den}'))
            kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
            await send(msg, razbor.svodka_iz_bazy(den), kb)
            return

        await send(msg, f'📆 Читаю ленту за {den}. Это до трёх минут — '
                        'модель перечитывает день целиком.')
        try:
            itog = await razbor.razobrat_den(den)
        except Exception:
            log.exception('Разбор дня не удался')
            await send(msg, '⚠️ Не получилось разобрать день. Смотрю, в чём дело.')
            return
        if not itog:
            await send(msg, '📆 Модель не ответила. Попробуйте ещё раз.')
            return
        n = razbor.sohranit(den, itog)
        kb.row(CallbackButton(text='📆 За вчера', payload='itogi:v'),
               CallbackButton(text='🏠 Меню', payload='menu'))
        if not n:
            await send(msg, f'📆 За {den} в ленте не нашлось ничего, что стоит '
                            'записать в хронику домов.', kb)
            return
        await send(msg, razbor.svodka(den, itog), kb)

    elif action == 'fl':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if not h:
            return
        zametki = db.flat_notes(h['id'], limit=60)
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='🔧 Техника дома', payload=f"tech:{h['id']}"),
               CallbackButton(text='🏠 Меню', payload='menu'))
        if not zametki:
            await send(msg, f"🚪 {h['address']}: находок по квартирам пока нет.\n\n"
                            'Записываются сами: напишите или наговорите в чат '
                            'адрес, квартиру и что нашли — «71/1, 105 квартира, '
                            'подмес».', kb)
            return
        po_kvartiram = {}
        for z in zametki:
            po_kvartiram.setdefault(z['flat'], []).append(z)
        lines = [f"🚪 {h['address']} — находки по квартирам "
                 f'({len(zametki)} в {len(po_kvartiram)} кв.)', '']
        for kv in sorted(po_kvartiram):
            spisok = po_kvartiram[kv]
            znak = '⚠️ ' if len(spisok) > 1 else ''
            lines.append(f'{znak}кв. {kv}:')
            for z in spisok[:4]:
                lines.append(f"   {z['created_at'][:10]} — {z['text'][:100]} "
                             f"({z['author'] or '—'})")
        await send(msg, '\n'.join(lines), kb)

    elif action == 'inv':
        veshchi = db.list_items()
        kb = InlineKeyboardBuilder()
        if not veshchi:
            kb.row(CallbackButton(text='➕ Записать вещь', payload='invadd'))
            kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
            await send(msg, '🧰 Опись пуста.\n\n'
                            'Записать можно прямо из чата одной строкой:\n'
                            '«в инвентарь: мотопомпа, подвал, Седова 71».\n'
                            'Потом достаточно спросить «где мотопомпа».', kb)
            return
        # Группируем по месту: человек ищет «что есть на этом адресе»,
        # а не «где лежит вещь номер 12»
        po_mestu = {}
        for it in veshchi:
            dom = houses.HOUSES_BY_ID.get(it['house_id']) if it['house_id'] else None
            po_mestu.setdefault(dom['address'] if dom else 'Без адреса', []).append(it)
        lines = [f'🧰 ОПИСЬ — {len(veshchi)} позиций', '']
        for adres in sorted(po_mestu):
            lines.append(f'📍 {adres}')
            for it in po_mestu[adres]:
                skolko = f" ×{it['qty']}" if it['qty'] > 1 else ''
                mesto = f" — {it['place']}" if it['place'] else ''
                lines.append(f"   • {it['name']}{skolko}{mesto}")
            lines.append('')
        lines.append('Спросить можно словами: «где мотопомпа».')
        for it in veshchi[:8]:
            kb.row(CallbackButton(text=f"🧰 {it['name'][:32]}", payload=f"invx:{it['id']}"))
        kb.row(CallbackButton(text='➕ Записать вещь', payload='invadd'),
               CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '\n'.join(lines), kb)

    elif action == 'invadd':
        STATE[uid] = {'mode': 'inv_add'}
        await send(msg, '🧰 Что и где лежит? Одной строкой:\n'
                        '«мотопомпа, подвал, Седова 71»\n'
                        '«2 тепловые пушки, бытовка»\n\n'
                        'Адрес можно не писать — тогда запишу без привязки к дому.')

    elif action == 'invh':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if not h:
            return
        veshchi = db.list_items(house_id=h['id'])
        kb = InlineKeyboardBuilder()
        for it in veshchi[:8]:
            kb.row(CallbackButton(text=f"🧰 {it['name'][:32]}", payload=f"invx:{it['id']}"))
        kb.row(CallbackButton(text='➕ Записать сюда', payload=f"invaddh:{h['id']}"))
        kb.row(CallbackButton(text='🔧 Техника дома', payload=f"tech:{h['id']}"),
               CallbackButton(text='🏠 Меню', payload='menu'))
        if not veshchi:
            await send(msg, f"🧰 {h['address']}: в описи по этому дому пусто.", kb)
            return
        lines = [f"🧰 {h['address']} — что здесь лежит ({len(veshchi)}):", '']
        for it in veshchi:
            lines.append('• ' + item_line(it, s_adresom=False)[2:].strip())
        await send(msg, '\n'.join(lines), kb)

    elif action == 'invaddh':
        h = houses.HOUSES_BY_ID.get(int(parts[1]))
        if h:
            STATE[uid] = {'mode': 'inv_add', 'house_id': h['id']}
            await send(msg, f"🧰 {h['address']}: что здесь лежит? Напишите вещь и "
                            'место, например «мотопомпа, подвал».')

    elif action == 'invx':
        it = db.get_item(int(parts[1]))
        if not it:
            await send(msg, '🧰 Такой записи уже нет.')
            return
        dom = houses.HOUSES_BY_ID.get(it['house_id']) if it['house_id'] else None
        lines = [f"🧰 {it['name']}" + (f" ×{it['qty']}" if it['qty'] > 1 else ''), '']
        lines.append(f"📍 {dom['address'] if dom else 'без привязки к дому'}")
        if it['place']:
            lines.append(f"   {it['place']}")
        if it['note']:
            lines.append(f"📝 {it['note']}")
        lines.append('')
        lines.append(f"Записал: {it['added_by_name'] or '—'}, {it['created_at']}")
        if it['updated_at'] != it['created_at']:
            lines.append(f"Обновлено: {it['updated_at']}")
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='🚚 Переехало', payload=f"invm:{it['id']}"),
               CallbackButton(text='🗑 Списать', payload=f"invoff:{it['id']}"))
        kb.row(CallbackButton(text='🧰 Вся опись', payload='inv'),
               CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '\n'.join(lines), kb)

    elif action == 'invm':
        it = db.get_item(int(parts[1]))
        if not it:
            await send(msg, '🧰 Такой записи уже нет.')
            return
        STATE[uid] = {'mode': 'inv_move', 'item_id': it['id']}
        await send(msg, f"🚚 Куда переехала «{it['name']}»? Напишите новое место, "
                        'например «подвал, Седова 71» или «бытовка».')

    elif action == 'invoff':
        it = db.get_item(int(parts[1]))
        if not it:
            await send(msg, '🧰 Такой записи уже нет.')
            return
        db.write_off_item(it['id'])
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='🧰 Вся опись', payload='inv'),
               CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, f"🗑 «{it['name']}» убрала из описи.", kb)

    elif action == 'plist':
        zapolneno, pusto = [], []
        for h in houses.HOUSES:
            data = db.get_passport(h['id']) or {}
            n = sum(1 for key, _ in PASSPORT_FIELDS if data.get(key))
            (zapolneno if n else pusto).append((h, n))
        vsego = len(PASSPORT_FIELDS)
        lines = [f'🗂 ПАСПОРТА ДОМОВ — {len(zapolneno)} из {len(houses.HOUSES)} начаты', '']
        if zapolneno:
            for h, n in sorted(zapolneno, key=lambda x: -x[1]):
                lines.append(f"▪️ {h['address']} — {n}/{vsego}")
            lines.append('')
        if pusto:
            lines.append(f'▫️ Пустые ({len(pusto)}):')
            lines.append(', '.join(h['address'] for h, _ in pusto))
            lines.append('')
        lines.append('Заполнять проще всего из чата: напишите сведения и '
                     'добавьте «в паспорт» — разложу по разделам сама.')
        kb = InlineKeyboardBuilder()
        for h, n in sorted(zapolneno, key=lambda x: -x[1])[:10]:
            kb.row(CallbackButton(text=f"🗂 {h['address']} ({n}/{vsego})",
                                  payload=f"p:{h['id']}"))
        kb.row(CallbackButton(text='🏘 Все дома', payload='homes'),
               CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '\n'.join(lines), kb)

    elif action in ('plantog', 'planall', 'plannone'):
        state = STATE.get(uid)
        if not state or state.get('mode') != 'plan_confirm':
            await send(msg, '🤔 Список уже неактуален. Пришлите заново.')
            return
        punkty, vybrano = state['punkty'], state['vybrano']
        if action == 'plantog':
            i = int(parts[1])
            if punkty[i]['house']:
                vybrano.symmetric_difference_update({i})
        elif action == 'planall':
            vybrano.clear()
            vybrano.update(i for i, p in enumerate(punkty) if p['house'])
        else:
            vybrano.clear()
        await show_plan_screen(msg, punkty, vybrano)

    elif action == 'plansave':
        state = STATE.pop(uid, None)
        if not state or state.get('mode') != 'plan_confirm':
            await send(msg, '🤔 Список уже неактуален. Пришлите заново.')
            return
        vybrano = state.get('vybrano', set())
        zapisano = []
        for i, p in enumerate(state['punkty']):
            if not p['house'] or i not in vybrano:
                continue
            db.add_work(p['house']['id'], p['work'], None, _uname_cb(event), uid)
            zapisano.append(f"• {p['house']['address']} — {p['work']}")
        if not zapisano:
            await send(msg, '⬜️ Ничего не отмечено — записывать нечего.')
            return
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='📅 Все работы', payload='wl'))
        await send(msg, f'✅ Записала в работы ({len(zapisano)}):\n'
                        + '\n'.join(zapisano)
                        + '\n\nСроки и исполнителей проставьте в «Все работы».', kb)

    elif action == 'plancancel':
        STATE.pop(uid, None)
        await send(msg, '✖️ Не записала.')

    elif action == 'kopiya':
        if _role(uid) not in ('admin', 'engineer'):
            await send(msg, '🗄 Копию базы отдаю админу и инженеру.')
            return
        await send(msg, '🗄 Собираю копию…')
        try:
            data, name = await asyncio.to_thread(backup.make_archive)
            media = await event.bot.upload_media(InputMediaBuffer(
                buffer=data, filename=name, type=UploadType.FILE))
            await msg.answer(
                text='🗄 Готово. Внутри база и папка «Дома» — по заметке на дом '
                     'в Markdown: паспорт, счётчики, манометры, работы, заявки '
                     'и что говорили в чате. Папку можно положить в Obsidian.',
                attachments=[media])
        except Exception:
            log.exception('Не удалось собрать резервную копию')
            await send(msg, '⚠️ Не получилось собрать копию. Смотрю, в чём дело.')

    elif action == 'mtxls':
        if _role(uid) not in BRIEFING_ROLES:
            await send(msg, '📊 Выгрузка доступна инженеру и руководству.')
            return
        period = parts[1] if len(parts) > 1 else current_period()
        await send(msg, '📊 Собираю таблицу…')
        try:
            data = report.meters_workbook(period, fmt_period(period))
            name = f'Показания_{period}.xlsx'
            uploaded = await event.bot.upload_media(
                InputMediaBuffer(buffer=data, filename=name, type=UploadType.FILE))
            await msg.answer(text=f'📊 Показания за {fmt_period(period)}. '
                                  'Жёлтым — счётчики без показаний за этот месяц.',
                             attachments=[uploaded])
        except Exception:
            log.exception('Не удалось выгрузить показания в Excel')
            await send(msg, '⚠️ Не получилось собрать файл. Уже смотрю, в чём дело.')

    elif action == 'mtc':
        m = db.get_meter(int(parts[1]))
        if m:
            db.remember_meter(uid, m['id'])
            h = houses.HOUSES_BY_ID.get(m['house_id'])
            rs = db.meter_readings(m['id'], limit=1)
            lines = [f"🧮 {METER_LABELS.get(m['kind'], '📟')} — {m['label']}",
                     f"🏠 {h['address'] if h else '?'}",
                     f"🔢 Заводской номер: {m['serial'] or '— не указан'}"]
            if rs:
                lines.append(f"📈 Последнее: {fmt_value(rs[0]['value'])} "
                             f"({fmt_period(rs[0]['period'])}, {rs[0]['submitted_by_name'] or '—'})")
            else:
                lines.append('📈 Показаний ещё нет')
            if m['photo']:
                lines.append('📷 Фото есть')
            snyat = m['status'] == db.METER_REMOVED
            lines.append('')
            if snyat:
                lines.append(f"🔧 СНЯТ НА ПОВЕРКУ {m['status_at'] or ''}"
                             + (f", снял {m['status_by']}" if m['status_by'] else ''))
                lines.append('На месте прибора нет. Пока не вернёте — показания '
                             'по нему не записываются.')
            else:
                lines.append('✅ На месте' + (f" с {m['status_at']}, поставил {m['status_by']}"
                                              if m['status_at'] else ''))
            kb = InlineKeyboardBuilder()
            if snyat:
                kb.row(CallbackButton(text='✅ Поставлен на место',
                                      payload=f"mtback:{m['id']}"))
            else:
                kb.row(CallbackButton(text='✍️ Показание', payload=f"mtr:{m['id']}"),
                       CallbackButton(text='📈 История', payload=f"mth:{m['id']}"))
                kb.row(CallbackButton(text='🔧 Снять на поверку',
                                      payload=f"mtoff:{m['id']}"))
            kb.row(CallbackButton(text='✏️ Название и номер', payload=f"mted:{m['id']}"))
            # У приборов, заведённых до разделения полей, номер сидит внутри
            # названия — вынести его должно быть одним нажатием, а не правкой
            if not m['serial'] and split_name_serial(m['label'])[1]:
                kb.row(CallbackButton(text='🔢 Вынести номер из названия',
                                      payload=f"mtfix:{m['id']}"))
            kb.row(CallbackButton(text='📷 Прислать фото — прочту сама',
                                  payload=f"mtph:{m['id']}"))
            kb.row(CallbackButton(text='🧮 Счётчики дома', payload=f"mt:{m['house_id']}"))
            await send(msg, '\n'.join(lines), kb)

    elif action == 'mtoff':
        m = db.get_meter(int(parts[1]))
        if m:
            db.meter_remove(m['id'], uid, _uname_cb(event))
            h = houses.HOUSES_BY_ID.get(m['house_id'])
            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text='🧮 К счётчику', payload=f"mtc:{m['id']}"))
            await send(msg, f"🔧 Записала: «{m['label']}» снят на поверку "
                            f"{db.now()}, снял {_uname_cb(event)}.\n"
                            f"{h['address'] if h else ''}: пока прибор не вернут, "
                            'он будет помечен в списках красным.', kb)

    elif action == 'mtback':
        m = db.get_meter(int(parts[1]))
        if m:
            # Отдельное подтверждение: «сказали поставлен, а он в столярке»
            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text='✅ Да, стоит на месте',
                                  payload=f"mtback2:{m['id']}"))
            kb.row(CallbackButton(text='◀️ Нет, ещё не ставили',
                                  payload=f"mtc:{m['id']}"))
            await send(msg, f"Подтвердите: «{m['label']}» действительно установлен "
                            'на место?\n\nПодпись останется в журнале — по ней потом '
                            'видно, кто ставил и когда.', kb)

    elif action == 'mtback2':
        m = db.get_meter(int(parts[1]))
        if m:
            db.meter_install(m['id'], uid, _uname_cb(event))
            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text='✍️ Внести показание', payload=f"mtr:{m['id']}"))
            kb.row(CallbackButton(text='🧮 К счётчику', payload=f"mtc:{m['id']}"))
            await send(msg, f"✅ «{m['label']}» на месте с {db.now()}, "
                            f'поставил {_uname_cb(event)}.', kb)

    elif action == 'mtfix':
        m = db.get_meter(int(parts[1]))
        if m:
            db.remember_meter(uid, m['id'])
            await apply_meter_edit(msg, m['id'], m['label'])

    # mtren и mtsn остались от прежних двух кнопок: они ещё висят в ленте
    # у людей, и нажатие не должно упираться в тишину
    elif action in ('mted', 'mtren', 'mtsn'):
        m = db.get_meter(int(parts[1]))
        if m:
            db.remember_meter(uid, m['id'])
            STATE[uid] = {'mode': 'meter_edit', 'meter_id': m['id']}
            await send(msg,
                       f"✏️ {m['label']}\n"
                       f"🔢 Заводской номер: {m['serial'] or '— не указан'}\n\n"
                       'Напишите одной строкой — название и номер:\n'
                       '«ВСХд-15 № 64380455»\n\n'
                       'Можно и по отдельности: одно название («ХВС домовой») '
                       'или одни цифры («64380455») — я разберу, где что. '
                       '«-» очистит номер.')

    elif action == 'mtph':
        m = db.get_meter(int(parts[1]))
        if m:
            STATE[uid] = {'mode': 'meter_photo', 'meter_id': m['id']}
            await send(msg, '📷 Пришлите фото счётчика — так, чтобы читались '
                            'заводской номер и табло.\n'
                            'Я прочту и покажу, что разобрала: подтвердите или поправьте.')

    elif action == 'mtok':
        m = db.get_meter(int(parts[1]))
        state = STATE.get(uid) or {}
        if m and state.get('mode') == 'meter_confirm' and state.get('meter_id') == m['id']:
            STATE.pop(uid, None)
            itog = []
            if state.get('serial'):
                db.update_meter(m['id'], serial=state['serial'])
                itog.append(f"номер {state['serial']}")
            if state.get('value') is not None:
                delta, warning = check_anomaly(m['id'], state['value'])
                db.add_reading(m['id'], state['value'], current_period(),
                               uid, _uname_cb(event) or 'ИИ по фото')
                itog.append(f"показание {fmt_value(state['value'])}")
                if warning:
                    itog.append(f'⚠️ {warning}')
            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text='🧮 К счётчику', payload=f"mtc:{m['id']}"))
            await send(msg, '✅ Записала: ' + ', '.join(itog) + '.' if itog
                       else 'Нечего записывать.', kb)
        else:
            await send(msg, '🤔 Это подтверждение уже неактуально — пришлите фото заново.')

    elif action == 'mtyes':
        m = db.get_meter(int(parts[1]))
        if m:
            stroka = await record_reading(event, m, float(parts[2]), uid)
            await send(msg, stroka, InlineKeyboardBuilder().row(
                CallbackButton(text='📈 История', payload=f"mth:{m['id']}"),
                CallbackButton(text='🧮 К счётчику', payload=f"mtc:{m['id']}")))

    elif action == 'mtdel':
        m = db.get_meter(int(parts[1]))
        rs = db.meter_readings(m['id'], limit=1) if m else []
        if rs:
            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text='🗑 Да, удалить',
                                  payload=f"mtdel2:{m['id']}:{rs[0]['id']}"))
            kb.row(CallbackButton(text='◀️ Отмена', payload=f"mth:{m['id']}"))
            await send(msg, f"Удалить последнее показание: {fmt_value(rs[0]['value'])} "
                            f"({fmt_period(rs[0]['period'])}, {rs[0]['submitted_by_name'] or '—'})?\n"
                            'Остальные записи останутся.', kb)

    elif action == 'mtdel2':
        db.delete_reading(int(parts[2]))
        log.info('Пользователь %s удалил показание %s', uid, parts[2])
        await send(msg, '🗑 Удалила.', InlineKeyboardBuilder().row(
            CallbackButton(text='📈 История', payload=f"mth:{parts[1]}"),
            CallbackButton(text='🧮 К счётчику', payload=f"mtc:{parts[1]}")))

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
            kb.row(CallbackButton(text='✍️ Новое показание', payload=f"mtr:{m['id']}"))
            if rs:
                # Ошибочную запись надо чем-то убирать: одна неверная цифра
                # портит и расход, и выгрузку для сбытовой
                kb.row(CallbackButton(text='🗑 Удалить последнее',
                                      payload=f"mtdel:{m['id']}"))
            kb.row(CallbackButton(text='🧮 Счётчики дома', payload=f"mt:{m['house_id']}"))
            await send(msg, '\n'.join(lines), kb)

    elif action == 'mtpick':
        # Показания подают ежедневно, поэтому дом выбирается кнопкой,
        # а не набором адреса руками
        kb = InlineKeyboardBuilder()
        # Карточка прибора уезжает вверх по ленте, и человек ищет её глазами.
        # Возврат к последнему — первой кнопкой, до всех домов
        posledniy = db.last_meter(uid)
        if posledniy:
            dom = houses.HOUSES_BY_ID.get(posledniy['house_id'])
            kb.row(CallbackButton(
                text=f"↩️ {posledniy['label']}" + (f" · {dom['address']}" if dom else ''),
                payload=f"mtc:{posledniy['id']}"))
        if _role(uid) in BRIEFING_ROLES:
            kb.row(CallbackButton(text='📊 Сводка за месяц', payload='mtall'))
        snyato = len(db.removed_meters())
        if snyato:
            kb.row(CallbackButton(text=f'🔧 Снято на поверку: {snyato}',
                                  payload='mtoffl'))
        house_buttons(kb, houses.HOUSES, payload='mt', counts=db.houses_with_meters())
        kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '🧮 Счётчики — выберите дом.\n'
                        '➕ значит, что счётчики там ещё не заведены.\n\n'
                        '💡 Показание можно прислать и просто сообщением: '
                        f'«{_primer(0)} хвс 1234».', kb)

    elif action == 'mtoffl':
        snyatye = db.removed_meters()
        if not snyatye:
            await send(msg, '✅ Снятых на поверку счётчиков нет — все на местах.',
                       InlineKeyboardBuilder().row(
                           CallbackButton(text='🧮 Счётчики', payload='mtpick')))
            return
        lines = [f'🔧 СНЯТЫ НА ПОВЕРКУ — {len(snyatye)}', '']
        kb = InlineKeyboardBuilder()
        for m in snyatye:
            h = houses.HOUSES_BY_ID.get(m['house_id'])
            lines.append(f"• {h['address'] if h else '?'} — {m['label']}\n"
                         f"   снял {m['status_by'] or '—'}, {m['status_at'] or '—'}")
            kb.row(CallbackButton(text=f"✅ Поставлен: {m['label'][:26]}",
                                  payload=f"mtback:{m['id']}"))
        kb.row(CallbackButton(text='🧮 Счётчики', payload='mtpick'))
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
                lines.append(f"   {METER_LABELS.get(r['kind'], '📟').split()[0]} {r['label']}: "
                             f"{fmt_value(r['value'])} ({r['submitted_by_name'] or '—'})")
            missing = [houses.HOUSES_BY_ID[hid]['address'] for hid in with_meters
                       if hid not in submitted_houses and hid in houses.HOUSES_BY_ID]
            if missing:
                lines.append('')
                lines.append('⏳ Ещё не сдали: ' + ', '.join(sorted(missing)))
        kb = InlineKeyboardBuilder()
        # Из сводки должен быть выход к делу, а не только «Меню»
        for hid in list(with_meters)[:8]:
            if hid in submitted_houses or hid not in houses.HOUSES_BY_ID:
                continue
            kb.row(CallbackButton(text=f"✍️ {houses.HOUSES_BY_ID[hid]['address'][:32]}",
                                  payload=f"mt:{hid}"))
        kb.row(CallbackButton(text='📊 Выгрузить в Excel', payload=f'mtxls:{period}'))
        kb.row(CallbackButton(text='🧮 Все дома', payload='mtpick'),
               CallbackButton(text='🏠 Меню', payload='menu'))
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
