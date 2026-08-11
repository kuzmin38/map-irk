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


VERIFY_WARN_DAYS = 30  # за сколько дней предупреждать об истечении поверки


async def _check_verifications(bot, today: date):
    """Поверка манометров: предупреждаем инженера, админа и руководителя."""
    until = (today + timedelta(days=VERIFY_WARN_DAYS)).isoformat()
    due = db.devices_verification_due(until, today.isoformat())
    if not due:
        return
    lines = []
    for d in due:
        h = houses.HOUSES_BY_ID.get(d['house_id'])
        left = (date.fromisoformat(d['verified_until']) - today).days
        state = f'просрочена на {-left} дн.' if left < 0 else f'осталось {left} дн.'
        lines.append(f"• {h['address'] if h else '?'} — {d['tp'] or ''} {d['place']}, "
                     f"№ {d['serial'] or '—'}: {state}")
        db.update_device(d['id'], last_reminded=today.isoformat())
    text = ('🔧 ПОВЕРКА МАНОМЕТРОВ\n\n' + '\n'.join(lines) +
            '\n\nПора планировать замену или поверку.')
    for u in db.list_users():
        if u['role'] in ('admin', 'engineer', 'director', 'master'):
            try:
                await bot.send_message(user_id=u['user_id'], text=text)
            except Exception:
                log.warning('Не доставлено напоминание о поверке пользователю %s', u['user_id'])


async def reminder_loop(bot):
    while True:
        try:
            now = datetime.now(db.IRKUTSK_TZ)
            if now.hour >= 8:
                today = now.date()
                today_iso = today.isoformat()
                until = (today + timedelta(days=1)).isoformat()
                for w in db.list_due_works(until, today_iso):
                    try:
                        await bot.send_message(user_id=w['assignee_id'], text=_reminder_text(w))
                    except Exception:
                        log.warning('Не доставлено напоминание по работе %s', w['id'])
                    db.update_work(w['id'], last_reminded=today_iso)
                await _check_verifications(bot, today)
        except Exception:
            log.exception('Сбой в цикле напоминаний')
        await asyncio.sleep(CHECK_INTERVAL)
