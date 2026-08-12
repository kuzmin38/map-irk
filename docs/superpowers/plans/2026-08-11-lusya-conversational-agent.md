# Люся: разговорный агент (личка) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Люся отвечает на свободный текст в личных диалогах, используя ИИ с
tool calling поверх реальных данных (дома, паспорта, документы, стояки,
справочник, работы, заявки), с долгосрочной памятью в `bot.db`.

**Architecture:** Новый модуль `bot/agent.py` — tool-calling цикл поверх
`bot/ai.py` (OpenRouter). Встраивается последним шагом в `on_text`
(`bot/handlers.py`), после существующих быстрых путей (диалоги, стояки,
поиск дома). Долгосрочная память — две новые таблицы в `bot/db.py`
(`user_notes`, `chat_history`), уже живущие на смонтированном volume Railway.

**Tech Stack:** Python 3.11, aiohttp, OpenRouter (`moonshotai/kimi-k2`,
платный), SQLite, pytest + pytest-asyncio для тестов.

## Global Constraints

- Только чтение данных через инструменты агента — никаких создающих/
  изменяющих действий через свободный текст (заявки/паспорт/показания
  остаются пошаговыми диалогами через кнопки).
- Модель — платная `moonshotai/kimi-k2` через `OPENROUTER_API_KEY`
  (тот же ключ, что уже используется в `bot/ai.py`).
- Максимум 4 круга tool calling за один ответ — защита от зацикливания.
- Память диалога — последние 6 реплик из `chat_history` в `bot.db`.
- Никакого выхода в интернет/веб-поиска — экспертиза только на встроенных
  знаниях модели + `directory.json`.
- Характер: своя, с лёгкой иронией, на «ты», по имени; но точна по делу.
- Любая ошибка ИИ (нет ключа, таймаут, лимит кругов) → тихий откат к
  прежнему поведению «🤷‍♀️ ничего не нашла», без traceback пользователю.

---

## File Structure

- Modify: `bot/db.py` — добавить таблицы `user_notes`, `chat_history` и
  CRUD-функции.
- Modify: `bot/ai.py` — добавить низкоуровневую `chat()` с поддержкой
  `tools`, переписать `ask()` через неё (поведение не меняется).
- Modify: `bot/handlers.py` — поправить устаревшее упоминание
  `KIMI_API_KEY` в сообщении об отключённом ИИ; подключить `bot/agent.py`
  как последний fallback в `on_text`.
- Create: `bot/agent.py` — список инструментов, их реализация поверх
  `houses`/`risers`/`db`/`directory.json`, цикл `answer()`, обновление
  профиля пользователя.
- Modify: `requirements.txt` — добавить `pytest`, `pytest-asyncio`.
- Create: `pytest.ini` — `asyncio_mode = auto`.
- Create: `tests/test_db_memory.py`
- Create: `tests/test_agent_tools.py`
- Create: `tests/test_agent_answer.py`

---

### Task 1: Долгосрочная память в bot.db

**Files:**
- Modify: `bot/db.py` (добавить в `init()`, добавить функции в конец файла)
- Modify: `requirements.txt`
- Create: `pytest.ini`
- Test: `tests/test_db_memory.py`

**Interfaces:**
- Produces: `db.get_user_notes(user_id: int) -> str`,
  `db.set_user_notes(user_id: int, profile: str) -> None`,
  `db.add_chat_message(user_id: int, role: str, content: str) -> None`,
  `db.recent_chat_history(user_id: int, limit: int = 6) -> list[dict]`
  (каждый элемент `{'role': ..., 'content': ...}`, от старых к новым).

- [ ] **Step 1: Добавить зависимости для тестов**

В `requirements.txt` добавить две строки в конец файла:

```
pytest
pytest-asyncio
```

- [ ] **Step 2: Установить зависимости**

Run: `pip install -r requirements.txt`
Expected: `pytest` и `pytest-asyncio` устанавливаются без ошибок.

- [ ] **Step 3: Создать pytest.ini**

Создать файл `pytest.ini` в корне репозитория:

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 4: Написать падающий тест для user_notes**

Создать `tests/test_db_memory.py`:

```python
import pytest

from bot import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


def test_user_notes_empty_by_default():
    assert db.get_user_notes(42) == ''


def test_user_notes_set_and_get():
    db.set_user_notes(42, 'Любит точные ответы, часто спрашивает про Байкальскую.')
    assert db.get_user_notes(42) == 'Любит точные ответы, часто спрашивает про Байкальскую.'


def test_user_notes_update_overwrites():
    db.set_user_notes(42, 'Первая заметка')
    db.set_user_notes(42, 'Вторая заметка')
    assert db.get_user_notes(42) == 'Вторая заметка'


def test_chat_history_empty_by_default():
    assert db.recent_chat_history(42) == []


def test_chat_history_roundtrip_order():
    db.add_chat_message(42, 'user', 'Привет')
    db.add_chat_message(42, 'assistant', 'Привет!')
    db.add_chat_message(42, 'user', 'Как дела?')
    history = db.recent_chat_history(42, limit=6)
    assert [m['content'] for m in history] == ['Привет', 'Привет!', 'Как дела?']
    assert [m['role'] for m in history] == ['user', 'assistant', 'user']


def test_chat_history_limit_keeps_most_recent():
    for i in range(10):
        db.add_chat_message(42, 'user', f'сообщение {i}')
    history = db.recent_chat_history(42, limit=4)
    assert [m['content'] for m in history] == [f'сообщение {i}' for i in range(6, 10)]


def test_chat_history_scoped_per_user():
    db.add_chat_message(1, 'user', 'от первого')
    db.add_chat_message(2, 'user', 'от второго')
    assert [m['content'] for m in db.recent_chat_history(1)] == ['от первого']
```

- [ ] **Step 5: Запустить тесты, убедиться что падают**

Run: `pytest tests/test_db_memory.py -v`
Expected: FAIL — `AttributeError: module 'bot.db' has no attribute 'get_user_notes'`
(и аналогично для остальных функций).

- [ ] **Step 6: Добавить таблицы в db.init()**

В `bot/db.py`, внутри функции `init()`, после блока `docs` (последний
`c.execute` перед закрывающей скобкой функции), добавить:

```python
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
```

- [ ] **Step 7: Добавить CRUD-функции**

В конец `bot/db.py` добавить новую секцию:

```python
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
```

- [ ] **Step 8: Запустить тесты, убедиться что проходят**

Run: `pytest tests/test_db_memory.py -v`
Expected: PASS (все 7 тестов)

- [ ] **Step 9: Commit**

```bash
git add bot/db.py requirements.txt pytest.ini tests/test_db_memory.py
git commit -m "feat: долгосрочная память агента в bot.db (user_notes, chat_history)"
```

---

### Task 2: chat() с tool calling поверх OpenRouter

**Files:**
- Modify: `bot/ai.py`
- Modify: `bot/handlers.py:1071-1073` (устаревшее упоминание KIMI_API_KEY)

**Interfaces:**
- Consumes: ничего нового (использует существующие `KIMI_API_KEY`,
  `KIMI_MODEL`, `OPENROUTER_BASE_URL`, `enabled()` из `bot/ai.py`).
- Produces: `ai.chat(messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 900, temperature: float = 0.4) -> dict | None`
  — возвращает `message` dict модели (ключи `role`, `content`,
  опционально `tool_calls`) или `None` при ошибке/отключённом ИИ.
  `ai.ask(...)` сохраняет прежнюю сигнатуру и поведение, реализована
  через `chat()`.

Эта задача без отдельного теста: `chat()` — тонкая обёртка над HTTP-вызовом
(аналогично уже непокрытому тестами `ask()`), а её поведение при
tool calling проверяется в Task 3 через мок на уровне `agent.py`.

- [ ] **Step 1: Переключить модель на платную**

В `bot/ai.py` заменить:

```python
KIMI_MODEL = os.environ.get('OPENROUTER_MODEL', 'moonshotai/kimi-k2:free')
```

на:

```python
KIMI_MODEL = os.environ.get('OPENROUTER_MODEL', 'moonshotai/kimi-k2')
```

- [ ] **Step 2: Добавить chat() и переписать ask() через неё**

В `bot/ai.py` заменить всю функцию `ask()` (и код ниже неё до конца файла)
на:

```python
async def chat(messages: list[dict], tools: list[dict] | None = None,
                max_tokens: int = 900, temperature: float = 0.4) -> dict | None:
    """Запрос к OpenRouter chat.completions с произвольными messages и,
    опционально, инструментами. Возвращает message модели (dict, может
    содержать tool_calls) или None при ошибке/отключённом ИИ."""
    if not enabled():
        return None
    payload = {
        'model': KIMI_MODEL,
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': temperature,
    }
    if tools:
        payload['tools'] = tools
    headers = {
        'Authorization': f'Bearer {KIMI_API_KEY}',
        'HTTP-Referer': 'https://github.com/kuzmin38/map-irk',
        'X-Title': 'Lusya Bot',
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f'{OPENROUTER_BASE_URL}/chat/completions',
                              json=payload, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=90)) as resp:
                data = await resp.json()
                if resp.status != 200:
                    log.error('OpenRouter API %s: %s', resp.status, data)
                    return None
                return data['choices'][0]['message']
    except Exception:
        log.exception('Ошибка запроса к OpenRouter')
        return None


async def ask(user_text: str, system: str = SYSTEM_PROMPT,
              max_tokens: int = 900, temperature: float = 0.4) -> str | None:
    """Один запрос к модели без инструментов. Возвращает текст ответа или None."""
    message = await chat(
        [{'role': 'system', 'content': system}, {'role': 'user', 'content': user_text}],
        max_tokens=max_tokens, temperature=temperature,
    )
    if message is None:
        return None
    return (message.get('content') or '').strip() or None
```

- [ ] **Step 3: Проверить, что существующая функция брифинга не сломалась**

Run: `python -c "import ast; ast.parse(open('bot/ai.py', encoding='utf-8').read())"`
Expected: без ошибок (синтаксически файл корректен).

- [ ] **Step 4: Починить устаревшее сообщение в handlers.py**

В `bot/handlers.py` найти (около строки 1071-1073):

```python
        if not ai.enabled():
            await send(msg, '🧠 ИИ пока не подключён: задайте переменную окружения KIMI_API_KEY '
                            'при запуске бота (ключ — тот же, что в телеграм-боте на Kimi).')
            return
```

Заменить на:

```python
        if not ai.enabled():
            await send(msg, '🧠 ИИ пока не подключён: задайте переменную окружения '
                            'OPENROUTER_API_KEY при запуске бота.')
            return
```

- [ ] **Step 5: Commit**

```bash
git add bot/ai.py bot/handlers.py
git commit -m "feat: ai.chat() с поддержкой tool calling, платная модель kimi-k2"
```

---

### Task 3: Инструменты и цикл агента (bot/agent.py)

**Files:**
- Create: `bot/agent.py`
- Test: `tests/test_agent_tools.py`
- Test: `tests/test_agent_answer.py`

**Interfaces:**
- Consumes: `ai.chat()`, `ai.enabled()`, `ai.ask()` (из Task 2);
  `db.get_user_notes`, `db.set_user_notes`, `db.add_chat_message`,
  `db.recent_chat_history`, `db.get_passport`, `db.list_docs`,
  `db.list_works`, `db.list_requests`, `db.WORK_LABELS`, `db.STATUS_LABELS`
  (из Task 1 и существующего `db.py`); `houses.search`, `houses.HOUSES`,
  `houses.HOUSES_BY_ID` (существующий `houses.py`); `risers.locate`,
  `risers.riser_flats` под именем `risers_mod` (существующий `risers.py`).
- Produces: `agent.answer(user_id: int, user_name: str, user_text: str) -> str | None`
  — `None`, если ИИ недоступен/ошибка/лимит кругов исчерпан, иначе текст
  ответа (уже записанный в `chat_history`).

- [ ] **Step 1: Написать падающие тесты на инструменты**

Создать `tests/test_agent_tools.py`:

```python
import json

import pytest

from bot import agent, db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()


def _house_id(address):
    return next(h['id'] for h in agent.houses.HOUSES if h['address'] == address)


def test_find_house_known_address():
    result = json.loads(agent._tool_find_house('Байкальская 99'))
    addresses = [h['address'] for h in result['found']]
    assert 'Байкальская 99' in addresses


def test_find_house_unknown_address():
    result = json.loads(agent._tool_find_house('Несуществующая улица 999'))
    assert result['found'] == []


def test_get_passport_empty():
    house_id = _house_id('Байкальская 99')
    result = json.loads(agent._tool_get_passport(house_id))
    assert result['passport'] == {}
    assert 'не заполнен' in result['note']


def test_get_passport_filled():
    house_id = _house_id('Байкальская 99')
    db.set_passport_field(house_id, 'year', '1985', 'тест')
    result = json.loads(agent._tool_get_passport(house_id))
    assert result['passport']['Год постройки'] == '1985'


def test_get_passport_unknown_house():
    result = json.loads(agent._tool_get_passport(999999))
    assert 'error' in result


def test_get_riser_known():
    result = json.loads(agent._tool_get_riser('4-я Советская 30', 1))
    assert result['floor'] == 2
    assert result['riser'] == 1
    assert result['flats_on_floor'] == 8


def test_get_riser_unknown_flat():
    result = json.loads(agent._tool_get_riser('4-я Советская 30', 9999))
    assert 'error' in result


def test_get_directory_all():
    result = json.loads(agent._tool_get_directory('all'))
    ids = [s['id'] for s in result]
    assert 'norms' in ids


def test_get_directory_section():
    result = json.loads(agent._tool_get_directory('norms'))
    assert 'НОРМАТИВЫ' in result['text']


def test_get_directory_unknown_section():
    result = json.loads(agent._tool_get_directory('bogus'))
    assert 'error' in result


def test_list_docs_empty():
    house_id = _house_id('Байкальская 99')
    result = json.loads(agent._tool_list_docs(house_id))
    assert result['docs'] == []


def test_get_house_works_empty():
    house_id = _house_id('Байкальская 99')
    result = json.loads(agent._tool_get_house_works(house_id))
    assert result['works'] == []


def test_get_house_works_with_data():
    house_id = _house_id('Байкальская 99')
    db.add_work(house_id, 'Опрессовка', '2026-09-01', 'Тест')
    result = json.loads(agent._tool_get_house_works(house_id))
    assert result['works'][0]['title'] == 'Опрессовка'


def test_get_open_requests_filtered_by_house():
    house_id = _house_id('Байкальская 99')
    other_id = _house_id('Байкальская 87')
    db.add_request(house_id, 'Байкальская 99', 'Течёт кран', 1, 'Тест')
    db.add_request(other_id, 'Байкальская 87', 'Другая проблема', 1, 'Тест')
    result = json.loads(agent._tool_get_open_requests(house_id))
    assert len(result['requests']) == 1
    assert result['requests'][0]['description'] == 'Течёт кран'
```

- [ ] **Step 2: Запустить тесты, убедиться что падают**

Run: `pytest tests/test_agent_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.agent'`

- [ ] **Step 3: Создать bot/agent.py — инструменты**

Создать `bot/agent.py`:

```python
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
```

- [ ] **Step 4: Запустить тесты инструментов, убедиться что проходят**

Run: `pytest tests/test_agent_tools.py -v`
Expected: PASS (все тесты)

- [ ] **Step 5: Написать падающие тесты на цикл answer()**

Создать `tests/test_agent_answer.py`:

```python
import json

import pytest

from bot import agent, db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    db.init()
    monkeypatch.setattr(agent.ai, 'enabled', lambda: True)
    monkeypatch.setattr(agent, '_update_profile', _noop_update_profile)


async def _noop_update_profile(*args, **kwargs):
    pass


async def test_answer_calls_tool_and_returns_final_text(monkeypatch):
    calls = []

    async def fake_chat(messages, tools=None, max_tokens=900, temperature=0.4):
        calls.append(messages)
        if len(calls) == 1:
            return {
                'role': 'assistant', 'content': None,
                'tool_calls': [{'id': 'call_1', 'type': 'function', 'function': {
                    'name': 'find_house',
                    'arguments': json.dumps({'query': 'Байкальская 99'})}}],
            }
        return {'role': 'assistant', 'content': 'Байкальская 99 — наш дом.'}

    monkeypatch.setattr(agent.ai, 'chat', fake_chat)

    result = await agent.answer(1, 'Андрей', 'Байкальская 99 наш дом?')

    assert result == 'Байкальская 99 — наш дом.'
    assert len(calls) == 2
    assert calls[1][-1]['role'] == 'tool'
    assert calls[1][-1]['tool_call_id'] == 'call_1'
    tool_result = json.loads(calls[1][-1]['content'])
    assert any(h['address'] == 'Байкальская 99' for h in tool_result['found'])

    history = db.recent_chat_history(1, limit=10)
    assert history[-2] == {'role': 'user', 'content': 'Байкальская 99 наш дом?'}
    assert history[-1] == {'role': 'assistant', 'content': 'Байкальская 99 — наш дом.'}


async def test_answer_returns_none_when_ai_disabled(monkeypatch):
    monkeypatch.setattr(agent.ai, 'enabled', lambda: False)
    result = await agent.answer(1, 'Андрей', 'привет')
    assert result is None


async def test_answer_returns_none_after_max_rounds(monkeypatch):
    async def fake_chat(messages, tools=None, max_tokens=900, temperature=0.4):
        return {
            'role': 'assistant', 'content': None,
            'tool_calls': [{'id': 'call_x', 'type': 'function', 'function': {
                'name': 'get_directory', 'arguments': '{"section": "all"}'}}],
        }

    monkeypatch.setattr(agent.ai, 'chat', fake_chat)

    result = await agent.answer(1, 'Андрей', 'бесконечный вопрос')
    assert result is None


async def test_answer_uses_stored_profile_in_system_prompt(monkeypatch):
    db.set_user_notes(1, 'Часто спрашивает про Байкальскую 99.')
    captured = {}

    async def fake_chat(messages, tools=None, max_tokens=900, temperature=0.4):
        captured['system'] = messages[0]['content']
        return {'role': 'assistant', 'content': 'Ок.'}

    monkeypatch.setattr(agent.ai, 'chat', fake_chat)

    await agent.answer(1, 'Андрей', 'привет')
    assert 'Часто спрашивает про Байкальскую 99.' in captured['system']
```

- [ ] **Step 6: Запустить тесты, убедиться что падают**

Run: `pytest tests/test_agent_answer.py -v`
Expected: FAIL — `AttributeError: module 'bot.agent' has no attribute 'answer'`

- [ ] **Step 7: Добавить цикл answer() и обновление профиля в bot/agent.py**

В конец `bot/agent.py` добавить:

```python
SYSTEM_PROMPT = (
    'Ты — Люся, помощница управляющей компании «Жемчужина» (Иркутск). '
    'Общаешься в личке с сантехниками и руководством. Характер живой, '
    'своя, с лёгкой иронией — можешь подтрунить или пошутить, но по делу '
    'отвечаешь точно и по существу. Обращаешься на «ты», по имени.\n\n'
    'У тебя есть инструменты, чтобы посмотреть реальные данные: дома, '
    'паспорта домов, документы, стояки квартир, справочник и нормативы, '
    'работы и дедлайны, заявки. Всегда пользуйся инструментами вместо '
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
```

- [ ] **Step 8: Запустить тесты, убедиться что проходят**

Run: `pytest tests/test_agent_answer.py tests/test_agent_tools.py tests/test_db_memory.py -v`
Expected: PASS (все тесты во всех трёх файлах)

- [ ] **Step 9: Commit**

```bash
git add bot/agent.py tests/test_agent_tools.py tests/test_agent_answer.py
git commit -m "feat: bot/agent.py — инструменты чтения данных и цикл tool calling"
```

---

### Task 4: Подключить агента в on_text

**Files:**
- Modify: `bot/handlers.py:22` (импорт)
- Modify: `bot/handlers.py` (конец `on_text`, ветка «дом не найден»)

**Interfaces:**
- Consumes: `agent.answer(user_id, user_name, user_text) -> str | None`
  (из Task 3).

- [ ] **Step 1: Добавить импорт agent**

В `bot/handlers.py` заменить:

```python
from . import ai, db, houses
```

на:

```python
from . import agent, ai, db, houses
```

- [ ] **Step 2: Подключить агента как fallback после поиска дома**

В `bot/handlers.py`, в конце функции `on_text`, найти:

```python
    # Режим по умолчанию — поиск дома по адресу
    found = houses.search(text)
    if not found:
        await send(event.message,
                   f'🤷‍♀️ По запросу «{text}» я ничего не нашла.\n'
                   'Попробуйте написать иначе, например: «Розы Люксембург 118» или «Байкальская 237».',
                   main_menu_kb())
    elif len(found) == 1:
```

Заменить на:

```python
    # Режим по умолчанию — поиск дома по адресу
    found = houses.search(text)
    if not found:
        ai_answer = await agent.answer(uid, _uname(event), text)
        if ai_answer:
            await send(event.message, ai_answer)
        else:
            await send(event.message,
                       f'🤷‍♀️ По запросу «{text}» я ничего не нашла.\n'
                       'Попробуйте написать иначе, например: «Розы Люксембург 118» или «Байкальская 237».',
                       main_menu_kb())
    elif len(found) == 1:
```

- [ ] **Step 3: Проверить синтаксис**

Run: `python -c "import ast; ast.parse(open('bot/handlers.py', encoding='utf-8').read())"`
Expected: без ошибок

- [ ] **Step 4: Прогнать весь набор тестов**

Run: `pytest -v`
Expected: PASS (все тесты проекта)

- [ ] **Step 5: Commit**

```bash
git add bot/handlers.py
git commit -m "feat: подключить разговорного агента как fallback в on_text"
```

- [ ] **Step 6: Задеплоить и проверить руками**

Запушить в `main` (Railway автодеплоится с этой ветки), задать
`OPENROUTER_API_KEY` в Variables сервиса на Railway, если ещё не задан.
Проверить в MAX личным сообщением боту: вопрос, не покрытый старыми
парсерами (например, «что с заявками по Байкальской 99» или «расскажи про
паспорт дома на Пискунова 148») — Люся должна ответить по существу, а не
показать «ничего не нашла».

---

## Self-Review Notes

- Все разделы спеки покрыты: инструменты (Task 3), fallback-встраивание,
  не трогающее рабочие пути (Task 4), долгосрочная память в bot.db без
  Obsidian (Task 1), платная модель (Task 2), экспертиза без веб-поиска
  (системный промпт в Task 3, `get_directory` как единственный источник
  нормативов помимо знаний модели), обработка ошибок (везде `None` →
  тихий откат на вызывающей стороне).
- Типы согласованы: `agent.answer(user_id: int, user_name: str, user_text: str) -> str | None`
  используется в Task 4 ровно с этой сигнатурой; `ai.chat(messages, tools=None, ...) -> dict | None`
  используется в Task 3 ровно с этой сигнатурой.
- Создание/редактирование данных через текст сознательно не реализуется —
  вне рамок v1 по спеке.
