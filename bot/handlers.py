"""Обработчики бота «Помощник сантехника» УК Жемчужина (мессенджер MAX)."""
import json
import logging
import os

from maxapi import Dispatcher
from maxapi.types import (
    BotStarted,
    CallbackButton,
    Command,
    CommandStart,
    LinkButton,
    MessageCallback,
    MessageCreated,
)
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from . import db, houses

log = logging.getLogger(__name__)
dp = Dispatcher()

with open(os.path.join(houses.DATA_DIR, 'directory.json'), encoding='utf-8') as f:
    DIRECTORY = json.load(f)['sections']

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
    """Отправляет текст (при необходимости частями), клавиатуру цепляет к последней части."""
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


def main_menu_kb() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text='🔍 Найти дом', payload='srch'),
           CallbackButton(text='🏘 Дома по звеньям', payload='zv'))
    kb.row(CallbackButton(text='📋 Заявки', payload='rl'),
           CallbackButton(text='➕ Новая заявка', payload='nr'))
    kb.row(CallbackButton(text='📖 Справочник', payload='dir'))
    return kb


MAIN_TEXT = (
    '👋 Помощник сантехника УК «Жемчужина»\n\n'
    'Что умею:\n'
    '• 🔍 Поиск дома — какое звено обслуживает адрес, точка на карте\n'
    '• 🗂 Паспорт дома — техданные: розливы, арматура, доступ\n'
    '• 📋 Заявки — приём и учёт: новая → в работе → выполнена\n'
    '• 📖 Справочник — телефоны, нормативы, сроки, трубы\n\n'
    '💡 Просто напишите адрес (например: «Розы Люксембург 118/5») — я найду дом.'
)


# ---------- Карточки ----------

def house_card_text(h) -> str:
    zv = houses.ZVENO_NAMES.get(h['zveno'], f"Звено {h['zveno']}")
    return (f"🏠 {h['address']}\n"
            f"👷 {zv}\n"
            f"📊 Заявок за год: {h['requests_year']}")


def house_card_kb(h) -> InlineKeyboardBuilder:
    gis, ya = houses.map_links(h)
    kb = InlineKeyboardBuilder()
    kb.row(LinkButton(text='🗺 2ГИС', url=gis),
           LinkButton(text='🗺 Яндекс', url=ya))
    kb.row(CallbackButton(text='🗂 Паспорт дома', payload=f"p:{h['id']}"),
           CallbackButton(text='➕ Заявка сюда', payload=f"nrh:{h['id']}"))
    kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
    return kb


def passport_text(h) -> str:
    data = db.get_passport(h['id'])
    lines = [f"🗂 ПАСПОРТ ДОМА: {h['address']}",
             f"👷 {houses.ZVENO_NAMES.get(h['zveno'], '')}", '']
    filled = 0
    for key, label in PASSPORT_FIELDS:
        val = data.get(key)
        if val:
            filled += 1
            lines.append(f'▪️ {label}:\n   {val}')
        else:
            lines.append(f'▫️ {label}: —')
    lines.append('')
    lines.append(f'Заполнено: {filled}/{len(PASSPORT_FIELDS)}. '
                 'Нажмите «Редактировать», чтобы дополнить.')
    return '\n'.join(lines)


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


# ---------- Старт ----------

@dp.bot_started()
async def on_bot_started(event: BotStarted):
    await event.bot.send_message(chat_id=event.chat_id, text=MAIN_TEXT,
                                 attachments=[main_menu_kb().as_markup()])


@dp.message_created(CommandStart())
async def on_start(event: MessageCreated):
    STATE.pop(_uid(event), None)
    await send(event.message, MAIN_TEXT, main_menu_kb())


@dp.message_created(Command('menu'))
async def on_menu(event: MessageCreated):
    STATE.pop(_uid(event), None)
    await send(event.message, MAIN_TEXT, main_menu_kb())


# ---------- Текстовые сообщения (поиск + шаги диалогов) ----------

def _uid(event) -> int:
    return event.message.sender.user_id


def _uname(event) -> str:
    return getattr(event.message.sender, 'full_name', None) or ''


@dp.message_created()
async def on_text(event: MessageCreated):
    text = (event.message.body.text or '').strip()
    if not text or text.startswith('/'):
        return
    uid = _uid(event)
    state = STATE.get(uid)

    if state and state['mode'] == 'req_addr':
        # Шаг 1 новой заявки: адрес свободным текстом
        found = houses.search(text, limit=1)
        h = found[0] if found else None
        STATE[uid] = {'mode': 'req_descr',
                      'house_id': h['id'] if h else None,
                      'address': h['address'] if h else text}
        addr = h['address'] if h else text
        note = '' if h else '\n⚠️ Адрес не найден в базе домов — запишу как есть.'
        await send(event.message, f'🏠 Адрес: {addr}{note}\n\n📝 Теперь опишите проблему одним сообщением:')
        return

    if state and state['mode'] == 'req_descr':
        req_id = db.add_request(state['house_id'], state['address'], text, uid, _uname(event))
        STATE.pop(uid, None)
        r = db.get_request(req_id)
        await send(event.message, '✅ Заявка создана!\n\n' + request_card_text(r), request_card_kb(r))
        return

    if state and state['mode'] == 'pass_edit':
        h = houses.HOUSES_BY_ID[state['house_id']]
        db.set_passport_field(h['id'], state['field'], text, _uname(event))
        STATE.pop(uid, None)
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='✏️ Редактировать ещё', payload=f"pe:{h['id']}"),
               CallbackButton(text='🗂 Открыть паспорт', payload=f"p:{h['id']}"))
        await send(event.message,
                   f"✅ Сохранено: {PASSPORT_LABELS[state['field']]} — {h['address']}", kb)
        return

    # Режим по умолчанию — поиск дома по адресу
    found = houses.search(text)
    if not found:
        await send(event.message,
                   f'🤷 По запросу «{text}» дом не найден.\n'
                   'Попробуйте написать иначе, например: «Розы Люксембург 118» или «Байкальская 237».',
                   main_menu_kb())
    elif len(found) == 1:
        h = found[0]
        await send(event.message, house_card_text(h), house_card_kb(h))
    else:
        kb = InlineKeyboardBuilder()
        for h in found:
            kb.row(CallbackButton(text=h['address'], payload=f"h:{h['id']}"))
        await send(event.message, f'🔍 Нашёл несколько домов по «{text}» — выберите:', kb)


# ---------- Кнопки ----------

@dp.message_callback()
async def on_callback(event: MessageCallback):
    payload = event.callback.payload or ''
    uid = event.callback.user.user_id
    msg = event.message
    parts = payload.split(':')
    action = parts[0]

    if action == 'menu':
        STATE.pop(uid, None)
        await send(msg, MAIN_TEXT, main_menu_kb())

    elif action == 'srch':
        STATE.pop(uid, None)
        await send(msg, '🔍 Напишите адрес (улица и номер дома), например:\n«Розы Люксембург 118/5» или «Байкальская 237»')

    elif action == 'zv':
        kb = InlineKeyboardBuilder()
        for z in (1, 2, 3):
            kb.row(CallbackButton(text=houses.ZVENO_NAMES[z], payload=f'zvl:{z}'))
        await send(msg, '🏘 Выберите звено:', kb)

    elif action == 'zvl':
        z = int(parts[1])
        hs = houses.by_zveno(z)
        lines = [f'🏘 {houses.ZVENO_NAMES[z]} — {len(hs)} домов:', '']
        lines += [f"• {h['address']}" for h in hs]
        lines.append('')
        lines.append('💡 Напишите адрес, чтобы открыть карточку дома.')
        kb = InlineKeyboardBuilder()
        kb.row(CallbackButton(text='◀️ К звеньям', payload='zv'),
               CallbackButton(text='🏠 Меню', payload='menu'))
        await send(msg, '\n'.join(lines), kb)

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
            body = title + '\n\nПока пусто.'
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
