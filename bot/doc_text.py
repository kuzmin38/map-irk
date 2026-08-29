"""Чтение документов: PDF, Word, Excel → текст, по которому Люся может отвечать.

Файлы проектной документации и документы домов лежат на томе, но до сих пор
были для бота непрозрачными: он мог их только переслать. Здесь они
превращаются в текст — дальше он живёт в базе (`doc_texts`) и доступен
разговорному агенту.

Тяжёлые форматы разбирает markitdown (Microsoft, MIT). Он необязателен: без
него простые текстовые файлы всё равно читаются, а остальные честно
помечаются как непрочитанные — бот работает как раньше.
"""
import asyncio
import logging
import os

log = logging.getLogger('doc_text')

# Читаем сами, без сторонних библиотек
PLAIN_EXT = {'.txt', '.md', '.csv', '.json', '.xml'}
# Отдаём markitdown
RICH_EXT = {'.pdf', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt',
            '.html', '.htm', '.epub'}
# Чертежи: не берёт ни markitdown, ни зрение модели — хранятся как есть
DRAWING_EXT = {'.dwg', '.dxf', '.rvt', '.nwd'}

MAX_CHARS = int(os.environ.get('DOC_TEXT_MAX_CHARS', '200000'))

_converter = None
_checked = False


def available() -> bool:
    """Установлен ли markitdown. Без него читаются только простые форматы."""
    global _converter, _checked
    if not _checked:
        _checked = True
        try:
            from markitdown import MarkItDown
            _converter = MarkItDown()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            # Не Exception: markitdown тянет нативные библиотеки, и сломанная
            # сборка роняет импорт паникой Rust — её обычным except не поймать.
            log.info('markitdown недоступен — PDF и Word читаться не будут')
            _converter = None
    return _converter is not None


def kind(path: str) -> str:
    """Как относиться к файлу: 'plain', 'rich', 'drawing' или 'other'."""
    ext = os.path.splitext(path)[1].lower()
    if ext in PLAIN_EXT:
        return 'plain'
    if ext in RICH_EXT:
        return 'rich'
    if ext in DRAWING_EXT:
        return 'drawing'
    return 'other'


def is_readable(path: str) -> bool:
    """Можно ли из файла достать текст здесь и сейчас."""
    k = kind(path)
    return k == 'plain' or (k == 'rich' and available())


def extract(path: str) -> tuple[str | None, str]:
    """Текст документа и статус: ok | empty | skipped | failed.

    Работает синхронно и не быстро (PDF на сотню страниц — секунды),
    поэтому вызывать её лучше через `extract_async`.
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None, 'failed'
    k = kind(path)
    if k == 'drawing':
        return None, 'skipped'
    if k == 'plain':
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read(MAX_CHARS + 1)
        except Exception:
            log.exception('Не удалось прочитать %s', path)
            return None, 'failed'
    elif k == 'rich':
        if not available():
            return None, 'skipped'
        try:
            text = _converter.convert(path).text_content or ''
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            # Битый PDF может уронить нативный разбор — один документ не должен
            # утаскивать за собой весь проход по каталогу.
            log.exception('markitdown не осилил %s', path)
            return None, 'failed'
    else:
        return None, 'skipped'
    text = (text or '').strip()
    if not text:
        # Скан без текстового слоя выглядит именно так: файл есть, букв нет
        return None, 'empty'
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + '\n\n[…документ обрезан]'
    return text, 'ok'


async def extract_async(path: str) -> tuple[str | None, str]:
    """То же самое, но не блокирует бота на время разбора."""
    return await asyncio.to_thread(extract, path)


def excerpt(text: str, query: str, radius: int = 400) -> str:
    """Кусок документа вокруг найденного слова — чтобы не слать модели всё."""
    if not text:
        return ''
    pos = text.lower().find(query.lower().strip())
    if pos == -1:
        return text[:radius * 2].strip()
    start = max(0, pos - radius)
    end = min(len(text), pos + len(query) + radius)
    piece = text[start:end].strip()
    return ('…' if start else '') + piece + ('…' if end < len(text) else '')
