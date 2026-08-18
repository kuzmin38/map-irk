"""Разговорный агент Люси: свободный текст → ответ через ИИ с инструментами
поверх реальных данных (дома, паспорта, документы, стояки, справочник,
работы, заявки). Только чтение — ничего не создаёт и не изменяет.
"""
import asyncio
import json
import logging
import os
import time

from . import ai, db, houses
from . import risers as risers_mod

log = logging.getLogger('agent')

with open(os.path.join(houses.DATA_DIR, 'directory.json'), encoding='utf-8') as f:
    DIRECTORY = json.load(f)['sections']

# Дублирует handlers.PASSPORT_FIELDS — вынесение в общий модуль создало бы
# циклический импорт (handlers → agent → handlers), а список маленький и
# меняется редко.
PASSPORT_LABELS = {
    'year': 'Год постройки',
    'floors': 'Этажность',
    'entrances': 'Подъезды',
    'flats': 'Квартиры',
    'heat': 'Тепловой узел (элеватор/ИТП, расположение)',
    'rozliv': 'Розлив (верхний/нижний, материал, ДУ)',
    'hvs': 'ХВС: ввод, материал, диаметры',
    'gvs': 'ГВС: схема, материал, диаметры',
    'kanaliz': 'Канализация: материал, выпуски',
    'valves': 'Запорная арматура: где перекрывать',
    'keys': 'Доступ: ключи от подвала/ТУ',
    'notes': 'Примечания',
}


def _tool_find_house(query: str) -> str:
    found = houses.search(query, limit=5)
    return json.dumps({'found': [{'id': h['id'], 'address': h['address']} for h in found]},
                       ensure_ascii=False)


def _tool_get_passport(house_id: int) -> str:
    h = houses.HOUSES_BY_ID.get(house_id)
    if not h:
        return json.dumps({'error': 'дом не найден'}, ensure_ascii=False)
    passport = db.get_passport(house_id)
    if not passport:
        return json.dumps({'address': h['address'], 'passport': {},
                            'note': 'паспорт ещё не заполнен'}, ensure_ascii=False)
    labeled = {PASSPORT_LABELS.get(k, k): v for k, v in passport.items()}
    return json.dumps({'address': h['address'], 'passport': labeled}, ensure_ascii=False)


def _tool_list_docs(house_id: int) -> str:
    h = houses.HOUSES_BY_ID.get(house_id)
    if not h:
        return json.dumps({'error': 'дом не найден'}, ensure_ascii=False)
    docs = db.list_docs(house_id)
    return json.dumps({
        'address': h['address'],
        'docs': [{'filename': d['filename'], 'note': d['note'],
                  'uploaded_by': d['uploaded_by'], 'uploaded_at': d['uploaded_at']}
                 for d in docs],
    }, ensure_ascii=False)


def _tool_get_riser(address: str, flat: int) -> str:
    found = risers_mod.locate(address, flat)
    if not found:
        return json.dumps(
            {'error': f'квартира {flat} по адресу "{address}" не найдена в таблицах стояков'},
            ensure_ascii=False)
    block, addr, floor, riser, on_floor = found
    chain = risers_mod.riser_flats(block, riser)
    return json.dumps({
        'address': addr, 'flat': flat, 'floor': floor, 'riser': riser,
        'flats_on_floor': on_floor, 'riser_chain_bottom_to_top': chain,
    }, ensure_ascii=False)


def _tool_get_directory(section: str) -> str:
    if section == 'all':
        return json.dumps([{'id': s['id'], 'title': s['title']} for s in DIRECTORY],
                           ensure_ascii=False)
    for s in DIRECTORY:
        if s['id'] == section:
            return json.dumps({'title': s['title'], 'text': s['text']}, ensure_ascii=False)
    ids = [s['id'] for s in DIRECTORY]
    return json.dumps({'error': f'раздела "{section}" нет, доступные: {ids}'}, ensure_ascii=False)


def _tool_get_house_works(house_id: int) -> str:
    h = houses.HOUSES_BY_ID.get(house_id)
    if not h:
        return json.dumps({'error': 'дом не найден'}, ensure_ascii=False)
    works = db.list_works(house_id=house_id, open_only=False, limit=20)
    return json.dumps({
        'address': h['address'],
        'works': [{'title': w['title'], 'status': db.WORK_LABELS.get(w['status'], w['status']),
                   'deadline': w['deadline'], 'assignee': w['assignee']} for w in works],
    }, ensure_ascii=False)


def _tool_get_open_requests(house_id: int | None = None) -> str:
    reqs = db.list_requests(limit=30)
    if house_id is not None:
        reqs = [r for r in reqs if r['house_id'] == house_id]
    return json.dumps({
        'requests': [{'id': r['id'], 'address': r['address'], 'description': r['description'],
                      'status': db.STATUS_LABELS.get(r['status'], r['status'])} for r in reqs],
    }, ensure_ascii=False)


TOOLS = [
    {'type': 'function', 'function': {
        'name': 'find_house', 'description': 'Найти дом по адресу или части адреса.',
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': f'Адрес или его часть, например "{houses.examples(1)[0] if houses.examples(1) else "Седова 67"}"'}},
            'required': ['query']}}},
    {'type': 'function', 'function': {
        'name': 'get_passport', 'description': 'Технический паспорт дома по его id.',
        'parameters': {'type': 'object', 'properties': {
            'house_id': {'type': 'integer'}}, 'required': ['house_id']}}},
    {'type': 'function', 'function': {
        'name': 'list_docs', 'description': 'Список загруженных документов дома по его id.',
        'parameters': {'type': 'object', 'properties': {
            'house_id': {'type': 'integer'}}, 'required': ['house_id']}}},
    {'type': 'function', 'function': {
        'name': 'get_riser',
        'description': 'Этаж, номер стояка и соседи по стояку для квартиры по адресу дома.',
        'parameters': {'type': 'object', 'properties': {
            'address': {'type': 'string'}, 'flat': {'type': 'integer'}},
            'required': ['address', 'flat']}}},
    {'type': 'function', 'function': {
        'name': 'get_directory',
        'description': 'Справочник: нормативы, телефоны, сроки устранения, шпаргалка по трубам. '
                        'section="all" — список разделов, иначе id раздела.',
        'parameters': {'type': 'object', 'properties': {
            'section': {'type': 'string'}}, 'required': ['section']}}},
    {'type': 'function', 'function': {
        'name': 'get_house_works', 'description': 'Работы и дедлайны по дому (id дома).',
        'parameters': {'type': 'object', 'properties': {
            'house_id': {'type': 'integer'}}, 'required': ['house_id']}}},
    {'type': 'function', 'function': {
        'name': 'get_open_requests',
        'description': 'Заявки (открытые и недавно выполненные). house_id можно не указывать — '
                        'тогда по всем домам.',
        'parameters': {'type': 'object', 'properties': {
            'house_id': {'type': 'integer'}}, 'required': []}}},
]

TOOL_FUNCS = {
    'find_house': lambda a: _tool_find_house(a['query']),
    'get_passport': lambda a: _tool_get_passport(a['house_id']),
    'list_docs': lambda a: _tool_list_docs(a['house_id']),
    'get_riser': lambda a: _tool_get_riser(a['address'], a['flat']),
    'get_directory': lambda a: _tool_get_directory(a['section']),
    'get_house_works': lambda a: _tool_get_house_works(a['house_id']),
    'get_open_requests': lambda a: _tool_get_open_requests(a.get('house_id')),
}


def _houses_block() -> str:
    """Все адреса списком — меньше двух килобайт на 86 домов.

    Без этого Люся судила о наличии дома по памяти: однажды заявила, что
    «4-я Советская 30» не в нашем управлении, не обратившись ни к одному
    инструменту. Список перед глазами такую выдумку исключает.
    """
    # Адрес идёт первым и целиком: когда строка начиналась со служебного id
    # («28 — 4-я Советская 30»), Люся принимала его за номер дома и отвечала
    # «дом 28 — это 4-я Советская, 30»
    return '\n'.join(f"{h['address']} (id {h['id']})" for h in houses.HOUSES)


def _build_prompt() -> str:
    """Собирает системную подсказку вместе со списком домов.

    Отдельной функцией — чтобы подсказку можно было пересобрать под другой
    список домов, не перезапуская модуль.
    """
    return (
    'Ты — Люся, помощница управляющей компании «Жемчужина» (Иркутск). '
    'Общаешься в личке с сантехниками и руководством. Характер живой, '
    'своя, с лёгкой иронией — можешь подтрунить или пошутить, но по делу '
    'отвечаешь точно и по существу. Обращаешься на «ты», по имени.\n\n'
    'У тебя есть инструменты, чтобы посмотреть реальные данные: паспорта '
    'домов, документы, стояки квартир, справочник и нормативы, работы '
    'и дедлайны, заявки. Всегда пользуйся инструментами вместо того, чтобы '
    'гадать — этих данных ты не помнишь, только через инструменты. Если по '
    'инструментам ничего не нашлось — так и скажи, не выдумывай данные.\n\n'
    'Про СНиПы, ГОСТы и законы отвечай по своим знаниям. Если нужна '
    'точная формулировка или номер пункта, а не суть — честно скажи '
    '«за точным пунктом сверьтесь с текстом норматива», не выдумывай номера.'
    '\n\nПолный список домов в обслуживании, других у нас нет:\n'
    + _houses_block() +
    '\n\nЭтот список — истина. Никогда не говори, что дома нет, если он тут '
    'есть, и не рассуждай о том, чей это район и та ли это улица.\n'
    'Номер дома называют словом и без улицы: «тридцатый дом» — это дом '
    'с номером 30, и если такой в списке один, речь про него. Название ЖК '
    'адресом не является: «Четыре солнца тридцатый дом» — это дом 30.\n'
    'В скобках — служебный id записи для инструментов. Это НЕ номер дома '
    'и не часть адреса, вслух его не называй: номер дома стоит в самом '
    'адресе, последним.'
    )


SYSTEM_PROMPT = _build_prompt()

MAX_ROUNDS = 4

# Предел на весь разговор с моделью: четыре круга по таймауту запроса — это
# уже минуты, а человек в мессенджере столько не ждёт.
BUDGET = 45


class TooSlow(Exception):
    """Модель не уложилась в отведённое время.

    Отдельно от «не нашла»: человеку важно понимать, что вопрос понят, но
    ответ не поспел, — тогда он повторит, а не решит, что бот бесполезен.
    """


async def answer(user_id: int, user_name: str, user_text: str) -> str | None:
    """Отвечает на свободный вопрос через инструменты. None — если ИИ
    недоступен, произошла ошибка, кончилось время или лимит кругов."""
    if not ai.enabled():
        return None
    started = time.monotonic()

    profile = db.get_user_notes(user_id)
    system = SYSTEM_PROMPT
    if profile:
        system += f'\n\nЧто ты знаешь про этого пользователя ({user_name}): {profile}'

    messages = [{'role': 'system', 'content': system}]
    messages += db.recent_chat_history(user_id, limit=6)
    messages.append({'role': 'user', 'content': user_text})

    for round_no in range(MAX_ROUNDS):
        left = BUDGET - (time.monotonic() - started)
        if left <= 0:
            log.warning('Не уложилась в %s с, кругов сделано %s', BUDGET, round_no)
            raise TooSlow
        try:
            message = await asyncio.wait_for(ai.chat(messages, tools=TOOLS), left)
        except asyncio.TimeoutError:
            log.warning('Модель не ответила за отведённое время')
            raise TooSlow from None
        if message is None:
            return None
        tool_calls = message.get('tool_calls')
        if not tool_calls:
            # Видно, ответила ли модель по данным или сочинила: однажды она
            # сообщила, что дома нет, не заглянув ни в один инструмент
            if round_no == 0:
                log.info('Ответ без обращения к данным')
            content = (message.get('content') or '').strip()
            if not content:
                return None
            db.add_chat_message(user_id, 'user', user_text)
            db.add_chat_message(user_id, 'assistant', content)
            asyncio.create_task(_update_profile(user_id, user_name))
            return content
        messages.append(message)
        for call in tool_calls:
            name = call['function']['name']
            try:
                args = json.loads(call['function']['arguments'] or '{}')
            except json.JSONDecodeError:
                args = {}
            func = TOOL_FUNCS.get(name)
            if func:
                result = func(args)
                log.info('%s(%s) → %s', name, args, result[:200])
            else:
                log.warning('Модель просит несуществующий инструмент %s', name)
                result = json.dumps({'error': f'неизвестный инструмент {name}'}, ensure_ascii=False)
            messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': result})
    return None


async def _update_profile(user_id: int, user_name: str):
    """Обновляет долгосрочную заметку о пользователе отдельным вызовом ИИ.
    Запускается в фоне (asyncio.create_task) — не блокирует ответ."""
    try:
        history = db.recent_chat_history(user_id, limit=12)
        if not history:
            return
        transcript = '\n'.join(
            f"{'Люся' if m['role'] == 'assistant' else user_name}: {m['content']}"
            for m in history)
        old_profile = db.get_user_notes(user_id)
        prompt = (
            f'Вот текущая заметка о пользователе {user_name}: '
            f'"{old_profile or "(пока пусто)"}"\n\n'
            f'Вот последние сообщения диалога с ним:\n{transcript}\n\n'
            'Обнови заметку: 2-4 коротких предложения о его привычках, манере '
            'общения, какими домами/темами чаще интересуется. Пиши только саму '
            'заметку, без вступлений.'
        )
        new_profile = await ai.ask(
            prompt, system='Ты помогаешь боту Люсе запоминать факты о собеседниках.',
            max_tokens=200, temperature=0.3)
        if new_profile:
            db.set_user_notes(user_id, new_profile.strip())
    except Exception:
        log.exception('Не удалось обновить профиль пользователя %s', user_id)