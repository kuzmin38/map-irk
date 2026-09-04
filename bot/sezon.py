"""Сезонные работы: то, что делается каждый год в одно и то же время.

Заказчик: «на зиму нужно открывать краны на перемычке между ливнёвой и
домовой канализацией, на лето закрывать; виброставки просматривать. Такие
работы есть, нужно постоянно что-то открывать, закрывать, смотреть. Лучше,
наверное, в плановые работы всё это вносить и чтобы напоминание было от
Люси, в какое число открыть ту задвижку, другую задвижку».

Так и сделано: сезонная запись — это не новый вид работы, а правило, по
которому раз в год сама собой заводится обычная кампания с работами по
домам. Всё, что уже умеют работы — сроки, ответственные, отметка «сдано»,
прогресс «17 из 25» — работает и здесь, дописывать ничего не пришлось.

Хранится только повторяемость: что, какого числа и за сколько дней
предупредить.
"""
import re
from datetime import date

MESYATSY = {
    'январ': 1, 'феврал': 2, 'март': 3, 'апрел': 4, 'ма': 5, 'июн': 6,
    'июл': 7, 'август': 8, 'сентябр': 9, 'октябр': 10, 'ноябр': 11,
    'декабр': 12,
}
NAZVANIYA = ['', 'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
             'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']

TRIGGER = re.compile(
    r'^\s*(сезонн\w*(?:\s+работ\w*)?|ежегодн\w*|кажд\w+\s+год\w*)\b\s*[:—-]?\s*',
    re.I)

# «15 октября», «15.10», «1 апреля»
DATA_SLOVOM = re.compile(
    r'\b(\d{1,2})\s*(?:-?[а-я]{0,2}\s+)?('
    + '|'.join(MESYATSY) + r')[а-я]*\b', re.I)
DATA_TSIFRAMI = re.compile(r'\b(\d{1,2})\s*[./]\s*(\d{1,2})(?!\s*[./]?\d)\b')

# «за неделю», «за 10 дней», «за две недели»
ZARANEE = re.compile(
    r'\bза\s+(\d{1,3})\s*(дн|день|дня|дней|недел)', re.I)
ZARANEE_SLOVOM = re.compile(r'\bза\s+(недел\w+|две\s+недел\w+|месяц)', re.I)

# «по всем домам», «по ЖК Квартал»
PO_ZHK = re.compile(r'\bпо\s+жк\s+([^,.;]+)', re.I)
VSE_DOMA = 'all'

PREDUPREDIT = 7          # по умолчанию — за неделю
MAX_PREDUPREDIT = 90


def _mesyats(slovo: str):
    slovo = slovo.lower()
    for koren, nomer in MESYATSY.items():
        if slovo.startswith(koren):
            return nomer
    return None


def data(text: str):
    """(день, месяц) из фразы — или None, если числа не назвали."""
    m = DATA_SLOVOM.search(text or '')
    if m:
        den, mes = int(m.group(1)), _mesyats(m.group(2))
        if mes and 1 <= den <= 31:
            return den, mes
    m = DATA_TSIFRAMI.search(text or '')
    if m:
        den, mes = int(m.group(1)), int(m.group(2))
        if 1 <= den <= 31 and 1 <= mes <= 12:
            return den, mes
    return None


def zaranee(text: str) -> int:
    """За сколько дней предупредить. По умолчанию — за неделю."""
    m = ZARANEE.search(text or '')
    if m:
        skolko = int(m.group(1))
        if m.group(2).startswith('недел'):
            skolko *= 7
        return max(1, min(skolko, MAX_PREDUPREDIT))
    m = ZARANEE_SLOVOM.search(text or '')
    if m:
        slovo = m.group(1).lower()
        if slovo.startswith('две'):
            return 14
        return 30 if slovo == 'месяц' else 7
    return PREDUPREDIT


def ubrat_sluzhebnoe(text: str) -> str:
    """Оставляет от фразы только само дело: без даты, срока и охвата."""
    chistoe = TRIGGER.sub('', text or '')
    for vyrazhenie in (DATA_SLOVOM, DATA_TSIFRAMI, ZARANEE, ZARANEE_SLOVOM, PO_ZHK):
        chistoe = vyrazhenie.sub(' ', chistoe)
    chistoe = re.sub(r'\bпо\s+всем\s+дом\w*\b', ' ', chistoe, flags=re.I)
    chistoe = re.sub(r'\s*[,;]\s*(?=[,;]|$)', ' ', chistoe)
    chistoe = re.sub(r'\s{2,}', ' ', chistoe).strip(' ,;:—-.')
    return chistoe


def parse(text: str):
    """Разбирает фразу в сезонную запись.

    Возвращает dict или None, если непонятно, что делать. Дату не
    угадываем: без числа напоминать не о чем, и лучше переспросить.
    """
    if not text:
        return None
    kogda = data(text)
    delo = ubrat_sluzhebnoe(text)
    if not delo or len(delo) < 4:
        return None
    zhk = PO_ZHK.search(text)
    return {
        'title': delo[0].upper() + delo[1:],
        'day': kogda[0] if kogda else None,
        'month': kogda[1] if kogda else None,
        'lead_days': zaranee(text),
        'complex_name': zhk.group(1).strip() if zhk else None,
    }


def v_godu(year: int, month: int, day: int) -> date:
    """Дата этого правила в конкретном году. 29 февраля в невисокосный — 28-е."""
    while day > 1:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, 1)


def pora(zapis, today: date) -> bool:
    """Пора ли заводить работы: подошёл срок предупреждения, и в этом году
    правило ещё не срабатывало."""
    if not zapis['active'] or not zapis['month']:
        return False
    if zapis['last_year'] and zapis['last_year'] >= today.year:
        return False
    srok = v_godu(today.year, zapis['month'], zapis['day'])
    return today >= srok - _dney(zapis['lead_days'])


def _dney(n):
    from datetime import timedelta
    return timedelta(days=n or PREDUPREDIT)


def kogda_slovami(zapis) -> str:
    if not zapis['month']:
        return 'дата не назначена'
    return f"{zapis['day']} {NAZVANIYA[zapis['month']]}"


def stroka(zapis, today: date | None = None) -> str:
    """Строка для экрана сезонных работ."""
    today = today or date.today()
    znak = '🌱' if zapis['active'] else '⏸'
    hvost = ''
    if zapis['month']:
        srok = v_godu(today.year, zapis['month'], zapis['day'])
        if zapis['last_year'] and zapis['last_year'] >= today.year:
            hvost = ' — в этом году заведено'
        else:
            dney = (srok - today).days
            if dney < 0:
                srok = v_godu(today.year + 1, zapis['month'], zapis['day'])
                dney = (srok - today).days
            hvost = f' — через {dney} дн.' if dney else ' — сегодня'
    return f"{znak} {kogda_slovami(zapis)} · {zapis['title']}{hvost}"
