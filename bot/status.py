"""Что происходит с ботом прямо сейчас — для страницы состояния.

Логи Railway с телефона читать неудобно, а понять «жив ли опрос MAX и доходят
ли до бота сообщения» нужно быстро. Здесь копятся несколько чисел, которые
`bot/webapp.py` отдаёт по секретному адресу.
"""
import time
from datetime import datetime

STARTED = time.time()

STATE = {
    'bot_username': None,   # username из get_me — если None, MAX не принял токен
    'bot_id': None,
    'me_error': None,       # текст ошибки get_me
    'polls': 0,             # сколько раз запускался цикл опроса
    'last_error': None,     # последняя ошибка опроса
    'last_error_at': None,
    'updates': 0,           # сколько сообщений и нажатий дошло до бота
    'last_update_at': None,
    'last_update_kind': None,
    'fetches': 0,           # сколько раз MAX ответил на запрос обновлений
    'last_fetch_at': None,
    'events': 0,            # сколько событий MAX прислал в этих ответах
    'fetch_error': None,    # последняя ошибка самого запроса обновлений
    'fetch_error_at': None,
    'instant': 0,           # пустых ответов сразу, без ожидания
}


def _stamp() -> str:
    return datetime.now().strftime('%d.%m.%Y %H:%M:%S')


def note_me(username, user_id, error=None):
    STATE.update(bot_username=username, bot_id=user_id,
                 me_error=str(error) if error else None)


def note_poll_start():
    STATE['polls'] += 1


def note_poll_error(exc):
    STATE.update(last_error=f'{type(exc).__name__}: {exc}'[:300], last_error_at=_stamp())


def note_fetch(events: int, instant: bool = False) -> bool:
    """Ответ MAX на запрос обновлений. Возвращает True, если он первый.

    Библиотека молча глотает таймауты: «MAX отвечает, но событий нет» и
    «запрос завис навсегда» выглядят одинаково — полной тишиной в логах.
    Считаем ответы, и одно от другого наконец отличается.

    instant — MAX ответил мгновенно вместо того, чтобы держать соединение.
    Много таких подряд означает, что цикл разгоняется и упрётся в лимит.
    """
    STATE['fetches'] += 1
    STATE['events'] += events
    STATE['last_fetch_at'] = _stamp()
    if instant and not events:
        STATE['instant'] += 1
    return STATE['fetches'] == 1


def note_fetch_error(exc):
    STATE.update(fetch_error=f'{type(exc).__name__}: {exc}'[:300],
                 fetch_error_at=_stamp())


def pulse() -> str:
    """Короткая сводка для лога: жив ли опрос и что он приносит."""
    s = STATE
    return (f"работаю {uptime()}, ответов MAX {s['fetches']}"
            + (f" (последний {s['last_fetch_at']})" if s['last_fetch_at'] else '')
            + f", событий {s['events']}, дошло до бота {s['updates']}"
            + (f", пустых сразу {s['instant']}" if s['instant'] else '')
            + (f", ошибка запроса: {s['fetch_error']}" if s['fetch_error'] else ''))


def note_update(kind: str):
    STATE['updates'] += 1
    STATE.update(last_update_at=_stamp(), last_update_kind=kind)


def uptime() -> str:
    sec = int(time.time() - STARTED)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f'{h} ч {m} мин' if h else (f'{m} мин {s} с' if m else f'{s} с')


def report(build: str, app_url: str | None, recognition: str) -> str:
    """Человекочитаемая сводка состояния."""
    s = STATE
    if s['bot_username']:
        token = f"принят, бот @{s['bot_username']} (id {s['bot_id']})"
    elif s['me_error']:
        token = f"НЕ ПРИНЯТ — {s['me_error']}"
    else:
        token = 'проверка не выполнялась'

    if s['updates']:
        updates = (f"{s['updates']}, последнее {s['last_update_at']} "
                   f"({s['last_update_kind']})")
    else:
        updates = 'НИ ОДНОГО с момента запуска'

    if s['fetches']:
        answers = f"{s['fetches']}, последний {s['last_fetch_at']}"
    else:
        answers = 'НИ ОДНОГО — запрос к MAX не возвращается'

    lines = [
        f'Сборка:            {build}',
        f'Работает:          {uptime()}',
        f'Токен MAX:         {token}',
        f'Циклов опроса:     {s["polls"]}',
        f'Ответов MAX:       {answers}',
        f'Событий от MAX:    {s["events"]}',
        f'Ошибка запроса:    {s["fetch_error"] or "нет"}'
        + (f' ({s["fetch_error_at"]})' if s['fetch_error_at'] else ''),
        f'Ошибка опроса:     {s["last_error"] or "нет"}'
        + (f' ({s["last_error_at"]})' if s['last_error_at'] else ''),
        f'Пришло сообщений:  {updates}',
        f'Расшифровка видео: {recognition}',
        f'Приложение:        {app_url or "домен не выдан"}',
        '',
    ]
    if not s['bot_username']:
        lines.append('⚠️ MAX не отдал данные бота — проверьте MAX_BOT_TOKEN.')
    elif not s['fetches']:
        lines.append('⚠️ Опрос запущен, но MAX ни разу не ответил на запрос обновлений.')
        lines.append('   Связь с MAX не установилась — смотрите «Ошибка запроса».')
    elif not s['events']:
        lines.append('⚠️ MAX отвечает, но событий не присылает совсем. Обычно это значит,')
        lines.append('   что тот же токен слушает второй запущенный экземпляр бота,')
        lines.append('   либо пишут не этому боту — сверьте @username в MAX.')
    elif not s['updates']:
        lines.append('⚠️ События от MAX идут, но до обработчиков не доходят —')
        lines.append('   это уже ошибка в самом боте, смотрите логи.')
    return '\n'.join(lines)
