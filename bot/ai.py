"""Подключение ИИ через OpenRouter (OpenAI-совместимый API, бесплатная модель).

Переменные окружения:
  OPENROUTER_API_KEY — ключ OpenRouter (тот же, что в вашем телеграм-боте).
                        Без него ИИ-функции просто отключены, остальное
                        работает как обычно.
  OPENROUTER_MODEL    — идентификатор модели (по умолчанию бесплатная
                        moonshotai/kimi-k2:free).
"""
import logging
import os

import aiohttp

log = logging.getLogger('ai')

OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'
KIMI_API_KEY = os.environ.get('OPENROUTER_API_KEY')
KIMI_MODEL = os.environ.get('OPENROUTER_MODEL', 'moonshotai/kimi-k2:free')

SYSTEM_PROMPT = (
    'Ты — Люся, помощница управляющей компании «Жемчужина» (Иркутск). '
    'Ты дружелюбная и деловая, пишешь по-русски, коротко и по существу, '
    'обращаешься к руководству уважительно, но без канцелярита. '
    'Ты работаешь с данными о домах, заявках, работах сантехников и показаниях счётчиков.'
)


def enabled() -> bool:
    return bool(KIMI_API_KEY)


async def ask(user_text: str, system: str = SYSTEM_PROMPT,
              max_tokens: int = 900, temperature: float = 0.4) -> str | None:
    """Один запрос к Kimi. Возвращает текст ответа или None при ошибке."""
    if not enabled():
        return None
    payload = {
        'model': KIMI_MODEL,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_text},
        ],
        'max_tokens': max_tokens,
        'temperature': temperature,
    }
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
                return data['choices'][0]['message']['content'].strip()
    except Exception:
        log.exception('Ошибка запроса к OpenRouter')
        return None
