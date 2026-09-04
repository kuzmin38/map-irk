"""Фоновые напоминания: Люся сама пишет ответственным про сроки.

Раз в полчаса (начиная с 08:00 по Иркутску) проверяет открытые работы
с назначенным исполнителем: просроченные и со сроком сегодня/завтра.
Каждому напоминает не чаще раза в день.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta

from . import db, houses, remind, sezon

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


# Кому что сообщать про поверку. Руководителя дёргаем только по факту
# просрочки: заранее это забота инженера и мастеров.
ITR_ROLES = ('admin', 'engineer', 'master')
OVERDUE_ROLES = ('admin', 'engineer', 'director')

# Весной, до летней сдачи тепловых узлов, инженер должен знать обо всём,
# что просрочится в этом году: замену планируют на лето, а не по факту.
SPRING_MONTHS = (4, 5)
SPRING_EVERY_DAYS = 30    # в апреле и в мае — по разу
SOON_DAYS = 30            # подстраховка: прибор мог появиться уже после весны
OVERDUE_EVERY_DAYS = 7    # просрочка — действующая проблема, напоминаем чаще


def _davno_li(dev, today: date, days: int) -> bool:
    """Прошло ли достаточно времени с прошлого напоминания об этом приборе."""
    if not dev['last_reminded']:
        return True
    return (today - date.fromisoformat(dev['last_reminded'])).days >= days


def _device_line(dev, today: date) -> str:
    h = houses.HOUSES_BY_ID.get(dev['house_id'])
    mesto = ' '.join(x for x in (dev['tp'], dev['place']) if x)
    srok = date.fromisoformat(dev['verified_until'])
    left = (srok - today).days
    kogda = (f'просрочена на {-left} дн.' if left < 0
             else f"до {srok.strftime('%d.%m.%Y')} (осталось {left} дн.)")
    return f"• {h['address'] if h else '?'} — {mesto}, № {dev['serial'] or '—'}: {kogda}"


async def _send_to(bot, roles, text):
    for u in db.list_users():
        if u['role'] in roles:
            try:
                await bot.send_message(user_id=u['user_id'], text=text)
            except Exception:
                log.warning('Не доставлено напоминание о поверке пользователю %s',
                            u['user_id'])


async def _check_verifications(bot, today: date):
    """Поверка манометров: заранее — ИТР, о просрочке — ещё и руководителю."""
    zaranee, prosrocheno = [], []
    for dev in db.devices_with_verification():
        srok = date.fromisoformat(dev['verified_until'])
        if srok < today:
            if _davno_li(dev, today, OVERDUE_EVERY_DAYS):
                prosrocheno.append(dev)
        elif ((today.month in SPRING_MONTHS and srok.year == today.year
               and _davno_li(dev, today, SPRING_EVERY_DAYS))
              or ((srok - today).days <= SOON_DAYS
                  and _davno_li(dev, today, SOON_DAYS))):
            zaranee.append(dev)

    if zaranee:
        await _send_to(bot, ITR_ROLES,
                       '🔧 ПОВЕРКА МАНОМЕТРОВ В ЭТОМ ГОДУ\n\n'
                       + '\n'.join(_device_line(d, today) for d in zaranee)
                       + '\n\nЛетом сдача тепловых узлов — планируйте замену '
                         'или поверку заранее.')
    if prosrocheno:
        await _send_to(bot, OVERDUE_ROLES,
                       '❌ ПОВЕРКА ПРОСРОЧЕНА\n\n'
                       + '\n'.join(_device_line(d, today) for d in prosrocheno)
                       + '\n\nЗамена не отмечена. Прибор считается негодным.')

    for dev in zaranee + prosrocheno:
        db.update_device(dev['id'], last_reminded=today.isoformat())


async def _send_asked(bot):
    """Напоминания, о которых просили люди: «напомни завтра в 9 про опрессовку».

    Пишем туда же, где просили: попросили в рабочем чате — напомним в чат,
    в личке — в личку. Иначе напоминание находит не тех.

    Кто просил — не пишем. В чате это лишнее: напоминание адресовано делу,
    а не человеку, и подпись превращает его в «Андрей велел».
    """
    for r in db.due_reminders():
        text = f"⏰ Напоминаю: {r['text']}"
        try:
            if r['chat_id']:
                await bot.send_message(chat_id=r['chat_id'], text=text)
            else:
                await bot.send_message(user_id=r['user_id'], text=text)
        except Exception:
            log.warning('Не доставлено напоминание %s', r['id'], exc_info=True)
            continue
        db.mark_reminder_sent(r['id'])
        log.info('Напоминание %s отправлено: %.50s', r['id'], r['text'])


# Через сколько напомнить про перекрытый стояк. Заказчик про это и говорил:
# «опять забудут краны перекрытия, опять будет мешаться»
STOYAK_CHASOV = 4


async def _check_shutoffs(bot):
    """Стояк перекрыт четвёртый час — напоминаем тому, кто перекрывал."""
    from . import handlers, houses, stoyak

    for z in db.open_shutoffs():
        if z['reminded'] or not z['by_id']:
            continue
        minut = handlers._minut_s(z['closed_at'])
        if minut < STOYAK_CHASOV * 60:
            continue
        dom = houses.HOUSES_BY_ID.get(z['house_id'])
        try:
            await bot.send_message(
                user_id=z['by_id'],
                text=f"🚫 Напоминаю: стояк на {dom['address'] if dom else '—'}, "
                     f"кв. {z['flat']} перекрыт уже {stoyak.dlitelnost(minut)}.\n"
                     'Если открыли — так и напишите: «открыл стояк». Адрес '
                     'называть не нужно, я помню.')
        except Exception:
            log.warning('Не доставлено напоминание о стояке %s', z['id'])
        db.mark_shutoff_reminded(z['id'])


async def _check_seasonal(bot, today: date):
    """Сезонные работы: подошёл срок — заводим задание с работами по домам.

    Работы обычные, как любые другие: сроки, ответственные, отметка
    «сдано», прогресс «17 из 25». Сезонная запись только заводит их сама,
    раз в год, и за неделю до числа — чтобы успеть распределить.
    """
    for zapis in db.list_seasonal(active_only=True):
        if sezon.pora(zapis, today):
            await zavesti_sezonnuyu(bot, zapis, today)


async def zavesti_sezonnuyu(bot, zapis, today: date):
    """Заводит задание по одному сезонному правилу.

    Отдельно от проверки срока: то же самое делает кнопка «завести сейчас»,
    и делать это она должна ровно так же, а не похоже.
    """
    if not zapis or not zapis['month']:
        return
    srok = sezon.v_godu(today.year, zapis['month'], zapis['day'])
    doma = _doma_pravila(zapis)
    if not doma:
        log.warning('Сезонная работа %s: нет домов в охвате', zapis['id'])
        db.update_seasonal(zapis['id'], last_year=today.year)
        return
    camp_id = db.add_campaign(zapis['title'], zapis['complex_id'],
                              srok.isoformat(), zapis['created_by'],
                              zapis['created_by_name'] or 'Люся')
    for hid in doma:
        db.add_work(hid, zapis['title'], srok.isoformat(),
                    zapis['created_by_name'] or 'Люся',
                    user_id=zapis['created_by'], campaign_id=camp_id)
    db.update_seasonal(zapis['id'], last_year=today.year)
    log.info('Сезонная работа «%s»: заведено %s работ на %s',
             zapis['title'], len(doma), srok)
    ostalos = (srok - today).days
    kogda = ('срок сегодня' if ostalos <= 0
             else f'срок {srok.strftime("%d.%m")}, осталось {ostalos} дн.')
    await _send_to(bot, ITR_ROLES,
                   f'🌱 СЕЗОННАЯ РАБОТА\n\n{zapis["title"]}\n'
                   f'Домов: {len(doma)}, {kogda}.\n\n'
                   'Работы уже в плане — распределите по людям '
                   '(меню → 📋 Работы).')


def _doma_pravila(zapis) -> list:
    """Дома, на которые заводится сезонная работа."""
    if zapis['complex_id'] == sezon.VSE_DOMA:
        return [h['id'] for h in houses.HOUSES if h.get('kind') != 'nonres']
    naznacheno = db.all_house_complexes()
    return [h['id'] for h in houses.HOUSES
            if naznacheno.get(h['id']) == zapis['complex_id']
            and h.get('kind') != 'nonres']


async def asked_loop(bot):
    """Отдельный цикл: просьбы проверяем каждую минуту, а не раз в полчаса.

    «Напомни через 20 минут перекрыть стояк» с получасовым шагом — это уже
    не напоминание.
    """
    while True:
        try:
            await _send_asked(bot)
            await _check_shutoffs(bot)
        except Exception:
            log.exception('Сбой в цикле напоминаний по просьбе')
        await asyncio.sleep(60)


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
                await _check_seasonal(bot, today)
        except Exception:
            log.exception('Сбой в цикле напоминаний')
        await asyncio.sleep(CHECK_INTERVAL)
