"""Расшифровка голосовых и видеоотчётов.

Из видео вытаскиваем звуковую дорожку (ffmpeg), сжимаем в компактный mp3
и отдаём распознавание модели OpenRouter — тем же ключом, что и остальной ИИ.
Смотреть саму картинку видео мы не пытаемся: в отчётах сантехников всё
существенное проговаривается голосом.
"""
import asyncio
import base64
import logging
import os
import shutil
import tempfile

import aiohttp

from . import ai

log = logging.getLogger('transcribe')

# Модель с поддержкой аудио на входе
AUDIO_MODEL = os.environ.get('OPENROUTER_AUDIO_MODEL', 'google/gemini-2.5-flash')

MAX_SOURCE_MB = 100      # больше качать смысла нет — это уже не отчёт, а кино
MAX_AUDIO_MB = 20        # предел на то, что отправляем в модель
MAX_SECONDS = 900        # 15 минут речи с запасом

PROMPT = ('Это рабочий отчёт сантехника управляющей компании. Расшифруй речь '
          'дословно на русском языке. Пиши только текст сказанного, без '
          'комментариев и пояснений. Если речи нет — ответь пустой строкой.')


def ffmpeg_available() -> bool:
    return shutil.which('ffmpeg') is not None


async def _run(*args) -> tuple[int, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode, out


async def extract_audio(src_path: str) -> str | None:
    """Достаёт из файла звук в mp3 (моно, 16 кГц) — компактно и достаточно для речи."""
    if not ffmpeg_available():
        log.warning('ffmpeg не установлен — расшифровка недоступна')
        return None
    out_path = src_path + '.mp3'
    code, out = await _run(
        'ffmpeg', '-y', '-i', src_path,
        '-vn',                        # видеодорожку выбрасываем
        '-ac', '1', '-ar', '16000',   # моно, 16 кГц — стандарт для распознавания
        '-b:a', '32k',
        '-t', str(MAX_SECONDS),
        out_path)
    if code != 0 or not os.path.exists(out_path):
        log.warning('ffmpeg не смог извлечь звук: %s', out[-300:].decode('utf-8', 'ignore'))
        return None
    if os.path.getsize(out_path) == 0:
        os.unlink(out_path)
        return None
    return out_path


async def transcribe_file(path: str) -> str | None:
    """Расшифровывает аудиофайл через OpenRouter. None — если не вышло."""
    if not ai.enabled():
        return None
    size_mb = os.path.getsize(path) / 1024 / 1024
    if size_mb > MAX_AUDIO_MB:
        log.warning('Аудио слишком большое (%.1f МБ) — пропускаю', size_mb)
        return None
    with open(path, 'rb') as f:
        data = base64.b64encode(f.read()).decode()
    payload = {
        'model': AUDIO_MODEL,
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': PROMPT},
                {'type': 'input_audio', 'input_audio': {'data': data, 'format': 'mp3'}},
            ],
        }],
        'max_tokens': 2000,
        'temperature': 0.1,
    }
    headers = {
        'Authorization': f'Bearer {ai.KIMI_API_KEY}',
        'HTTP-Referer': 'https://github.com/kuzmin38/map-irk',
        'X-Title': 'Lusya Bot',
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f'{ai.OPENROUTER_BASE_URL}/chat/completions',
                              json=payload, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=300)) as resp:
                body = await resp.json()
                if resp.status != 200:
                    log.error('Расшифровка не удалась, OpenRouter %s: %s', resp.status, body)
                    return None
                text = (body['choices'][0]['message'].get('content') or '').strip()
                return text or None
    except Exception:
        log.exception('Ошибка расшифровки')
        return None


async def transcribe_url(url: str) -> str | None:
    """Скачивает вложение (голосовое или видео) и возвращает расшифровку речи."""
    tmpdir = tempfile.mkdtemp(prefix='lusya_')
    src = os.path.join(tmpdir, 'source')
    audio = None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                if resp.status != 200:
                    log.warning('Не удалось скачать вложение: HTTP %s', resp.status)
                    return None
                size = 0
                with open(src, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(1 << 16):
                        size += len(chunk)
                        if size > MAX_SOURCE_MB * 1024 * 1024:
                            log.warning('Файл больше %s МБ — пропускаю', MAX_SOURCE_MB)
                            return None
                        f.write(chunk)
        audio = await extract_audio(src)
        if not audio:
            return None
        return await transcribe_file(audio)
    except Exception:
        log.exception('Ошибка при расшифровке вложения')
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
