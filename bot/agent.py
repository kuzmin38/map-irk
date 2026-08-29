"""Разговорный агент Люси: свободный текст → ответ через ИИ с инструментами
поверх реальных данных (дома, паспорта, документы, стояки, справочник,
работы, заявки). Только чтение — ничего не создаёт и не изменяет.
"""
import asyncio
import json
import logging
import os

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


def _tool_get_meetings(limit: int = 3) -> str:
    """Протоколы последних планёрок: решения и задачи, а не вся расшифровка."""
    items = db.list_meetings(limit=max(1, min(int(limit or 3), 10)))
    out = []
    for m in items:
        if not m['protocol']:
            continue
        try:
            data = json.loads(m['protocol'])
        except json.JSONDecodeError:
            continue
        out.append({
            'id': m['id'], 'date': m['created_at'], 'title': m['title'],
            'summary': data.get('summary'), 'decisions': data.get('decisions'),
            'tasks': data.get('tasks'), 'questions': data.get('questions'),
        })
    return json.dumps({'meetings': out}, ensure_ascii=False)


def _tool_search_docs(query: str, address: str | None = None) -> str:
    """Ищет по разобранным документам и отдаёт фрагменты вокруг совпадения."""
    from . import doc_text

    house = houses.detect_house(address) if address else None
    rows = db.search_doc_texts(query, house_id=house['id'] if house else None,
                               address=house['address'] if house else address)
    if not rows:
        stats = db.doc_texts_stats()
        if not stats.get(db.DOC_OK):
            return json.dumps({'found': [], 'note': 'документы ещё не разобраны — '
                               'инженеру нужно нажать «Прочитать документацию»'},
                              ensure_ascii=False)
        return json.dumps({'found': [], 'note': f'по запросу "{query}" ничего нет'},
                          ensure_ascii=False)
    return json.dumps({'found': [
        {'key': r['key'], 'title': r['title'], 'addresses': r['addresses'],
         'excerpt': doc_text.excerpt(r['text'], query),
         'chars': r['chars']} for r in rows]}, ensure_ascii=False)


def _tool_read_doc(key: str, part: int = 1) -> str:
    """Отдаёт документ кусками по 4000 знаков — целиком он в ответ не влезет."""
    row = db.get_doc_text('project', key) or db.get_doc_text('house', key)
    if not row or not row['text']:
        return json.dumps({'error': 'документ не найден или не разобран'},
                          ensure_ascii=False)
    size = 4000
    part = max(1, int(part or 1))
    total = max(1, (len(row['text']) + size - 1) // size)
    chunk = row['text'][(part - 1) * size:part * size]
    return json.dumps({'title': row['title'], 'part': part, 'parts_total': total,
                       'text': chunk}, ensure_ascii=False)


TOOLS = [
    {'type': 'function', 'function': {
        'name': 'find_house', 'description': 'Найти дом по адресу или части адреса.',
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'Адрес или его часть, например "Байкальская 99"'}},
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
    {'type': 'function', 'function': {
        'name': 'search_docs',
        'description': 'Поиск по проектной документации и документам домов '
                        '(отопление, ВК, тепловые пункты, паспорта, расчёты). '
                        'Возвращает фрагменты вокруг найденного. address — адрес дома, '
                        'чтобы искать только по нему.',
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'что ищем: «диаметр розлива», «ДУ», «манометр»'},
            'address': {'type': 'string'}}, 'required': ['query']}}},
    {'type': 'function', 'function': {
        'name': 'read_doc',
        'description': 'Читать разобранный документ кусками по 4000 знаков. '
                        'key берётся из результатов search_docs, part — номер куска.',
        'parameters': {'type': 'object', 'properties': {
            'key': {'type': 'string'}, 'part': {'type': 'integer'}},
            'required': ['key']}}},
    {'type': 'function', 'function': {
        'name': 'get_meetings',
        'description': 'Протоколы последних планёрок: о чём говорили, что решили, '
                        'какие задачи и на ком. limit — сколько последних взять.',
        'parameters': {'type': 'object', 'properties': {
            'limit': {'type': 'integer'}}, 'required': []}}},
]

TOOL_FUNCS = {
    'find_house': lambda a: _tool_find_house(a['query']),
    'get_passport': lambda a: _tool_get_passport(a['house_id']),
    'list_docs': lambda a: _tool_list_docs(a['house_id']),
    'get_riser': lambda a: _tool_get_riser(a['address'], a['flat']),
    'get_directory': lambda a: _tool_get_directory(a['section']),
    'get_house_works': lambda a: _tool_get_house_works(a['house_id']),
    'get_open_requests': lambda a: _tool_get_open_requests(a.get('house_id')),
    'get_meetings': lambda a: _tool_get_meetings(a.get('limit', 3)),
    'search_docs': lambda a: _tool_search_docs(a['query'], a.get('address')),
    'read_doc': lambda a: _tool_read_doc(a['key'], a.get('part', 1)),
}


SYSTEM_PROMPT = (
    'Ты — Люся, помощница управляющей компании «Жемчужина» (Иркутск). '
    'Общаешься в личке с сантехниками и руководством. Характер живой, '
    'своя, с лёгкой иронией — можешь подтрунить или пошутить, но по делу '
    'отвечаешь точно и по существу. Обращаешься на «ты», по имени.\n\n'
    'У тебя есть инструменты, чтобы посмотреть реальные данные: дома, '
    'паспорта домов, документы, стояки квартир, справочник и нормативы, '
    'работы и дедлайны, заявки, протоколы планёрок, а также текст проектной '
    'документации (search_docs — по нему отвечай про диаметры, схемы, '
    'оборудование ТП). Всегда пользуйся инструментами вместо '
    'того, чтобы гадать — сама ты этих данных не помнишь, только через '
    'инструменты. Если по инструментам ничего не нашлось — так и скажи, '
    'не выдумывай данные.\n\n'
    'Про СНиПы, ГОСТы и законы отвечай по своим знаниям. Если нужна '
    'точная формулировка или номер пункта, а не суть — честно скажи '
    '«за точным пунктом сверьтесь с текстом норматива», не выдумывай номера.'
)

MAX_ROUNDS = 4


async def answer(user_id: int, user_name: str, user_text: str) -> str | None:
    """Отвечает на свободный вопрос через инструменты. None — если ИИ
    недоступен, произошла ошибка или исчерпан лимит кругов."""
    if not ai.enabled():
        return None

    profile = db.get_user_notes(user_id)
    system = SYSTEM_PROMPT
    if profile:
        system += f'\n\nЧто ты знаешь про этого пользователя ({user_name}): {profile}'

    messages = [{'role': 'system', 'content': system}]
    messages += db.recent_chat_history(user_id, limit=6)
    messages.append({'role': 'user', 'content': user_text})

    for _ in range(MAX_ROUNDS):
        message = await ai.chat(messages, tools=TOOLS)
        if message is None:
            return None
        tool_calls = message.get('tool_calls')
        if not tool_calls:
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
            else:
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