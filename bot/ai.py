"""Подключение ИИ через OpenRouter (OpenAI-совместимый API, бесплатная модель).

Переменные окружения:
  OPENROUTER_API_KEY — ключ OpenRouter (тот же, что в вашем телеграм-боте).
                        Без него ИИ-функции просто отключены, остальное
                        работает как обычно.
  OPENROUTER_MODEL    — идентификатор модели (по умолчанию бесплатная
                        moonshotai/kimi-k2:free).
"""
import asyncio
import logging
import os
import time

import aiohttp

log = logging.getLogger('ai')

OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'
KIMI_API_KEY = os.environ.get('OPENROUTER_API_KEY')
KIMI_MODEL = os.environ.get('OPENROUTER_MODEL', 'moonshotai/kimi-k2')

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
        # Одну и ту же модель на OpenRouter раздают разные поставщики, и по
        # скорости они отличаются в разы. Просим самого быстрого: для Люси
        # это разница между «ответила сразу» и «не дождались».
        'provider': {'sort': 'throughput'},
    }
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
                              timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as resp:
                data = await resp.json()
                if resp.status != 200:
                    log.error('OpenRouter API %s: %s', resp.status, data)
                    return None
                # Без этой строки непонятно, кто тормозит: модель или бот
                log.info('Модель %s ответила за %.1f с', KIMI_MODEL,
                         time.monotonic() - started)
                return data['choices'][0]['message']
    except asyncio.TimeoutError:
        # Обычное дело для перегруженной модели — трассировка тут только шумит
        log.warning('Модель %s не ответила за %s с', KIMI_MODEL, REQUEST_TIMEOUT)
        return None
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
