"""Протоколы планёрок: из расшифровки записи — итоги, решения и задачи.

Расшифровку даёт `bot.transcribe`, здесь она превращается в разбор совещания:
о чём говорили, что решили, кому что делать и к какому сроку. Задачи Люся
отдаёт в том же виде, в каком живут работы по домам, — чтобы одной кнопкой
завести их в «📅 Все работы».
"""
import json
import logging
import re

from . import ai

log = logging.getLogger('meetings')

# Сколько расшифровки отдаём модели: полтора часа речи — это примерно 60 тысяч
# знаков, столько в один запрос не нужно, важное обычно проговаривают заново.
MAX_TRANSCRIPT_CHARS = 40000

PROMPT = (
    'Ты ведёшь протокол планёрки управляющей компании «Жемчужина» (Иркутск): '
    'обсуждают дома, аварии, заявки, работы сантехников, сроки и материалы.\n\n'
    'Ниже — расшифровка записи совещания. Разбери её и верни ТОЛЬКО JSON, '
    'без пояснений и без markdown-разметки, по схеме:\n'
    '{\n'
    '  "title": "тема планёрки, 3-6 слов",\n'
    '  "summary": "о чём говорили: 2-4 предложения по существу",\n'
    '  "decisions": ["принятые решения, по одному в строке"],\n'
    '  "tasks": [{"title": "что сделать", "assignee": "имя или null", '
    '"deadline": "ДД.ММ.ГГГГ или null", "address": "адрес дома или null"}],\n'
    '  "questions": ["вопросы, оставшиеся без ответа"]\n'
    '}\n\n'
    'Правила: пиши по-русски; ничего не выдумывай — только сказанное на записи; '
    'если раздел пустой, оставь пустой список; в задачах формулируй действие '
    '(«заменить манометр на подаче»), а не пересказ разговора; имя ответственного '
    'бери так, как его называли на планёрке.\n\n'
    'Расшифровка:\n')


def parse_json(text: str) -> dict | None:
    """Достаёт JSON из ответа модели: она любит обернуть его в ```json."""
    if not text:
        return None
    text = text.strip()
    fence = re.search(r'```(?:json)?\s*(.+?)```', text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        log.warning('Модель вернула не JSON: %.200s', text)
        return None
    return data if isinstance(data, dict) else None


def _clean_list(value) -> list:
    """Список строк из того, что вернула модель (бывает строка или мусор)."""
    if isinstance(value, str):
        value = [v for v in value.split('\n')]
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip().lstrip('-•').strip())
    return out


def _clean_tasks(value) -> list:
    """Задачи в предсказуемом виде: title обязателен, остальное — как получится."""
    if not isinstance(value, list):
        return []
    tasks = []
    for item in value:
        if isinstance(item, str) and item.strip():
            tasks.append({'title': item.strip(), 'assignee': None,
                          'deadline': None, 'address': None})
            continue
        if not isinstance(item, dict):
            continue
        title = (item.get('title') or item.get('task') or '').strip()
        if not title:
            continue

        def field(name):
            v = item.get(name)
            v = v.strip() if isinstance(v, str) else None
            return v if v and v.lower() not in ('null', 'none', 'нет', '-') else None

        tasks.append({'title': title, 'assignee': field('assignee'),
                      'deadline': field('deadline'), 'address': field('address')})
    return tasks


def normalize(data: dict) -> dict:
    """Приводит ответ модели к одной форме — на неё опираются кнопки и вёрстка."""
    return {
        'title': (data.get('title') or '').strip() or 'Планёрка',
        'summary': (data.get('summary') or '').strip(),
        'decisions': _clean_list(data.get('decisions')),
        'tasks': _clean_tasks(data.get('tasks')),
        'questions': _clean_list(data.get('questions')),
    }


async def build_protocol(transcript: str) -> dict | None:
    """Протокол из расшифровки. None — если ИИ выключен или не ответил."""
    if not transcript or not transcript.strip():
        return None
    body = transcript.strip()[:MAX_TRANSCRIPT_CHARS]
    answer = await ai.ask(PROMPT + body, max_tokens=2000, temperature=0.2)
    data = parse_json(answer or '')
    if data is None:
        if not answer:
            return None
        # Модель ответила прозой — сохраняем хотя бы пересказ, он тоже полезен
        return normalize({'summary': answer})
    return normalize(data)


def fmt_duration(seconds) -> str:
    if not seconds:
        return ''
    minutes = int(seconds) // 60
    if minutes < 60:
        return f'{minutes} мин'
    return f'{minutes // 60} ч {minutes % 60:02d} мин'


def format_protocol(data: dict, held_at: str = '', duration=None) -> str:
    """Протокол текстом — то, что Люся присылает в ответ на запись."""
    head = f"🗒 {data.get('title') or 'Планёрка'}"
    meta = ' · '.join(x for x in (held_at, fmt_duration(duration)) if x)
    lines = [head]
    if meta:
        lines.append(meta)
    if data.get('summary'):
        lines += ['', data['summary']]
    if data.get('decisions'):
        lines += ['', '✅ Решили:']
        lines += [f'• {d}' for d in data['decisions']]
    if data.get('tasks'):
        lines += ['', '📌 Задачи:']
        for i, t in enumerate(data['tasks'], 1):
            tail = ' · '.join(x for x in (t.get('assignee'),
                                          f"до {t['deadline']}" if t.get('deadline') else None,
                                          t.get('address')) if x)
            lines.append(f"{i}. {t['title']}" + (f'\n   {tail}' if tail else ''))
    if data.get('questions'):
        lines += ['', '❓ Без ответа:']
        lines += [f'• {q}' for q in data['questions']]
    if not any((data.get('summary'), data.get('decisions'),
                data.get('tasks'), data.get('questions'))):
        lines += ['', 'Разобрать запись не вышло — полная расшифровка ниже по кнопке.']
    return '\n'.join(lines)
