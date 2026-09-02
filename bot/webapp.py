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

from . import checks, db

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


def house_stats() -> dict:
    """Числа по каждому дому для списка: заявки, счётчики, просроченные поверки.

    Список адресов сам по себе бесполезен — по нему не видно, где что нужно
    сделать. Значки превращают его в рабочий экран.
    """
    from datetime import date

    from .handlers import current_period

    out = {}
    try:
        period = current_period()
        sdano = {r['meter_id'] for r in db.readings_for_period(period)}
        vsego = db.houses_with_meters()
        otkrytye = {}
        for r in db.list_requests(limit=500):
            if r['status'] != 'done' and r['house_id'] is not None:
                otkrytye[r['house_id']] = otkrytye.get(r['house_id'], 0) + 1
        prosrocheno = {}
        today = date.today().isoformat()
        for d in db.devices_with_verification():
            if d['verified_until'] < today:
                prosrocheno[d['house_id']] = prosrocheno.get(d['house_id'], 0) + 1
        for house_id, n in vsego.items():
            out.setdefault(house_id, {})['meters'] = n
            out[house_id]['meters_done'] = sum(
                1 for m in db.list_meters(house_id) if m['id'] in sdano)
        for house_id, n in otkrytye.items():
            out.setdefault(house_id, {})['requests_open'] = n
        for house_id, n in prosrocheno.items():
            out.setdefault(house_id, {})['verify_overdue'] = n
        # Значок в списке: красный — критичное, жёлтый — недозаполненное
        from . import houses as houses_mod
        for h in houses_mod.HOUSES:
            level = checks.house_level(checks.house_findings(h['id'], period))
            if level:
                out.setdefault(h['id'], {})['level'] = level
    except Exception:
        log.exception('Не удалось собрать числа по домам — отдаю список без них')
    return out


def build_payload() -> dict:
    """Данные для приложения: справочники из файлов, привязка к ЖК — из базы.

    Список домов берём из `houses`, а не из файла напрямую: там уже применено
    ограничение из `bot/data/active_houses.txt` — какие дома сейчас в работе.
    """
    from . import houses as houses_mod

    houses = [dict(h) for h in houses_mod.HOUSES]
    complexes = {c['id']: c['name'] for c in _load('complexes.json', [])}

    try:
        house_complex = db.all_house_complexes()
    except Exception:
        log.exception('Не удалось прочитать привязку домов к ЖК — отдаю без неё')
        house_complex = {}

    stats = house_stats()
    for h in houses:
        h.pop('zveno', None)  # звенья больше не используются
        cx = house_complex.get(h['id']) or house_complex.get(str(h['id']))
        if cx:
            h['complex'] = cx
        h.update(stats.get(h['id'], {}))

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


def house_state(house_id: int) -> dict:
    """Живое состояние дома из базы: паспорт, заявки, работы, приборы, чат.

    Всё, что в боте разложено по кнопкам, здесь собирается в один ответ —
    приложение показывает это одной карточкой.
    """
    from .handlers import PASSPORT_FIELDS

    raw = db.get_passport(house_id)
    passport = [{'field': k, 'label': label, 'value': raw[k]}
                for k, label in PASSPORT_FIELDS if raw.get(k)]

    requests = [{'id': r['id'], 'description': r['description'], 'status': r['status'],
                 'created_at': r['created_at'], 'author': r['created_by_name']}
                for r in db.list_requests(limit=200) if r['house_id'] == house_id]

    works = [{'id': w['id'], 'title': w['title'], 'deadline': w['deadline'],
              'assignee': w['assignee'], 'status': w['status'], 'report': w['report']}
             for w in db.list_works(house_id=house_id, open_only=False, limit=40)]

    devices = []
    for p in db.list_points(house_id):
        d = db.active_device(p['id'])
        devices.append({
            'place': p['place'], 'tp': p['tp'],
            'serial': d['serial'] if d else None,
            'verified_until': d['verified_until'] if d else None,
            'installed_by': d['installed_by'] if d else None,
        })

    from .handlers import METER_LABELS, current_period

    period = current_period()
    sdano = {r['meter_id'] for r in db.readings_for_period(period)}
    meters = []
    for m in db.list_meters(house_id):
        last = db.meter_readings(m['id'], limit=1)
        meters.append({
            'label': m['label'],
            'kind': METER_LABELS.get(m['kind'], m['kind']),
            'serial': m['serial'],
            'value': last[0]['value'] if last else None,
            'period': last[0]['period'] if last else None,
            'by': last[0]['submitted_by_name'] if last else None,
            'done': m['id'] in sdano,
        })

    chat = [{'author': m['user_name'], 'at': m['created_at'],
             'text': m['text'] or m['transcript'] or '',
             'is_issue': bool(m['is_issue']), 'has_files': bool(m['has_files'])}
            for m in db.house_chat_records(house_id, limit=15)]

    docs = [{'filename': d['filename'], 'note': d['note']}
            for d in db.list_docs(house_id)]
    return {
        'passport': passport,
        'passport_total': len(PASSPORT_FIELDS),
        'requests': requests,
        'works': works,
        'devices': devices,
        'meters': meters,
        'chat': chat,
        'docs': docs,
        'findings': checks.house_findings(house_id, period),
        # Сводка показывается всегда, даже из нулей: пустая карточка
        # выглядела так, будто приложение ничего не умеет
        'summary': {
            'requests_open': sum(1 for r in requests if r['status'] != 'done'),
            'works_open': sum(1 for w in works if w['status'] != 'done'),
            'devices': len(devices),
            'meters': len(meters),
            'meters_done': sum(1 for m in meters if m['done']),
            'passport': len(passport),
            'passport_total': len(PASSPORT_FIELDS),
            'docs': len(docs),
            'period': period,
        },
    }


async def _house_api(request):
    try:
        house_id = int(request.match_info['house_id'])
    except (KeyError, ValueError):
        raise web.HTTPNotFound(text='404')
    try:
        state = house_state(house_id)
    except Exception:
        log.exception('Не удалось собрать данные дома %s', house_id)
        raise web.HTTPInternalServerError(text='Данные временно недоступны')
    return web.json_response(state, headers={'Cache-Control': 'no-store',
                                             'X-Robots-Tag': 'noindex, nofollow'},
                             dumps=lambda o: json.dumps(o, ensure_ascii=False))


async def _status(request):
    """Состояние бота простым текстом: открыть с телефона вместо логов Railway."""
    from . import ai, status, transcribe
    from .handlers import build_version

    ffmpeg_ok = transcribe.ffmpeg_available()
    rec = 'работает' if (ffmpeg_ok and ai.enabled()) else (
        'нет ffmpeg' if not ffmpeg_ok else 'нет ключа ИИ')
    text = status.report(build_version(), public_url(), rec)
    return web.Response(text=text, content_type='text/plain', charset='utf-8',
                        headers={'Cache-Control': 'no-store',
                                 'X-Robots-Tag': 'noindex, nofollow'})


async def _health(request):
    """Живость сервера и какая сборка приехала — чтобы не гадать после деплоя."""
    try:
        from .handlers import build_version
        return web.Response(text=f'ok {build_version()}')
    except Exception:
        return web.Response(text='ok')


# Бот нужен странице голоса: ответ дублируется в MAX
BOT = {'ptr': None}


async def _golos_page(request):
    """Страница записи. Адрес личный, он же пропуск."""
    from . import golos

    user_id = db.token_user(request.match_info.get('token'))
    if not user_id:
        return web.Response(status=404, text='404')
    polzovatel = db.get_user(user_id)
    imya = (polzovatel['name'] if polzovatel else None) or 'сотрудник'
    return web.Response(text=golos.stranitsa(imya), content_type='text/html')


async def _golos_upload(request):
    """Принимает запись, расшифровывает и обрабатывает как обычное сообщение."""
    from . import golos

    user_id = db.token_user(request.match_info.get('token'))
    if not user_id:
        return web.json_response({'error': 'Ссылка недействительна.'}, status=404)
    data = await request.read()
    if not data:
        return web.json_response({'error': 'Запись пустая.'})
    if len(data) > golos.MAX_BYTES:
        return web.json_response({'error': 'Запись слишком длинная.'})
    log.info('Голос со страницы: %s, %.1f КБ', user_id, len(data) / 1024)
    try:
        text = await golos.rasshifrovat(data, request.headers.get('Content-Type', ''))
    except Exception:
        log.exception('Расшифровка записи со страницы не удалась')
        return web.json_response({'error': 'Не удалось расшифровать. Попробуйте ещё раз.'})
    if not text:
        return web.json_response({'error': 'Не разобрала речь. Попробуйте ещё раз, '
                                           'ближе к телефону.'})
    otvet = await golos.obrabotat(user_id, text, BOT['ptr'])
    return web.json_response({'text': text, 'reply': otvet})


async def _not_found(request):
    # без подсказок о том, что здесь вообще что-то есть
    return web.Response(status=404, text='404')


def hook_url() -> str | None:
    """Полный адрес для подписки MAX на обновления."""
    host = (os.environ.get('MINIAPP_HOST')
            or os.environ.get('RAILWAY_PUBLIC_DOMAIN'))
    return f'https://{host}{hook_path()}' if host else None


def hook_path() -> str:
    """Адрес, по которому MAX стучится с обновлениями. Секретный, как и приложение."""
    return f'/{miniapp_path()}/hook'


def create_app(webhook=None) -> web.Application:
    """Сервер мини-приложения. С webhook — он же принимает обновления MAX.

    Вебхук вешаем на то же приложение, а не на отдельный порт: Railway даёт
    один порт, и выбирать между приложением и обновлениями не хочется.
    """
    path = miniapp_path()
    app = web.Application()
    if webhook is not None:
        app.on_startup.append(webhook.on_startup)
        webhook.setup(app, path=hook_path())
        log.info('Вебхук принимает обновления на %s', hook_path())
    app.router.add_get('/healthz', _health)
    # без завершающего слэша — переводим на слэш, иначе относительные запросы
    # приложения (api/house/...) ушли бы мимо секретного пути
    async def _to_slash(request):
        raise web.HTTPFound(f'/{path}/')

    app.router.add_get(f'/{path}', _to_slash)
    app.router.add_get(f'/{path}/', _page)
    app.router.add_get(f'/{path}/api/house/{{house_id}}', _house_api)
    app.router.add_get(f'/{path}/status', _status)
    app.router.add_get(f'/{path}/golos/{{token}}/', _golos_page)
    app.router.add_post(f'/{path}/golos/{{token}}/golos', _golos_upload)
    app.router.add_route('*', '/{tail:.*}', _not_found)
    return app


async def start(port: int, host: str = '0.0.0.0', bot=None,
                webhook=None) -> web.AppRunner:
    """Поднимает сервер рядом с ботом и возвращает runner (для остановки в тестах)."""
    BOT['ptr'] = bot
    runner = web.AppRunner(create_app(webhook), access_log=None)
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
