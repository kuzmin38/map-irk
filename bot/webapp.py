"""HTTP-сервер мини-приложения MAX.

Раздаёт `miniapp/template.html` с вшитыми данными прямо из процесса бота,
поэтому GitHub Pages больше не нужен, а репозиторий может быть приватным.

Отличие от `scripts/build_miniapp.py`: там страница собирается один раз при
сборке, здесь — на лету, поэтому привязка домов к ЖК берётся из рабочей базы
и меняется сразу после правки в боте, без пересборки и коммита.

Переменные окружения:
  PORT         — порт (Railway задаёт сам)
  MINIAPP_PATH — путь, по которому отдаётся приложение. Значение по умолчанию
                 «miniapp» угадывается, поэтому для боевого сервера впишите
                 длинную случайную строку: адрес и есть пропуск.
  MINIAPP_TTL  — сколько секунд держать собранную страницу в памяти (60).
"""
import json
import logging
import os
import time

from aiohttp import web

from . import db

log = logging.getLogger('bot.webapp')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, 'bot', 'data')
TEMPLATE = os.path.join(ROOT, 'miniapp', 'template.html')
MARKER = '/*__DATA__*/{}'

_cache = {'html': None, 'at': 0.0}


def _load(name, default=None):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def build_payload() -> dict:
    """Данные для приложения: справочники из файлов, привязка к ЖК — из базы."""
    houses = _load('houses.json', [])
    complexes = {c['id']: c['name'] for c in _load('complexes.json', [])}

    try:
        house_complex = db.all_house_complexes()
    except Exception:
        log.exception('Не удалось прочитать привязку домов к ЖК — отдаю без неё')
        house_complex = {}

    for h in houses:
        h.pop('zveno', None)  # звенья больше не используются
        cx = house_complex.get(h['id']) or house_complex.get(str(h['id']))
        if cx:
            h['complex'] = cx

    return {
        'houses': houses,
        'complexes': complexes,
        'risers': _load('risers.json', {}),
        'docs': _load('docs_catalog.json', []),
        'directory': (_load('directory.json') or {}).get('sections', []),
    }


def render() -> str:
    """Собирает страницу приложения. Держит результат в памяти MINIAPP_TTL секунд."""
    ttl = float(os.environ.get('MINIAPP_TTL', '60'))
    now = time.monotonic()
    if _cache['html'] is not None and now - _cache['at'] < ttl:
        return _cache['html']

    with open(TEMPLATE, encoding='utf-8') as f:
        html = f.read()
    if MARKER not in html:
        raise RuntimeError('В шаблоне мини-приложения не найден маркер для данных')

    data = json.dumps(build_payload(), ensure_ascii=False, separators=(',', ':'))
    html = html.replace(MARKER, data)
    _cache.update(html=html, at=now)
    return html


def miniapp_path() -> str:
    return os.environ.get('MINIAPP_PATH', 'miniapp').strip('/')


def public_url() -> str | None:
    """Полный адрес приложения, если Railway отдал домен сервиса."""
    host = (os.environ.get('MINIAPP_HOST')
            or os.environ.get('RAILWAY_PUBLIC_DOMAIN'))
    return f'https://{host}/{miniapp_path()}/' if host else None


async def _page(request):
    try:
        html = render()
    except Exception:
        log.exception('Не удалось собрать мини-приложение')
        raise web.HTTPInternalServerError(text='Приложение временно недоступно')
    return web.Response(
        text=html,
        content_type='text/html',
        charset='utf-8',
        headers={
            # данные меняются в базе — отдавать из кэша браузера нельзя
            'Cache-Control': 'no-store',
            # адрес секретный: поисковикам он не нужен
            'X-Robots-Tag': 'noindex, nofollow',
            'Referrer-Policy': 'no-referrer',
        },
    )


async def _health(request):
    """Живость сервера и какая сборка приехала — чтобы не гадать после деплоя."""
    try:
        from .handlers import build_version
        return web.Response(text=f'ok {build_version()}')
    except Exception:
        return web.Response(text='ok')


async def _not_found(request):
    # без подсказок о том, что здесь вообще что-то есть
    return web.Response(status=404, text='404')


def create_app() -> web.Application:
    path = miniapp_path()
    app = web.Application()
    app.router.add_get('/healthz', _health)
    app.router.add_get(f'/{path}', _page)
    app.router.add_get(f'/{path}/', _page)
    app.router.add_route('*', '/{tail:.*}', _not_found)
    return app


async def start(port: int, host: str = '0.0.0.0') -> web.AppRunner:
    """Поднимает сервер рядом с ботом и возвращает runner (для остановки в тестах)."""
    runner = web.AppRunner(create_app(), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()

    path = miniapp_path()
    log.info('Мини-приложение отдаётся на %s:%s/%s/', host, port, path)
    if path == 'miniapp':
        log.warning('MINIAPP_PATH не задан — адрес приложения угадывается. '
                    'Впишите длинную случайную строку, адрес и есть пропуск.')
    url = public_url()
    if url:
        log.info('Публичный адрес приложения: %s', url)
    return runner
