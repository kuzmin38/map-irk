"""Проектная документация: разовая загрузка файлов с Google Диска к боту.

Файлы на Диске могут быть закрыты обратно сразу после загрузки — бот отдаёт
их из своего хранилища, и видят их только сотрудники, добавленные в бота.
"""
import asyncio
import json
import logging
import os
import re

import aiohttp

from . import houses

log = logging.getLogger('project_docs')

# Каталог на томе (на Railway — /data/project), рядом с файлами документов домов
PROJECT_DIR = os.environ.get(
    'BOT_PROJECT_DIR',
    os.path.join(os.environ.get('BOT_DOCS_DIR', os.path.join(houses.DATA_DIR, 'docs')), 'project'))

with open(os.path.join(houses.DATA_DIR, 'docs_catalog.json'), encoding='utf-8') as f:
    CATALOG = json.load(f)


def file_id(url: str) -> str | None:
    m = re.search(r'/d/([\w-]+)', url) or re.search(r'[?&]id=([\w-]+)', url)
    return m.group(1) if m else None


def local_path(doc: dict) -> str | None:
    """Путь к скачанному файлу или None."""
    fid = file_id(doc['url'])
    if not fid:
        return None
    for ext in ('.pdf', '.doc', '.docx', '.dwg', '.xlsx', '.bin'):
        p = os.path.join(PROJECT_DIR, fid + ext)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
    return None


def downloaded_count() -> int:
    return sum(1 for d in CATALOG if local_path(d))


EXT_BY_TYPE = {
    'application/pdf': '.pdf',
    'application/msword': '.doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'image/vnd.dwg': '.dwg',
}


async def _fetch_one(session, doc) -> tuple[bool, str]:
    fid = file_id(doc['url'])
    if not fid:
        return False, f"{doc['title']}: не разобрал ссылку"
    if local_path(doc):
        return True, f"{doc['title']}: уже был"
    url = f'https://drive.google.com/uc?export=download&id={fid}'
    try:
        async with session.get(url, allow_redirects=True,
                               timeout=aiohttp.ClientTimeout(total=180)) as resp:
            if resp.status != 200:
                return False, f"{doc['title']}: HTTP {resp.status}"
            ctype = (resp.headers.get('Content-Type') or '').split(';')[0].strip()
            data = await resp.read()
            # Диск отдаёт HTML вместо файла, когда доступ закрыт или нужна проверка
            if ctype.startswith('text/html') or data[:15].lstrip().lower().startswith(b'<!doctype'):
                return False, f"{doc['title']}: Диск не отдал файл (проверьте доступ по ссылке)"
            ext = EXT_BY_TYPE.get(ctype, os.path.splitext(doc['title'])[1] or '.bin')
            os.makedirs(PROJECT_DIR, exist_ok=True)
            with open(os.path.join(PROJECT_DIR, fid + ext), 'wb') as f:
                f.write(data)
            return True, f"{doc['title']}: {len(data) / 1024:.0f} КБ"
    except Exception as e:
        log.exception('Ошибка загрузки %s', doc['title'])
        return False, f"{doc['title']}: {type(e).__name__}"


async def download_all(progress=None) -> tuple[int, int, list]:
    """Скачивает все документы каталога. Возвращает (успешно, всего, отчёт)."""
    os.makedirs(PROJECT_DIR, exist_ok=True)
    report, ok = [], 0
    async with aiohttp.ClientSession() as session:
        for i, doc in enumerate(CATALOG, 1):
            success, line = await _fetch_one(session, doc)
            ok += success
            report.append(('✅ ' if success else '⚠️ ') + line)
            if progress and i % 5 == 0:
                await progress(i, len(CATALOG))
            await asyncio.sleep(0.3)  # не частим запросами к Диску
    return ok, len(CATALOG), report
