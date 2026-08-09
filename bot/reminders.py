"""Фоновые напоминания: Люся сама пишет ответственным про сроки.

Раз в полчаса (начиная с 08:00 по Иркутску) проверяет открытые работы
с назначенным исполнителем: просроченные и со сроком сегодня/завтра.
Каждому напоминает не чаще раза в день.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta

from . import db, houses

log = logging.getLogger('reminders')

CHECK_INTERVAL = 30 * 60  # секунд


def _reminder_text(w) -> str:
    h = houses.HOUSES_BY_ID.get(w['house_id'])
    addr = h['address'] if h else '?'
    dl = date.fromisoformat(w['deadline'])
    days = (dl - datetime.now(db.IRKUTSK_TZ).date()).days
    if days < 0:
        head = f'⚠️ Просрочено на {-days} дн.!'
    elif days == 0:
        head = '🔥 Срок — сегодня!'
    else:
        head = '⏰ Срок — завтра!'
    return (f"{head}\nРабота №{w['id']}: {addr} — {w['title']}\n"
            'Как сдадите — отметьте «✅ Сдано» в карточке (меню → 🧰 Мои работы).')


async def reminder_loop(bot):
    while True:
        try:
            now = datetime.now(db.IRKUTSK_TZ)
            if now.hour >= 8:
                today = now.date().isoformat()
                until = (now.date() + timedelta(days=1)).isoformat()
                for w in db.list_due_works(until, today):
                    try:
                        await bot.send_message(user_id=w['assignee_id'], text=_reminder_text(w))
                    except Exception:
                        log.warning('Не доставлено напоминание по работе %s', w['id'])
                    db.update_work(w['id'], last_reminded=today)
        except Exception:
            log.exception('Сбой в цикле напоминаний')
        await asyncio.sleep(CHECK_INTERVAL)
