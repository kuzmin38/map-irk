"""Подключение ИИ (Kimi / Moonshot AI, OpenAI-совместимый API).

Переменные окружения:
  KIMI_API_KEY  — ключ API (тот же, что в вашем телеграм-боте). Без него
                  ИИ-функции просто отключены, остальное работает как обычно.
  KIMI_BASE_URL — базовый URL API (по умолчанию https://api.moonshot.ai/v1;
                  для китайского контура — https://api.moonshot.cn/v1).
  KIMI_MODEL    — идентификатор модели. Поставьте ту же, что в телеграм-боте
                  (по умолчанию kimi-k2-turbo-preview).
"""
import logging
import os

import aiohttp

log = logging.getLogger('ai')

KIMI_API_KEY = os.environ.get('KIMI_API_KEY')
KIMI_BASE_URL = os.environ.get('KIMI_BASE_URL', 'https://api.moonshot.ai/v1').rstrip('/')
KIMI_MODEL = os.environ.get('KIMI_MODEL', 'kimi-k2-turbo-preview')

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
    headers = {'Authorization': f'Bearer {KIMI_API_KEY}'}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f'{KIMI_BASE_URL}/chat/completions',
                              json=payload, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=90)) as resp:
                data = await resp.json()
                if resp.status != 200:
                    log.error('Kimi API %s: %s', resp.status, data)
                    return None
                return data['choices'][0]['message']['content'].strip()
    except Exception:
        log.exception('Ошибка запроса к Kimi')
        return None
