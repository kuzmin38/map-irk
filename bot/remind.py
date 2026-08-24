"""Напоминания по просьбе: «Люся, напомни завтра в 9 про опрессовку».

Заказчик попросил напомнить о работах на следующий день — и не получил
ничего. Люся ответила что-то вежливое, но поставить напоминание ей было
нечем: в боте есть только сроки работ и поверок, а «напомни мне вот об
этом» не существовало как явление.

Разбираем просьбу своим кодом, а не моделью. Время — это то место, где
догадка недопустима: лучше честно не понять и переспросить, чем поставить
на четверг то, что нужно завтра.
"""
import re
from datetime import date, datetime, timedelta

# «напомни», «напомните», «напомнить», «напомнишь»
TRIGGER = re.compile(r'(?<![а-я])напомн(и|ите|ить|ишь|и-ка)?(?![а-я])', re.IGNORECASE)

DNI = {'понедельник': 0, 'вторник': 1, 'среда': 2, 'среду': 2, 'четверг': 3,
       'пятница': 4, 'пятницу': 4, 'суббота': 5, 'субботу': 5,
       'воскресенье': 6, 'понедельника': 0, 'вторника': 1, 'среды': 2,
       'четверга': 3, 'пятницы': 4, 'субботы': 5, 'воскресенья': 6}

MESYATSY = {'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5,
            'июня': 6, 'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10,
            'ноября': 11, 'декабря': 12}

# Часть суток, когда часа не назвали
VREMYA_SUTOK = {'утром': 9, 'с утра': 8, 'днём': 13, 'днем': 13, 'к обеду': 12,
                'после обеда': 14, 'вечером': 18, 'к вечеру': 17,
                'в обед': 12, 'ночью': 22}

DEFAULT_HOUR = 9        # «напомни завтра» без часа — утром, к началу смены
MIN_AHEAD = 60          # ближе минуты ставить нечего


def _next_weekday(today: date, weekday: int) -> date:
    ahead = (weekday - today.weekday()) % 7
    return today + timedelta(days=ahead or 7)


def _clean(text: str) -> str:
    """Убирает служебные слова, оставляя суть напоминания."""
    text = re.sub(r'^[\s,:—-]+|[\s,:—-]+$', '', text)
    text = re.sub(r'^@?люс[яеию][\s,]+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^(мне|нам|всем|нас|меня|ребятам|в\s+чат|пожалуйста)[\s,]+',
                  '', text, flags=re.IGNORECASE)
    text = re.sub(r'^(про|о|об|обо|что|чтобы|насч[её]т)[\s,]+', '', text,
                  flags=re.IGNORECASE)
    return text.strip(' ,.;:—-')


def parse_reminder(text: str, now: datetime | None = None):
    """Разбирает просьбу напомнить. Возвращает (когда, о чём) или None.

    Когда — datetime в том же поясе, что и now. Если срок понять не удалось,
    возвращаем None: угадывать время нельзя.
    """
    if not text or not TRIGGER.search(text):
        return None
    now = now or datetime.now()
    low = text.lower().replace('ё', 'е')

    # 1. Через сколько-то времени — считается от «сейчас»
    m = re.search(r'через\s+(\d+)\s*(минут\w*|мин|час\w*|дн\w*|недел\w*)', low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith(('минут', 'мин')):
            when = now + timedelta(minutes=n)
        elif unit.startswith('час'):
            when = now + timedelta(hours=n)
        elif unit.startswith('недел'):
            when = now + timedelta(weeks=n)
        else:
            when = now + timedelta(days=n)
        ostatok = TRIGGER.sub(' ', text)
        ostatok = re.sub(r'через\s+\d+\s*\S+', ' ', ostatok, flags=re.IGNORECASE)
        return when, _clean(ostatok)

    den = None
    den_nazvan = False   # день назвали прямо — не переносим его сами
    kuski = []           # что вырезать из текста напоминания

    # 2. День
    if re.search(r'(?<![а-я])послезавтра(?![а-я])', low):
        den = now.date() + timedelta(days=2)
        kuski.append(r'послезавтра')
        den_nazvan = True
    elif re.search(r'(?<![а-я])завтра(?![а-я])', low):
        den = now.date() + timedelta(days=1)
        kuski.append(r'завтра')
        den_nazvan = True
    elif re.search(r'(?<![а-я])сегодня(?![а-я])', low):
        den = now.date()
        kuski.append(r'сегодня')
        den_nazvan = True
    else:
        m = re.search(r'(?<![а-я])(\d{1,2})\s+(' + '|'.join(MESYATSY) + r')(?![а-я])', low)
        if m:
            god = now.year
            den = date(god, MESYATSY[m.group(2)], int(m.group(1)))
            if den < now.date():
                den = date(god + 1, MESYATSY[m.group(2)], int(m.group(1)))
            kuski.append(re.escape(m.group(0)))
            den_nazvan = True
        else:
            m = re.search(r'(?<![\d.])(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?![\d.])', low)
            if m:
                god = int(m.group(3) or now.year)
                god += 2000 if god < 100 else 0
                try:
                    den = date(god, int(m.group(2)), int(m.group(1)))
                except ValueError:
                    den = None
                else:
                    kuski.append(re.escape(m.group(0)))
                    den_nazvan = True
            if den is None:
                for name, wd in DNI.items():
                    if re.search(rf'(?<![а-я]){name}(?![а-я])', low):
                        den = _next_weekday(now.date(), wd)
                        kuski.append(rf'(в|во)?\s*{name}')
                        den_nazvan = True
                        break

    # 3. Час
    chas = minuta = None
    m = re.search(r'(?<![а-я])(?:в|к|около)\s+(\d{1,2})[:.\s]?(\d{2})?\s*'
                  r'(?:час\w*|ч)?(?![\d])', low)
    if m:
        chas = int(m.group(1))
        minuta = int(m.group(2) or 0)
        if chas > 23 or minuta > 59:
            chas = minuta = None
        else:
            kuski.append(re.escape(m.group(0)))
            # «в 5 вечера» — это 17 часов
            if chas < 12 and re.search(r'вечера|ночи', low):
                chas += 12
    if chas is None:
        for slovo, h in VREMYA_SUTOK.items():
            if slovo in low:
                chas, minuta = h, 0
                kuski.append(re.escape(slovo))
                break

    if den is None and chas is None:
        return None          # срок не назван — угадывать нельзя

    if den is None:
        den = now.date()
    if chas is None:
        chas, minuta = DEFAULT_HOUR, 0

    when = datetime(den.year, den.month, den.day, chas, minuta,
                    tzinfo=now.tzinfo)
    # «напомни в 8», а уже девять вечера — значит, завтра в восемь. Но если
    # день назвали прямо, переносить нельзя: пусть Люся скажет, что поздно
    if (when - now).total_seconds() < MIN_AHEAD and not den_nazvan:
        when += timedelta(days=1)

    ostatok = TRIGGER.sub(' ', text)
    for kusok in kuski:
        ostatok = re.sub(kusok, ' ', ostatok, count=1, flags=re.IGNORECASE)
    return when, _clean(ostatok)


def fmt_when(when: datetime, now: datetime | None = None) -> str:
    """«завтра в 09:00», «в пятницу в 14:30», «25.08 в 09:00»."""
    now = now or datetime.now(when.tzinfo)
    delta = (when.date() - now.date()).days
    chas = when.strftime('%H:%M')
    if delta == 0:
        return f'сегодня в {chas}'
    if delta == 1:
        return f'завтра в {chas}'
    if delta == 2:
        return f'послезавтра в {chas}'
    if 0 < delta < 7:
        dni = ['в понедельник', 'во вторник', 'в среду', 'в четверг',
               'в пятницу', 'в субботу', 'в воскресенье']
        return f'{dni[when.weekday()]} в {chas}'
    return f"{when.strftime('%d.%m')} в {chas}"
