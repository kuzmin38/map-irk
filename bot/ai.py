"""Подключение ИИ через OpenRouter (OpenAI-совместимый API).

Все модели идут через один ключ и один счёт OpenRouter — и разговорная,
и та, что расшифровывает голос. Отдельно ничего оплачивать не нужно.

Переменные окружения:
  OPENROUTER_API_KEY   — ключ OpenRouter. Без него ИИ-функции отключены,
                         остальное работает как обычно.
  OPENROUTER_MODEL     — разговорная модель. По умолчанию gemini-2.5-flash:
                         на kimi-k2 Люся дописывала в отчёты работы, которых
                         не было, и хуже держала указания по-русски.
  OPENROUTER_FALLBACK  — запасная модель на случай, когда основная молчит.
"""
import asyncio
import logging
import os
import time

import aiohttp

log = logging.getLogger('ai')

OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'
KIMI_API_KEY = os.environ.get('OPENROUTER_API_KEY')
KIMI_MODEL = os.environ.get('OPENROUTER_MODEL', 'google/gemini-2.5-flash')
# Модель может молчать: перегружен поставщик, упал провайдер. Одна попытка
# на запасной — дешевле, чем «Люся не ответила»
FALLBACK_MODEL = os.environ.get('OPENROUTER_FALLBACK', 'moonshotai/kimi-k2')

# Модель для медленной работы: раз в сутки перечитать ленту и разложить её
# по домам. Там не нужна скорость, зато нужна голова — и обходится это
# недорого, потому что вызовов единицы, а не тысячи
SLOW_MODEL = os.environ.get('OPENROUTER_SLOW_MODEL', 'google/gemini-2.5-pro')
SLOW_TIMEOUT = 180

# Ждать модель полторы минуты нельзя: человек в мессенджере успевает решить,
# что бот сломался. Лучше честно не ответить, чем молчать.
REQUEST_TIMEOUT = 30

SYSTEM_PROMPT = (
    'Ты — Люся, помощница управляющей компании «Жемчужина» (Иркутск). '
    'Ты дружелюбная и деловая, пишешь по-русски, коротко и по существу, '
    'обращаешься к руководству уважительно, но без канцелярита. '
    'Ты работаешь с данными о домах, заявках, работах сантехников и показаниях счётчиков.'
)


def enabled() -> bool:
    return bool(KIMI_API_KEY)


async def chat(messages: list[dict], tools: list[dict] | None = None,
               max_tokens: int = 900, temperature: float = 0.4,
               model: str | None = None, timeout: int | None = None) -> dict | None:
    """Запрос к модели с инструментами. None — если не вышло и с запасной."""
    if not enabled():
        return None
    osnovnaya = model or KIMI_MODEL
    message = await _one_call(osnovnaya, messages, tools, max_tokens, temperature,
                              timeout)
    if message is None and FALLBACK_MODEL and FALLBACK_MODEL != osnovnaya:
        log.warning('Основная модель не ответила — пробую %s', FALLBACK_MODEL)
        message = await _one_call(FALLBACK_MODEL, messages, tools,
                                  max_tokens, temperature, timeout)
    return message


async def _one_call(model: str, messages: list[dict], tools, max_tokens: int,
                    temperature: float, timeout: int | None = None) -> dict | None:
    payload = {
        'model': model,
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': temperature,
        # Одну и ту же модель на OpenRouter раздают разные поставщики, и по
        # скорости они отличаются в разы. Просим самого быстрого: для Люси
        # это разница между «ответила сразу» и «не дождались».
        'provider': {'sort': 'throughput'},
    }
    srok = timeout or REQUEST_TIMEOUT
    if tools:
        payload['tools'] = tools
    headers = {
        'Authorization': f'Bearer {KIMI_API_KEY}',
        'HTTP-Referer': 'https://github.com/kuzmin38/map-irk',
        'X-Title': 'Lusya Bot',
    }
    started = time.monotonic()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f'{OPENROUTER_BASE_URL}/chat/completions',
                              json=payload, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=srok)) as resp:
                data = await resp.json()
                if resp.status != 200:
                    log.error('OpenRouter API %s: %s', resp.status, data)
                    return None
                # Без этой строки непонятно, кто тормозит: модель или бот
                log.info('Модель %s ответила за %.1f с', model,
                         time.monotonic() - started)
                return data['choices'][0]['message']
    except asyncio.TimeoutError:
        # Обычное дело для перегруженной модели — трассировка тут только шумит
        log.warning('Модель %s не ответила за %s с', model, srok)
        return None
    except Exception:
        log.exception('Ошибка запроса к OpenRouter')
        return None


async def ask(user_text: str, system: str = SYSTEM_PROMPT,
              max_tokens: int = 900, temperature: float = 0.4,
              model: str | None = None, timeout: int | None = None) -> str | None:
    """Один запрос к модели без инструментов. Возвращает текст ответа или None."""
    message = await chat(
        [{'role': 'system', 'content': system}, {'role': 'user', 'content': user_text}],
        max_tokens=max_tokens, temperature=temperature, model=model, timeout=timeout,
    )
    if message is None:
        return None
    return (message.get('content') or '').strip() or None
