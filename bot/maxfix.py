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
        obj = _pochinit(event)
        if obj is None:
            log.warning('Починить не удалось, событие потеряно')
            return None
        log.info('Событие починено и передано боту')
    _zapomnit(event)
    return await enrich_event(event_object=obj, bot=bot)


def install():
    """Ставит заплатку. Вызывать один раз при запуске."""
    gu.get_update_model = get_update_model
    log.info('Заплатка на разбор событий MAX установлена')
