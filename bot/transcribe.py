"""Расшифровка голосовых, видеоотчётов и записей планёрок.

Из видео вытаскиваем звуковую дорожку (ffmpeg), сжимаем в компактный mp3
и отдаём распознавание модели OpenRouter — тем же ключом, что и остальной ИИ.
Смотреть саму картинку видео мы не пытаемся: в отчётах сантехников всё
существенное проговаривается голосом.

Короткий отчёт уходит в модель одним куском. Запись совещания на полтора часа
так не отправить — её режем на отрезки по 10 минут, распознаём по очереди
и склеиваем текст обратно.
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

# Планёрки: запись длинная, поэтому лимиты свои и текст собирается из кусков
MEETING_MAX_SOURCE_MB = int(os.environ.get('MEETING_MAX_SOURCE_MB', '600'))
MEETING_MAX_SECONDS = int(os.environ.get('MEETING_MAX_SECONDS', '10800'))  # 3 часа
CHUNK_SECONDS = int(os.environ.get('MEETING_CHUNK_SECONDS', '600'))        # 10 минут

PROMPT = ('Это рабочий отчёт сантехника управляющей компании. Расшифруй речь '
          'дословно на русском языке. Пиши только текст сказанного, без '
          'комментариев и пояснений. Если речи нет — ответь пустой строкой.')

MEETING_PROMPT = (
    'Это запись рабочего совещания (планёрки) управляющей компании: говорят '
    'несколько человек. Расшифруй речь дословно на русском языке. Реплики '
    'разных людей начинай с новой строки. Если по голосу или по обращению '
    'понятно, кто говорит, ставь в начале строки имя и двоеточие, иначе — '
    '«Говорящий 1:», «Говорящий 2:» и так далее, сохраняя нумерацию для одного '
    'и того же голоса. Ничего не придумывай и не пересказывай: только то, что '
    'сказано. Если речи нет — ответь пустой строкой.')


def ffmpeg_available() -> bool:
    return shutil.which('ffmpeg') is not None


async def _run(*args) -> tuple[int, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode, out


async def extract_audio(src_path: str, max_seconds: int = MAX_SECONDS) -> str | None:
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
        '-t', str(max_seconds),
        out_path)
    if code != 0 or not os.path.exists(out_path):
        log.warning('ffmpeg не смог извлечь звук: %s', out[-300:].decode('utf-8', 'ignore'))
        return None
    if os.path.getsize(out_path) == 0:
        os.unlink(out_path)
        return None
    return out_path


async def audio_duration(path: str) -> float | None:
    """Длительность записи в секундах (ffprobe). None — если не удалось узнать."""
    if not shutil.which('ffprobe'):
        return None
    code, out = await _run('ffprobe', '-v', 'error', '-show_entries',
                           'format=duration', '-of',
                           'default=noprint_wrappers=1:nokey=1', path)
    if code != 0:
        return None
    try:
        return float(out.decode('utf-8', 'ignore').strip())
    except ValueError:
        return None


async def split_audio(path: str, chunk_seconds: int = CHUNK_SECONDS) -> list[str]:
    """Режет длинное аудио на отрезки. Короткое отдаёт как есть, одним куском."""
    duration = await audio_duration(path)
    if duration is not None and duration <= chunk_seconds:
        return [path]
    pattern = path + '.part%03d.mp3'
    code, out = await _run(
        'ffmpeg', '-y', '-i', path,
        '-f', 'segment', '-segment_time', str(chunk_seconds),
        '-c', 'copy', '-reset_timestamps', '1',
        pattern)
    parts = sorted(
        os.path.join(os.path.dirname(path), f)
        for f in os.listdir(os.path.dirname(path) or '.')
        if f.startswith(os.path.basename(path) + '.part') and f.endswith('.mp3'))
    if code != 0 or not parts:
        log.warning('Не удалось нарезать запись: %s', out[-300:].decode('utf-8', 'ignore'))
        return [path]
    return parts


async def transcribe_file(path: str, prompt: str = PROMPT) -> str | None:
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
                {'type': 'text', 'text': prompt},
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


async def download(url: str, dest: str, max_mb: int) -> bool:
    """Качает вложение в файл. False — если не отдали или файл слишком большой."""
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=1800)) as resp:
            if resp.status != 200:
                log.warning('Не удалось скачать вложение: HTTP %s', resp.status)
                return False
            size = 0
            with open(dest, 'wb') as f:
                async for chunk in resp.content.iter_chunked(1 << 16):
                    size += len(chunk)
                    if size > max_mb * 1024 * 1024:
                        log.warning('Файл больше %s МБ — пропускаю', max_mb)
                        return False
                    f.write(chunk)
    return True


async def transcribe_url(url: str) -> str | None:
    """Скачивает вложение (голосовое или видео) и возвращает расшифровку речи."""
    tmpdir = tempfile.mkdtemp(prefix='lusya_')
    src = os.path.join(tmpdir, 'source')
    audio = None
    try:
        if not await download(url, src, MAX_SOURCE_MB):
            return None
        audio = await extract_audio(src)
        if not audio:
            return None
        return await transcribe_file(audio)
    except Exception:
        log.exception('Ошибка при расшифровке вложения')
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def transcribe_meeting(url: str, progress=None) -> dict | None:
    """Расшифровывает запись совещания целиком, кусками по CHUNK_SECONDS.

    Возвращает {'text', 'duration', 'chunks'} или None, если не вышло.
    `progress` — необязательная корутина progress(готово, всего): пока идёт
    распознавание часовой записи, человеку стоит показывать, что дело движется.
    """
    tmpdir = tempfile.mkdtemp(prefix='lusya_meet_')
    src = os.path.join(tmpdir, 'source')
    try:
        if not await download(url, src, MEETING_MAX_SOURCE_MB):
            return None
        audio = await extract_audio(src, max_seconds=MEETING_MAX_SECONDS)
        if not audio:
            return None
        duration = await audio_duration(audio)
        parts = await split_audio(audio)
        texts = []
        for i, part in enumerate(parts, 1):
            if progress:
                await progress(i, len(parts))
            text = await transcribe_file(part, prompt=MEETING_PROMPT)
            if text:
                texts.append(text.strip())
            else:
                log.warning('Кусок %s из %s не распознан', i, len(parts))
        if not texts:
            return None
        return {'text': '\n'.join(texts), 'duration': duration, 'chunks': len(parts)}
    except Exception:
        log.exception('Ошибка при расшифровке планёрки')
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
