"""Ночной разбор ленты: из разговора — факты по домам.

Днём Люся работает рефлексами: правила, регулярки, мгновенный ответ. Это
правильно — в чате никто не ждёт, и ошибаться там дорого. Но рефлексы
видят одно сообщение и не видят дня целиком.

Поэтому второй темп. Раз в сутки, когда спешить некуда, ленту перечитывает
сильная модель — целиком, со всеми репликами и расшифровками — и
раскладывает по домам: что сделали, что нашли, что повисло. В карточку
дома ложатся факты, а не список сообщений.

Это дёшево именно потому, что медленно: не тысяча вызовов по одному
сообщению, а один вызов на весь день.
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta

from . import ai, db, houses

log = logging.getLogger('razbor')

HOUR = 22          # вечером по Иркутску: смена кончилась, лента полная
MAX_ZAPISEY = 250  # больше в один запрос не отдаём

ZADANIE = (
    'Ниже — переписка рабочего чата сантехников управляющей компании за один '
    'день. Реплики, расшифровки голосовых и видеоотчётов. В квадратных скобках '
    'указан дом, если он определён.\n\n{lenta}\n\n'
    'Дома в обслуживании:\n{doma}\n\n'
    'Разложи день по домам. Верни строго JSON без пояснений:\n'
    '{{"дома": [{{"адрес": "...", "факты": [{{"что": "...", "вид": "работа|находка|'
    'заявка|отключение"}}]}}], "повисло": ["..."]}}\n'
    'Правила:\n'
    '— адрес бери из списка домов дословно; если дом непонятен, пропусти факт;\n'
    '— факт — одна короткая деловая строка: что сделали или что нашли. '
    'Без имён, без времени, без оценок;\n'
    '— ничего не придумывай: если в переписке этого нет, факта нет. Лучше '
    'вернуть пустой список, чем догадку;\n'
    '— болтовню, приветствия, благодарности и шутки пропускай;\n'
    '— в «повисло» вынеси то, о чём попросили и что не закрыли в этот день. '
    'Если такого не видно, верни пустой список;\n'
    '— брань замени нейтральными словами.'
)


def lenta_za_den(day: str) -> str:
    """Переписка за день одной простынёй. day — «ДД.ММ.ГГГГ»."""
    zapisi = db.chat_records_between(day, day, limit=MAX_ZAPISEY)
    stroki = []
    for r in zapisi:
        chto = (r['transcript'] or r['text'] or '').strip()
        if not chto:
            continue
        dom = houses.HOUSES_BY_ID.get(r['house_id']) if r['house_id'] else None
        adres = f" [{dom['address']}]" if dom else ''
        stroki.append(f"{r['created_at'][-5:]} {r['user_name'] or '—'}{adres}: {chto}")
    return '\n'.join(stroki)


async def razobrat_den(day: str) -> dict:
    """Факты по домам и то, что повисло. Пустой разбор — если разбирать нечего."""
    lenta = lenta_za_den(day)
    if len(lenta) < 100:
        log.info('За %s в ленте почти пусто — разбирать нечего', day)
        return {'дома': [], 'повисло': []}

    spisok = '\n'.join(h['address'] for h in houses.HOUSES)
    otvet = await ai.ask(ZADANIE.format(lenta=lenta[:60000], doma=spisok),
                         max_tokens=2000, temperature=0,
                         model=ai.SLOW_MODEL, timeout=ai.SLOW_TIMEOUT)
    if not otvet:
        log.warning('Модель не разобрала день %s', day)
        return {}
    m = re.search(r'\{.*\}', otvet, re.S)
    if not m:
        log.warning('Разбор вернулся не JSON: %.150s', otvet)
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        log.warning('JSON разбора не читается: %.150s', m.group(0))
        return {}


def sohranit(day: str, razbor: dict) -> int:
    """Кладёт факты в хронику домов. Возвращает, сколько записано."""
    iso = _iso(day)
    zapisano = 0
    for dom_data in razbor.get('дома') or []:
        adres = (dom_data.get('адрес') or '').strip()
        dom = houses.detect_house(adres) if adres else None
        if not dom:
            log.info('Разбор: дом «%s» не опознан, факты пропущены', adres)
            continue
        for fakt in dom_data.get('факты') or []:
            chto = (fakt.get('что') or '').strip()
            if not chto:
                continue
            db.add_house_fact(dom['id'], iso, chto, (fakt.get('вид') or '').strip() or None)
            zapisano += 1
    return zapisano


def _iso(day: str) -> str:
    """«01.09.2026» → «2026-09-01»: так хроника сортируется сама."""
    try:
        return datetime.strptime(day, '%d.%m.%Y').date().isoformat()
    except ValueError:
        return day


def svodka(day: str, razbor: dict) -> str:
    """Короткий итог дня для руководителя."""
    stroki = [f'📆 Итоги дня {day}', '']
    doma = [d for d in (razbor.get('дома') or []) if d.get('факты')]
    if not doma:
        stroki.append('По домам за день ничего существенного.')
    for d in doma:
        stroki.append(f"🏠 {d.get('адрес')}")
        for f in d['факты']:
            stroki.append(f"   • {f.get('что')}")
    povislo = [p for p in (razbor.get('повисло') or []) if str(p).strip()]
    if povislo:
        stroki += ['', '⏳ Осталось без ответа:']
        stroki += [f'   • {p}' for p in povislo]
    return '\n'.join(stroki)


async def razbor_loop(bot):
    """Раз в сутки вечером — разобрать день и отправить итог руководству."""
    while True:
        now = datetime.now(db.IRKUTSK_TZ)
        wait = ((HOUR - now.hour) % 24) * 3600 - now.minute * 60 - now.second
        await asyncio.sleep(wait if wait > 60 else wait + 24 * 3600)
        day = datetime.now(db.IRKUTSK_TZ).strftime('%d.%m.%Y')
        try:
            if db.day_already_parsed(_iso(day)):
                log.info('День %s уже разобран', day)
                continue
            razbor = await razobrat_den(day)
            if not razbor:
                continue
            n = sohranit(day, razbor)
            log.info('Разбор дня %s: фактов записано %s', day, n)
            if n:
                await razoslat(bot, svodka(day, razbor))
        except Exception:
            log.exception('Не удалось разобрать день')


async def razoslat(bot, text: str):
    """Итог дня — тем, кто отвечает за хозяйство."""
    for u in db.list_users():
        if u['role'] not in ('admin', 'engineer'):
            continue
        try:
            await bot.send_message(user_id=u['user_id'], text=text)
        except Exception:
            log.warning('Не удалось отправить итог дня %s', u['user_id'], exc_info=True)
