"""«Люся, сохрани» — список работ из сообщения превращается в задачи.

Мастер присылает в чат план работ по тепловым пунктам одним сообщением:
адреса вперемешку с работами, без всякой структуры. Раньше на просьбу
сохранить Люся честно отвечала, что не умеет: инструменты у неё только
на чтение.

Разбирает модель — сплошной текст на пункты кодом не разложить. Но
записывает не она: человек сначала видит разбор и подтверждает. Модель
может слепить два пункта в один или потерять адрес, и такое должно
всплывать до записи, а не после.
"""
import json
import logging
import re

from . import ai, houses

log = logging.getLogger('plan')

ZADANIE = (
    'Ниже — план работ, присланный мастером в рабочий чат. Разложи его на '
    'отдельные пункты.\n\n{text}\n\n'
    'Верни строго JSON без пояснений: {{"пункты": [{{"адрес": "...", '
    '"работа": "..."}}]}}\n'
    'Правила:\n'
    '— адрес пиши так, как он назван в тексте, ничего не дописывая и не '
    'исправляя;\n'
    '— работу — коротким деловым языком, одной строкой, без адреса внутри;\n'
    '— если в одном месте перечислено несколько работ, сделай несколько '
    'пунктов;\n'
    '— если один адрес относится к нескольким работам или наоборот — '
    'разложи по парам;\n'
    '— ничего не добавляй от себя: ни сроков, ни исполнителей, ни работ, '
    'которых в тексте нет;\n'
    '— если адрес у пункта не назван, поставь адрес пустой строкой.'
)


def looks_like_plan(text: str) -> bool:
    """Похоже ли сообщение на список работ, а не на болтовню."""
    if not text or len(text) < 25:
        return False
    rabota = re.search(
        r'(?<![а-я])(ремонт\w*|замен\w+|протяжк\w+|промывк\w+|опрессовк\w+|'
        r'ревизи\w+|очистк\w+|устан\w+|демонтаж\w*|монтаж\w*|прочистк\w+|'
        r'работ\w+|обход\w*|проверк\w+)(?![а-я])', text, re.IGNORECASE)
    return bool(rabota)


async def parse_plan(text: str) -> list:
    """Пункты плана: [{'address', 'house', 'work'}]. Пустой список — не вышло.

    house — дом из справочника или None, если адрес не опознан. Такие
    пункты не выбрасываем: человек увидит их и подскажет адрес.
    """
    otvet = await ai.ask(ZADANIE.format(text=text), max_tokens=900, temperature=0)
    if not otvet:
        return []
    m = re.search(r'\{.*\}', otvet, re.S)
    if not m:
        log.warning('Модель вернула не JSON: %.120s', otvet)
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        log.warning('JSON не разобрался: %.120s', m.group(0))
        return []

    punkty = []
    for item in data.get('пункты') or []:
        rabota = (item.get('работа') or '').strip()
        if not rabota:
            continue
        adres = (item.get('адрес') or '').strip()
        dom = houses.detect_house(adres) if adres else None
        punkty.append({'address': adres, 'house': dom, 'work': rabota})
    return punkty


def preview(punkty: list) -> str:
    """Как разбор выглядит перед записью."""
    lines = []
    for i, p in enumerate(punkty, 1):
        if p['house']:
            lines.append(f"{i}. {p['house']['address']} — {p['work']}")
        else:
            nazvan = f" ({p['address']})" if p['address'] else ''
            lines.append(f"{i}. ⚠️ дом не опознан{nazvan} — {p['work']}")
    return '\n'.join(lines)


# ---------- Выбор пунктов ----------

CHOICE = re.compile(r'^[\s,;и0-9первыхйедпоследнбкромевсёе.\-–—]+$', re.IGNORECASE)


def parse_choice(text: str, vsego: int) -> set | None:
    """Какие пункты выбрал человек. Индексы с нуля, None — не про выбор.

    «Первые 4 пункта сохрани», «1-4», «1,3,5», «все», «кроме 5». Заказчик
    написал «первые 4» — и это самый естественный способ, а Люся его не
    поняла и попыталась разобрать свой же список заново.
    """
    if not text or vsego <= 0:
        return None
    low = text.lower().replace('ё', 'е').strip()

    m = re.search(r'(?<![а-я])(перв\w+|последн\w+)\s+(\d+|\w+)', low)
    if m:
        n = _chislo(m.group(2))
        if n:
            n = min(n, vsego)
            return set(range(n)) if m.group(1).startswith('перв') else \
                set(range(vsego - n, vsego))

    # «второй пункт», «третий пункт сохрани» — один пункт по порядку
    m = re.search(r'(?<![а-я])(' + '|'.join(PORYADOK) + r')\w*\s+пункт', low)
    if m:
        return {PORYADOK[m.group(1)] - 1}

    krome = re.search(r'(?<![а-я])(кроме|без)\s+([\d\s,;и-]+)', low)
    if krome:
        ubrat = _nomera(krome.group(2), vsego)
        if ubrat:
            return set(range(vsego)) - ubrat

    if re.fullmatch(r'(все|всё|все\s+пункты|целиком)[.!]?', low):
        return set(range(vsego))

    nomera = _nomera(low, vsego)
    # Одинокое число в свободной фразе за выбор не считаем: «сохрани 1»
    # понятно, а «поеду в 14» — нет
    if nomera and (re.search(r'(пункт|сохран|запиш|занес|только|с\s*\d+\s*по)', low)
                   or CHOICE.fullmatch(low)):
        return nomera
    return None


# Порядковые словом: «второй пункт». Основы, чтобы падеж не мешал
PORYADOK = {'перв': 1, 'втор': 2, 'трет': 3, 'четверт': 4, 'пят': 5,
            'шест': 6, 'седьм': 7, 'восьм': 8, 'девят': 9, 'десят': 10}

SLOVA = {'один': 1, 'два': 2, 'две': 2, 'три': 3, 'четыре': 4, 'пять': 5,
         'шесть': 6, 'семь': 7, 'восемь': 8, 'девять': 9, 'десять': 10}


def _chislo(s: str):
    if s.isdigit():
        return int(s)
    return SLOVA.get(s)


def _nomera(text: str, vsego: int) -> set:
    """Номера и диапазоны: «1-4», «1,3,5», «с 2 по 6»."""
    out = set()
    for a, b in re.findall(r'(\d+)\s*(?:[-–—]|по)\s*(\d+)', text):
        out |= {i for i in range(int(a) - 1, int(b)) if 0 <= i < vsego}
    bez_diapazonov = re.sub(r'\d+\s*(?:[-–—]|по)\s*\d+', ' ', text)
    for n in re.findall(r'\d+', bez_diapazonov):
        i = int(n) - 1
        if 0 <= i < vsego:
            out.add(i)
    return out
