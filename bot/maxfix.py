"""Заплатка на разбор событий MAX.

Пересланное голосовое до Люси не доходило вовсе. Причина оказалась не в
боте: библиотека maxapi не смогла разобрать событие и выбросила его
целиком, написав в лог «неизвестный тип обновления: message_created».
Обновиться некуда — 1.2.2 самая свежая.

Поэтому здесь три вещи. Первое: когда разбор падает, в лог уходит само
событие — иначе причину не найти, мы это уже проходили. Второе: событие
чинится и всё-таки доезжает до бота, пусть и без той части, которую
библиотека не поняла. Третье, ради чего всё: сырое событие сохраняется,
и вложения читаются прямо из него — в обход библиотеки.
"""
import copy
import json
import logging
from collections import OrderedDict

from maxapi.methods.types import getted_updates as gu
from maxapi.types.updates import UpdateUnionAdapter
from maxapi.utils.updates import enrich_event

log = logging.getLogger('maxfix')

# Сырые тела сообщений: mid → dict. Держим последние, больше не нужно
RAW: OrderedDict = OrderedDict()
RAW_LIMIT = 200

SPEECH_TYPES = ('audio', 'video')


def _zapomnit(event: dict):
    """Кладёт тело сообщения в запас — из него потом читаем вложения."""
    body = ((event.get('message') or {}).get('body') or {})
    mid = body.get('mid')
    if not mid:
        return
    RAW[mid] = event.get('message') or {}
    RAW.move_to_end(mid)
    while len(RAW) > RAW_LIMIT:
        RAW.popitem(last=False)


def raw_message(mid: str) -> dict:
    return RAW.get(mid) or {}


def _vlozheniya(message: dict) -> list:
    """Вложения самого сообщения и пересланного вместе."""
    out = list(((message.get('body') or {}).get('attachments') or []))
    link = message.get('link') or {}
    out += list(((link.get('message') or {}).get('attachments') or []))
    return out


def speech_from_raw(mid: str):
    """(готовая расшифровка, ссылка) из сырого события. Обе части могут быть None."""
    gotovo = url = None
    for a in _vlozheniya(raw_message(mid)):
        if a.get('type') not in SPEECH_TYPES:
            continue
        gotovo = gotovo or (a.get('transcription') or '').strip() or None
        payload = a.get('payload') or {}
        url = url or payload.get('url')
        if not url:
            urls = a.get('urls') or {}
            for pole in ('mp4_480', 'mp4_360', 'mp4_720', 'mp4_240', 'mp4_144',
                         'mp4_1080', 'hls'):
                if urls.get(pole):
                    url = urls[pole]
                    break
    return gotovo, url


# ---------- Починка события ----------

def _bez_link(event: dict) -> dict:
    (event.get('message') or {}).pop('link', None)
    return event


def _bez_vlozheniy(event: dict) -> dict:
    body = (event.get('message') or {}).get('body') or {}
    body['attachments'] = []
    link_msg = ((event.get('message') or {}).get('link') or {}).get('message') or {}
    if link_msg:
        link_msg['attachments'] = []
    return event


def _golyy(event: dict) -> dict:
    return _bez_vlozheniy(_bez_link(event))


def _pochinit(event: dict):
    """Пробует разобрать событие, снимая по одному спорному куску."""
    for pravka in (_bez_link, _bez_vlozheniy, _golyy):
        try:
            return UpdateUnionAdapter.validate_python(pravka(copy.deepcopy(event)))
        except Exception:
            continue
    return None


async def get_update_model(event: dict, bot):
    """Замена библиотечной: не молчит и не теряет сообщение целиком."""
    try:
        obj = UpdateUnionAdapter.validate_python(event)
    except Exception as e:
        try:
            syroe = json.dumps(event, ensure_ascii=False)[:1500]
        except Exception:
            syroe = repr(event)[:1500]
        log.warning('Библиотека не разобрала событие: %s', str(e)[:400])
        log.info('Сырое событие: %s', syroe)
        if pustoe(event):
            # Тела в уведомлении нет вовсе — чинить нечего, идём за ним сами
            import asyncio
            asyncio.create_task(podobrat(bot, event.get('timestamp') or 0))
            return None
        obj = _pochinit(event)
        if obj is None:
            log.warning('Починить не удалось, событие потеряно')
            return None
        log.info('Событие починено и передано боту')
    _zapomnit(event)
    return await enrich_event(event_object=obj, bot=bot)


# ---------- Событие без тела ----------

# MAX присылает про голосовое в личке пустое уведомление: только метка
# времени, без самого сообщения. Текстовые приходят целиком, значит дело
# в аудио. Раз в событии его нет — забираем сообщение отдельным запросом
ON_RECOVERED = None      # сюда handlers кладёт свой обработчик
VZYATO: OrderedDict = OrderedDict()   # что уже подобрали, чтобы не по кругу
OKNO_MS = 120_000        # ищем сообщения за две минуты до уведомления

_CHATY: dict = {'kogda': 0.0, 'spisok': []}
CHAT_TTL = 600           # список чатов меняется редко


def pustoe(event: dict) -> bool:
    return (event.get('update_type') == 'message_created'
            and not event.get('message'))


async def _dialogi(bot) -> list:
    """Где искать потерянное сообщение: личные диалоги и чаты бота.

    Личных диалогов в get_chats нет вовсе — MAX их туда не кладёт. Их
    chat_id мы знаем только из входящих сообщений, поэтому запоминаем.
    """
    import time

    from . import db

    svoi = db.dialog_chats()
    if _CHATY['spisok'] and time.monotonic() - _CHATY['kogda'] < CHAT_TTL:
        return _bez_povtorov(svoi + _CHATY['spisok'])
    chats = await bot.get_chats(count=100)
    spisok = [c.chat_id for c in (getattr(chats, 'chats', None) or [])
              if getattr(c, 'chat_id', None)]
    _CHATY.update(kogda=time.monotonic(), spisok=spisok)
    log.info('Чатов у бота: %d, личных диалогов известно: %d', len(spisok), len(svoi))
    return _bez_povtorov(svoi + spisok)


def _bez_povtorov(spisok: list) -> list:
    vidno, out = set(), []
    for x in spisok:
        if x and x not in vidno:
            vidno.add(x)
            out.append(x)
    return out


def _v_ms(ts) -> int:
    """Метка времени в миллисекундах.

    MAX присылает миллисекунды в уведомлении и, как выяснилось, не всегда
    их же в ответе на запрос сообщений. Секунды меньше триллиона —
    по этому и различаем.
    """
    try:
        ts = int(ts or 0)
    except (TypeError, ValueError):
        return 0
    return ts * 1000 if 0 < ts < 1_000_000_000_000 else ts


def _pomnim(mid: str) -> bool:
    if not mid or mid in VZYATO or mid in RAW:
        return True
    VZYATO[mid] = 1
    while len(VZYATO) > RAW_LIMIT:
        VZYATO.popitem(last=False)
    return False


async def podobrat(bot, ts_ms: int):
    """Забирает сообщение, которого не было в уведомлении.

    Ходит по чатам бота и берёт самые свежие сообщения. Дорого делать это
    на каждое событие, но пустые уведомления приходят редко — только на
    голосовые.
    """
    from types import SimpleNamespace

    try:
        chat_ids = await _dialogi(bot)
    except Exception:
        log.exception('Не удалось получить список чатов')
        return
    porog = _v_ms(ts_ms) - OKNO_MS
    for chat_id in chat_ids:
        try:
            otvet = await bot.get_messages(chat_id=chat_id, count=5)
        except Exception:
            log.warning('Не удалось прочитать чат %s', chat_id, exc_info=True)
            continue
        soobscheniya = list(getattr(otvet, 'messages', None) or [])
        log.info('Чат %s: сообщений получено %d, метки %s (порог %s)',
                 chat_id, len(soobscheniya),
                 [_v_ms(getattr(m, 'timestamp', 0)) for m in soobscheniya], porog)
        for m in soobscheniya:
            body = getattr(m, 'body', None)
            mid = getattr(body, 'mid', None)
            if _v_ms(getattr(m, 'timestamp', 0)) < porog:
                continue
            if _pomnim(mid):
                continue
            log.info('Подобрала сообщение %s из чата %s: текст %r, вложений %d',
                     mid, chat_id, (getattr(body, 'text', None) or '')[:60],
                     len(getattr(body, 'attachments', None) or []))
            if ON_RECOVERED is None:
                continue
            try:
                await ON_RECOVERED(SimpleNamespace(message=m, bot=bot))
            except Exception:
                log.exception('Обработчик подобранного сообщения упал')
            return


def install():
    """Ставит заплатку. Вызывать один раз при запуске."""
    gu.get_update_model = get_update_model
    log.info('Заплатка на разбор событий MAX установлена')
