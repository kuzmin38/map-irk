"""Разговорный агент Люси: свободный текст → ответ через ИИ с инструментами
поверх реальных данных (дома, паспорта, документы, стояки, справочник,
работы, заявки). Только чтение — ничего не создаёт и не изменяет.
"""
import asyncio
import contextvars
import json
import logging
import os
import time

from . import ai, db, feminine, houses
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
    # Ключ называется house_id, а не id: короткое «id» рядом с адресом
    # модель принимала за номер дома
    return json.dumps({'found': [{'house_id': h['id'], 'address': h['address']}
                                 for h in found]}, ensure_ascii=False)


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


def _tool_flat_notes(house_id: int, flat=None) -> str:
    """Что находили по квартирам: подмес найдут там же снова, и это надо знать."""
    h = houses.HOUSES_BY_ID.get(house_id)
    if not h:
        return json.dumps({'error': 'дом не найден'}, ensure_ascii=False)
    zametki = [{'квартира': z['flat'], 'что': z['text'], 'когда': z['created_at'],
                'кто': z['author']}
               for z in db.flat_notes(house_id, flat, limit=30)]
    return json.dumps({'адрес': h['address'], 'найдено': len(zametki),
                       'находки': zametki}, ensure_ascii=False)


def _tool_find_item(query: str) -> str:
    """Где лежит вещь по описи.

    Мотопомпа в компании была, а на затопленной парковке о ней не
    вспомнили. Теперь достаточно спросить.
    """
    from . import inventory

    nashlos = []
    for it in db.list_items():
        if not inventory.matches(query, it['name'], it['place'] or ''):
            continue
        dom = houses.HOUSES_BY_ID.get(it['house_id']) if it['house_id'] else None
        nashlos.append({
            'что': it['name'],
            'сколько': it['qty'],
            'адрес': dom['address'] if dom else None,
            'место': it['place'],
            'записал': it['added_by_name'],
        })
    if not nashlos:
        return json.dumps({'найдено': 0,
                           'подсказка': 'в описи такого нет — так и скажи, и предложи '
                                        'записать: «в инвентарь: название, место, адрес»'},
                          ensure_ascii=False)
    return json.dumps({'найдено': len(nashlos), 'вещи': nashlos[:15]}, ensure_ascii=False)


def _tool_get_equipment(house_id: int) -> str:
    """Приборы дома: что стоит, с каким номером, до какого числа поверка.

    Без этого инструмента Люся про манометры не знала вовсе и на прямой
    вопрос отвечала общими словами.
    """
    h = houses.HOUSES_BY_ID.get(house_id)
    if not h:
        return json.dumps({'error': 'дом не найден'}, ensure_ascii=False)
    points = []
    for p in db.list_points(h['id']):
        dev = db.active_device(p['id'])
        points.append({
            'место': ', '.join(x for x in (p['tp'], p['place']) if x),
            'прибор': ({
                'заводской_номер': dev['serial'],
                'поверка_до': dev['verified_until'],
                'установлен': dev['installed_at'],
                'установил': dev['installed_by'],
                'фото_прибора': bool(dev['photo_device']),
                'фото_паспорта': bool(dev['photo_passport']),
                'примечание': dev['note'],
            } if dev else None),
            'замен_за_всё_время': len(db.point_history(p['id'])),
        })
    return json.dumps({'address': h['address'], 'манометры': points,
                       'note': 'манометров не заведено' if not points else None},
                      ensure_ascii=False)


def _tool_get_meters(house_id: int) -> str:
    """Счётчики дома и последние показания: что стоит, кто завёл, кто подавал."""
    h = houses.HOUSES_BY_ID.get(house_id)
    if not h:
        return json.dumps({'error': 'дом не найден'}, ensure_ascii=False)
    vidy = {'hvs': 'ХВС', 'gvs': 'ГВС', 'heat': 'тепло', 'other': 'другой'}
    meters = []
    for m in db.list_meters(h['id']):
        rs = db.meter_readings(m['id'], limit=2)
        meters.append({
            'название': m['label'],
            'вид': vidy.get(m['kind'], m['kind']),
            'завёл': m['created_by_name'],
            'последнее_показание': ({
                'значение': rs[0]['value'],
                'период': rs[0]['period'],
                'подал': rs[0]['submitted_by_name'],
                'есть_фото': bool(rs[0]['photo']),
            } if rs else None),
            'расход_за_период': (rs[0]['value'] - rs[1]['value']) if len(rs) >= 2 else None,
        })
    return json.dumps({'address': h['address'], 'счётчики': meters,
                       'note': 'счётчиков не заведено' if not meters else None},
                      ensure_ascii=False)


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


def _tool_chat_reports(chat_id) -> str:
    """Последние сообщения того же чата — с расшифровками голоса и видео.

    Без этого на вопрос «какой адрес?» модели неоткуда взять отчёт, о котором
    спрашивают, и она отвечает тем, что попалось в памяти.
    """
    if not chat_id:
        return json.dumps({'error': 'это личка, рабочего чата тут нет'},
                          ensure_ascii=False)
    records = db.chat_reports(chat_id, limit=8)
    lenta = []
    for r in records:
        h = houses.HOUSES_BY_ID.get(r['house_id']) if r['house_id'] else None
        lenta.append({
            'когда': r['created_at'],
            'кто': r['user_name'],
            'текст': r['text'] or '',
            'расшифровка': r['transcript'] or '',
            'вложение': bool(r['has_files']),
            'дом': h['address'] if h else None,
        })
    return json.dumps({'сообщения': lenta,
                       'подсказка': 'дом=null означает, что адрес в сообщении '
                                    'не назван — так и скажи, не подставляй свой'},
                      ensure_ascii=False)


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
        'name': 'get_equipment',
        'description': 'Манометры дома: место установки, заводской номер, срок поверки, '
                        'кто и когда поставил, сколько было замен. По id дома.',
        'parameters': {'type': 'object', 'properties': {
            'house_id': {'type': 'integer'}}, 'required': ['house_id']}}},
    {'type': 'function', 'function': {
        'name': 'get_meters',
        'description': 'Счётчики дома: название, вид, кто завёл, последнее показание, '
                        'кто его подал, расход за период. По id дома.',
        'parameters': {'type': 'object', 'properties': {
            'house_id': {'type': 'integer'}}, 'required': ['house_id']}}},
    {'type': 'function', 'function': {
        'name': 'get_house_works', 'description': 'Работы и дедлайны по дому (id дома).',
        'parameters': {'type': 'object', 'properties': {
            'house_id': {'type': 'integer'}}, 'required': ['house_id']}}},
    {'type': 'function', 'function': {
        'name': 'get_chat_reports',
        'description': 'Последние сообщения рабочего чата, где идёт разговор: тексты, '
                        'расшифровки голосовых и видеоотчётов, распознанный адрес. '
                        'Обращайся сюда, когда спрашивают про отчёт, видео, «какой адрес» '
                        'или «что там было» — речь про ленту этого чата.',
        'parameters': {'type': 'object', 'properties': {}, 'required': []}}},
    {'type': 'function', 'function': {
        'name': 'get_flat_notes',
        'description': 'Что уже находили по квартирам дома: подмес, течь, засор, '
                        'неисправные краны. Спрашивают «что было по такой-то '
                        'квартире», «тут уже было?» — сюда. flat можно не указывать.',
        'parameters': {'type': 'object', 'properties': {
            'house_id': {'type': 'integer'}, 'flat': {'type': 'integer'}},
            'required': ['house_id']}}},
    {'type': 'function', 'function': {
        'name': 'find_item',
        'description': 'Опись имущества: где лежит вещь — насос, мотопомпа, пушка, '
                        'тура, инструмент. Спрашивают «где у нас …», «есть ли у нас …», '
                        '«что лежит на таком-то доме» — сюда. query — название вещи '
                        'словами человека.',
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string'}}, 'required': ['query']}}},
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
    'get_equipment': lambda a: _tool_get_equipment(a['house_id']),
    'get_meters': lambda a: _tool_get_meters(a['house_id']),
    'get_house_works': lambda a: _tool_get_house_works(a['house_id']),
    'get_open_requests': lambda a: _tool_get_open_requests(a.get('house_id')),
    'get_chat_reports': lambda a: _tool_chat_reports(CHAT.get()),
    'find_item': lambda a: _tool_find_item(a['query']),
    'get_flat_notes': lambda a: _tool_flat_notes(a['house_id'], a.get('flat')),
}

# Чат, в котором идёт разговор. Модель его не знает и знать не должна —
# инструмент берёт его отсюда, а не из аргументов
CHAT = contextvars.ContextVar('chat_id', default=None)


def _chat_context_block(chat_id) -> str:
    """Лента чата коротким списком: кто, когда и что сказал."""
    if chat_id is None:
        return ''
    stroki = []
    for r in db.chat_context(chat_id, limit=12):
        chto = (r['transcript'] or r['text'] or '').strip()
        if not chto:
            continue
        dom = houses.HOUSES_BY_ID.get(r['house_id']) if r['house_id'] else None
        adres = f" [{dom['address']}]" if dom else ''
        znak = ' 🚨' if r['is_issue'] else ''
        stroki.append(f"{r['created_at'][-5:]} {r['user_name'] or '—'}{adres}{znak}: "
                      f'{chto[:200]}')
    return '\n'.join(stroki)


def _houses_block() -> str:
    """Все адреса списком — меньше двух килобайт на 86 домов.

    Без этого Люся судила о наличии дома по памяти: однажды заявила, что
    «4-я Советская 30» не в нашем управлении, не обратившись ни к одному
    инструменту. Список перед глазами такую выдумку исключает.
    """
    # Никаких служебных номеров рядом с адресом. Сначала список выглядел как
    # «28 — 4-я Советская 30», и Люся выдавала «дом 28 — это Советская, 30».
    # Перенос id в скобки не помог — путала всё равно. Теперь только адреса,
    # а id она берёт через find_house, где он назван house_id.
    return '\n'.join(h['address'] for h in houses.HOUSES)


def _build_prompt() -> str:
    """Собирает системную подсказку вместе со списком домов.

    Отдельной функцией — чтобы подсказку можно было пересобрать под другой
    список домов, не перезапуская модуль.
    """
    return (
    'Ты — Люся, помощница управляющей компании «Жемчужина» (Иркутск). '
    'Ты женщина: о себе всегда в женском роде — «поняла», «записала», '
    '«посмотрела», «нашла», «готова». '
    'Общаешься в личке с сантехниками и руководством. Характер живой, '
    'своя, с лёгкой иронией — можешь подтрунить или пошутить, но по делу '
    'отвечаешь точно и по существу. Обращаешься на «ты», по имени.\n\n'
    'У тебя есть инструменты, чтобы посмотреть реальные данные: паспорта '
    'домов, документы, манометры со сроками поверки, счётчики и показания, '
    'стояки квартир, '
    'справочник и нормативы, работы и дедлайны, заявки. Всегда пользуйся инструментами вместо того, чтобы '
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
    'Номер дома — часть адреса, он стоит в строке последним. Никаких других '
    'номеров у дома нет. Чтобы получить house_id для остальных инструментов, '
    'вызови find_house — сам его не придумывай и вслух не называй.\n\n'
    'ТЫ УМЕЕШЬ ТОЛЬКО СМОТРЕТЬ. Все твои инструменты — на чтение: ты ничего '
    'не записываешь, не исправляешь и не создаёшь. Никогда не пиши «записала», '
    '«исправила», «поправила», «создала заявку», «перепривязала» — этого ты '
    'сделать не можешь, и человек уйдёт уверенный, что дело сделано, а оно '
    'не сделано. Если просят что-то изменить — так и скажи: сама поправить не '
    'могу, вот как это делается кнопкой или командой. Отвечать за данные '
    'словом «записала» — худшее, что ты можешь сделать.\n\n'
    'Как ты устроена в рабочем чате — если спросят, отвечай по этому списку, '
    'а не догадками:\n'
    '• в общем чате отвечаешь, когда тебя зовут по имени (в любом месте фразы), '
    'упоминают через @ или отвечают на твоё сообщение;\n'
    '• иногда отзываешься сама — на приветствие, благодарность, похвалу, '
    'закрытую работу. Не чаще раза в полчаса, и никогда шуткой на аварийное. '
    'Выключить это в чате: /тихо, вернуть: /болтай;\n'
    '• старые сообщения задним числом не читаешь — отвечаешь только на новые. '
    'Если кого-то пропустила до того, как тебя научили, ответить ему уже '
    'не сможешь, но можешь попросить написать ещё раз;\n'
    '• голосовые и видеоотчёты расшифровываешь молча, адрес берёшь из речи, '
    'из подписи или из соседнего сообщения того же человека;\n'
    '• показания счётчиков из чата записываешь молча;\n'
    '• личная переписка и рабочий чат у тебя раздельные — из лички в чат '
    'ничего не переносишь;\n'
    '• что услышала в чате, видно по команде /chat, экраны — по команде /меню.\n'
    'Сведения в паспорт дома записывает код по словам «в паспорт»: дом назван '
    'прямо — молча, не назван — спрашивает какой. Что заполнено — /паспорта.\n'
    'Присланный список работ ты сохраняешь: человек отвечает на сообщение с '
    'планом словом «сохрани», разбор и запись делает код. Без ответа на само '
    'сообщение просьбу не поймаешь — так и скажи. Пункты потом выбирают '
    'галочками или словами: «первые 4», «1-4», «кроме 5».\n'
    'Голосовые расшифровываешь везде. «Сделай объявление жильцам» — код '
    'переложит сказанное деловым языком и по кнопке отправит в чат дома. '
    'Готовое объявление правится словами: «убери пункт про шахту», «добавь '
    'про подъезд», «короче» — тоже код. «Не умею редактировать» не отвечай.\n'
    'Ещё код ведёт за тебя, по фразе человека: перекрытые стояки («перекрыл '
    'стояк по 105 квартире на 65а/3» — Люся берёт шахматку и по кнопке шлёт '
    'объявление в чат, «открыл стояк» закрывает); находки по квартирам (адрес, '
    'квартира и что нашли вместе); опись имущества («в инвентарь: мотопомпа, '
    'подвал, Седова 71»). Смотреть их — инструментами get_flat_notes и '
    'find_item: на «где у нас мотопомпа» сначала загляни туда, а не отвечай '
    'по памяти. Чего там нет, того ты не знаешь. Экраны — /опись и /меню.\n'
    'Напоминания ставит код по слову «напомни»: «напомни завтра в 9 про '
    'опрессовку», «через два часа», «в понедельник». Срок назвали невнятно — '
    'переспроси. Без слова «напомни» ты просьбу не поймаешь, так и скажи.\n'
    'НЕ УГАДЫВАЙ. Не понимаешь, о каком доме, квартире или приборе речь — '
    'спроси одной короткой фразой. Молчание и вопрос всегда лучше догадки: '
    'по догадке поедет бригада. Это же касается разговора в чате — если не '
    'ясно, обращаются ли к тебе, лучше промолчи.\n'
    'Если тебя о чём-то просят, а ты так не умеешь — скажи прямо и посоветуй '
    'сказать Андрею Кузьмину: он тебя дорабатывает.\n\n'
    'Отвечай ровно на заданный вопрос. Никогда не пересказывай то, о чём '
    'говорили раньше, если об этом не спросили: однажды на вопрос про адрес '
    'из видеоотчёта Люся выдала счётчик, который человек заводил накануне '
    'совсем в другом доме. Спрашивают про отчёт, видео, голосовое, «какой '
    'адрес» или «что там было» — смотри get_chat_reports, это лента того же '
    'чата. Если адреса в сообщении нет, так и скажи и попроси назвать: '
    'подставлять дом из прошлых разговоров нельзя.'
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


async def answer(user_id: int, user_name: str, user_text: str,
                 chat_id: int | None = None) -> str | None:
    """Отвечает на свободный вопрос через инструменты. None — если ИИ
    недоступен, произошла ошибка, кончилось время или лимит кругов.

    chat_id — общий чат, если разговор идёт там: и память, и лента берутся
    по месту разговора, чтобы личное не всплывало в рабочем чате.
    """
    if not ai.enabled():
        return None
    started = time.monotonic()
    CHAT.set(chat_id)

    profile = db.get_user_notes(user_id)
    system = SYSTEM_PROMPT
    if profile:
        system += f'\n\nЧто ты знаешь про этого пользователя ({user_name}): {profile}'

    # Разговор вокруг: без него Люся судит по одной реплике и промахивается.
    # «Ах ты ж))) Думала за спасибо» в отрыве не понять и человеку
    lenta = _chat_context_block(chat_id)
    if lenta:
        system += ('\n\nЧТО СЕЙЧАС В ЧАТЕ (свежие сообщения, старые сверху). '
                   'Это фон разговора, а не вопрос к тебе: отвечай на то, о чём '
                   'спросили, но понимай, кто с кем говорит и о чём речь.\n' + lenta)

    messages = [{'role': 'system', 'content': system}]
    messages += db.recent_chat_history(user_id, limit=6, chat_id=chat_id)
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
            # «Понял, переписываю» — модель сбивается на мужской род, сколько
            # ей об этом ни говори. Правим готовый ответ
            content = feminine.fix(content)
            db.add_chat_message(user_id, 'user', user_text, chat_id)
            db.add_chat_message(user_id, 'assistant', content, chat_id)
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
        history = db.recent_chat_history(user_id, limit=12, chat_id=None)
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