"""Обработчики бота «Помощник сантехника» УК Жемчужина (мессенджер MAX)."""
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
)
from maxapi.types import InputMedia
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from . import db, houses

log = logging.getLogger(__name__)
dp = Dispatcher()

with open(os.path.join(houses.DATA_DIR, 'directory.json'), encoding='utf-8') as f:
    DIRECTORY = json.load(f)['sections']

with open(os.path.join(houses.DATA_DIR, 'complexes.json'), encoding='utf-8') as f:
    COMPLEXES = json.load(f)
COMPLEX_NAMES = {c['id']: c['name'] for c in COMPLEXES}

DOCS_DIR = os.path.join(houses.DATA_DIR, 'docs')

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
           CallbackButton(text='🏘 Наши дома', payload='homes'))
    kb.row(CallbackButton(text='📋 Заявки', payload='rl'),
           CallbackButton(text='➕ Новая заявка', payload='nr'))
    kb.row(CallbackButton(text='📖 Справочник', payload='dir'))
    return kb


BOT_NAME = 'Люся'  # имя помощницы — поменяйте здесь, если выбрали другое

MAIN_TEXT = (
    f'👋 Привет, я {BOT_NAME} — помощница нашего звена сантехников УК «Жемчужина».\n\n'
    'Чем помогу:\n'
    '• 🔍 Найду дом — наш или нет, и покажу точку на карте\n'
    '• 🗂 Паспорт дома — розливы, арматура, где перекрывать, доступ\n'
    '• 📋 Заявки — запишу и буду вести: новая → в работе → выполнена\n'
    '• 📖 Справочник — телефоны, нормативы, сроки, шпаргалка по трубам\n\n'
    '💡 Просто напишите адрес (например: «Розы Люксембург 118/5») — я всё найду. 😉'
)


# ---------- Карточки ----------

def house_card_text(h) -> str:
    cx = db.get_house_complex(h['id'])
    cx_name = COMPLEX_NAMES.get(cx, 'не указан')
    n_docs = len(db.list_docs(h['id']))
    return (f"🏠 {h['address']}\n"
            f"🏙 ЖК: {cx_name}\n"
            f"👷 Наш дом (УК «Жемчужина»)\n"
            f"📊 Заявок за год: {h['requests_year']}\n"
            f"📁 Документов: {n_docs}")


def house_card_kb(h) -> InlineKeyboardBuilder:
    gis, ya = houses.map_links(h)
    kb = InlineKeyboardBuilder()
    kb.row(LinkButton(text='🗺 2ГИС', url=gis),
           LinkButton(text='🗺 Яндекс', url=ya))
    kb.row(CallbackButton(text='🗂 Паспорт дома', payload=f"p:{h['id']}"),
           CallbackButton(text='➕ Заявка сюда', payload=f"nrh:{h['id']}"))
    kb.row(CallbackButton(text='📁 Документы', payload=f"dl:{h['id']}"),
           CallbackButton(text='🏙 Указать ЖК', payload=f"cxs:{h['id']}"))
    kb.row(CallbackButton(text='🏠 Меню', payload='menu'))
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


def _safe_filename(name: str) -> str:
    name = re.sub(r'[^\w.\-() ]', '_', name)
    return name[:80] or 'file'


async def _download(url: str) -> bytes:
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()


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

    if not text or text.startswith('/'):
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

    # Режим по умолчанию — поиск дома по адресу
    found = houses.search(text)
    if not found:
        await send(event.message,
                   f'🤷‍♀️ По запросу «{text}» я ничего не нашла.\n'
                   'Попробуйте написать иначе, например: «Розы Люксембург 118» или «Байкальская 237».',
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
    msg = event.message
    parts = payload.split(':')
    action = parts[0]

    if action == 'menu':
        STATE.pop(uid, None)
        await send(msg, MAIN_TEXT, main_menu_kb())

    elif action == 'srch':
        STATE.pop(uid, None)
        await send(msg, '🔍 Напишите адрес (улица и номер дома), например:\n«Розы Люксембург 118/5» или «Байкальская 237»')

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
            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text='📎 Добавить документ', payload=f"da:{h['id']}"),
                   CallbackButton(text='🏠 К дому', payload=f"h:{h['id']}"))
            if not docs:
                await send(msg, f"📁 По дому {h['address']} документов пока нет.\n"
                                'Нажмите «Добавить документ» и пришлите фото/скан/файл.', kb)
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
