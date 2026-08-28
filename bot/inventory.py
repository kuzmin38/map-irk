"""Опись имущества: что где лежит.

Парковку топило, а в компании была мотопомпа — и об этом никто не
вспомнил. Насосы, пушки, туры, инструмент расходятся по подвалам и
бытовкам, и знание о них живёт в голове того, кто их туда занёс. Через
полгода проще купить заново, чем найти.

Здесь опись: вещь, место и адрес. Пишется одной строкой из чата, ищется
одним словом. Строгого списка названий нет намеренно — «мотопомпа»,
«помпа» и «насос грязевой» найдутся друг по другу, потому что человек в
подвале вспоминает вещь, а не её карточку.
"""
import re

# Прямая просьба записать. Как и с паспортом, без неё в опись ничего не
# попадает: иначе туда стечёт вся болтовня чата
TRIGGER = re.compile(
    r'(?<![а-я])(в\s+инвентар\w*|на\s+инвентар\w*|в\s+опись|в\s+описи|'
    r'на\s+уч[её]т|инвентаризаци\w*)(?![а-я])', re.IGNORECASE)

# «Где мотопомпа?», «где у нас тепловая пушка» — вопрос, а не запись
VOPROS = re.compile(
    r'(?<![а-я])где(?:\s+(?:у\s+нас|наш\w*|лежит|лежат|стоит|хранится))?\s+'
    r'(?P<chto>[^?.!\n]{3,60})', re.IGNORECASE)

# Слова, после которых начинается место, а не название вещи
MESTA = r'подвал\w*|итп|тепл\w+\s+пункт\w*|бытовк\w*|склад\w*|гараж\w*|' \
        r'офис\w*|машин\w*|паркинг\w*|парковк\w*|техэтаж\w*|чердак\w*|' \
        r'колясочн\w*|электрощитов\w*|насосн\w*'

# Служебные слова, которые не должны попасть в название
_MUSOR = re.compile(r'^(?:@?люс[яеию][\s,]*)?(?:пожалуйста[\s,]*)?'
                    r'(?:запиши|запишите|запомни|запомните|занеси|занесите|'
                    r'добавь|добавьте|внеси|внесите|поставь|поставьте)?[\s,:—-]*',
                    re.IGNORECASE)


def wants_add(text: str) -> bool:
    return bool(text) and bool(TRIGGER.search(text))


def strip_trigger(text: str) -> str:
    out = TRIGGER.sub(' ', text or '')
    out = _MUSOR.sub('', out.strip())
    out = re.sub(r'[ \t]{2,}', ' ', out)
    return out.strip(' ,.;:—-')


def parse_qty(text: str) -> tuple[int, str]:
    """Количество и текст без него: «2 шт», «х3», «3 штуки»."""
    m = re.search(r'(?<![а-я\d])(\d{1,3})\s*(?:шт\w*|штук\w*)(?![а-я])', text, re.I)
    if not m:
        m = re.search(r'(?<![а-я\d])[хx]\s?(\d{1,3})(?![\d])', text, re.I)
    if not m:
        # «2 тепловые пушки» — число впереди названия
        m = re.match(r'\s*(\d{1,3})\s+(?=[А-Яа-яЁё])', text)
    if not m:
        return 1, text
    n = int(m.group(1))
    ochishchen = (text[:m.start()] + ' ' + text[m.end():]).strip(' ,.;:—-')
    return (n if 1 <= n <= 999 else 1), re.sub(r'[ \t]{2,}', ' ', ochishchen)


def parse_add(text: str):
    """Разбирает «мотопомпа, подвал, Седова 65/2» → (название, место, дом, сколько).

    Дом ищем по всему тексту, а не по одной части: адрес пишут и через
    запятую, и слитно с местом — «в подвале на Седова 65/2».
    """
    from . import houses

    chistyy = strip_trigger(text)
    if not chistyy:
        return None
    kolichestvo, chistyy = parse_qty(chistyy)

    dom = houses.detect_house(chistyy)
    chasti = [c.strip(' ,.;:—-') for c in re.split(r'[,;\n]|\s+—\s+', chistyy)]
    chasti = [c for c in chasti if c]
    if not chasti:
        return None

    # Часть, в которой нашёлся адрес, в название и место не идёт
    if dom:
        ostatok = []
        for c in chasti:
            if houses.detect_house(c):
                # «мотопомпа в подвале на Седова 65/2» — вырезаем только адрес,
                # всё остальное в этой части ещё нужно
                c = ubrat_adres(c, dom)
            if c:
                ostatok.append(c)
        chasti = ostatok or chasti

    nazvanie = chasti[0]
    mesto = ', '.join(chasti[1:])
    # «мотопомпа в подвале» одной фразой, без запятой
    if not mesto:
        m = re.search(rf'(?<![а-я])(?:в|во|на|под)\s+({MESTA})', nazvanie, re.I)
        if m and m.start() > 2:
            mesto = nazvanie[m.start():].strip(' ,.;:—-')
            nazvanie = nazvanie[:m.start()].strip(' ,.;:—-')
    nazvanie = re.sub(r'^(?:у\s+нас\s+)?(?:есть\s+)?', '', nazvanie, flags=re.I).strip()
    if len(nazvanie) < 3:
        return None
    return nazvanie, mesto, dom, kolichestvo


def ubrat_adres(chast: str, dom) -> str:
    """Вырезает из части адрес дома, оставляя название вещи и место.

    Убираем не одним шаблоном, а по кусочкам: улица бывает из двух слов
    («Красных Мадьяр»), с порядковым номером («4-я Советская») и с
    корпусом. Проще снять каждый кусок отдельно, чем угадать их порядок.
    """
    out = chast
    # порядковое в названии улицы — раньше номера дома, иначе «4» из «4-я»
    # уйдёт как номер и оставит хвост
    if re.search(r'\d{1,2}\s*-\s*[а-я]', dom['address'], re.IGNORECASE):
        out = re.sub(r'(?<![а-я\d])\d{1,2}\s*-\s*[а-я]{1,2}(?![а-я])', ' ', out,
                     count=1, flags=re.IGNORECASE)
    # слова улицы в любом падеже
    for slovo in re.findall(r'[А-Яа-яЁё]{4,}', dom['address']):
        koren = re.escape(slovo[:-2]) if len(slovo) > 5 else re.escape(slovo)
        out = re.sub(rf'(?<![а-я]){koren}[а-яё]*', ' ', out, count=1, flags=re.IGNORECASE)
    # номер дома: «65а/2», «д. 30», «14»
    # буква корпуса — только если она отдельная: «65а», но не «105 квартира»
    out = re.sub(r'(?<![\d/])(?:д\.?|дом)?\s*\d{1,3}(?:\s*[а-яё](?![а-яё]))?'
                 r'(?:\s*/\s*\d+)?(?![\d])',
                 ' ', out, count=1, flags=re.IGNORECASE)
    # предлог, оставшийся без адреса
    out = re.sub(r'(?<![а-я])(?:на|по|с)\s*(?=[\s,;.]|$)', ' ', out, flags=re.IGNORECASE)
    out = re.sub(r'[ \t]{2,}', ' ', out)
    return out.strip(' ,.;:—-')


# ---------- Поиск ----------

# Окончания, которые мешают «мотопомпы» найтись по «мотопомпа»
_HVOSTY = ('ами', 'ями', 'ого', 'ому', 'ыми', 'ими', 'ах', 'ях', 'ов', 'ев',
           'ой', 'ей', 'ом', 'ем', 'ые', 'ие', 'ый', 'ий', 'ая', 'яя', 'ую',
           'ю', 'а', 'я', 'ы', 'и', 'у', 'е', 'о')


def osnova(slovo: str) -> str:
    """Грубая основа слова: «мотопомпы» и «мотопомпа» дают одно и то же."""
    s = (slovo or '').lower().replace('ё', 'е')
    s = re.sub(r'[^0-9a-zа-я]+', '', s)
    if len(s) <= 3:
        return s
    for h in _HVOSTY:
        if s.endswith(h) and len(s) - len(h) >= 3:
            return s[:-len(h)]
    return s


def slova(text: str) -> list:
    return [osnova(w) for w in re.findall(r'[0-9A-Za-zА-Яа-яЁё]+', text or '')
            if len(w) > 1]


def matches(zapros: str, nazvanie: str, mesto: str = '') -> bool:
    """Совпала ли вещь с запросом. Достаточно одного слова из запроса."""
    q = [w for w in slova(zapros) if len(w) >= 3]
    if not q:
        return False
    est = slova(f'{nazvanie} {mesto}')
    for w in q:
        for e in est:
            # Вхождением, а не только началом: «помпа» должна найти
            # «мотопомпу», а «тура» — «вышку-туру»
            if w in e or e in w:
                return True
    return False


def chto_ishchut(text: str) -> str | None:
    """Из «где у нас мотопомпа?» достаёт «мотопомпа». None — вопрос не про вещь."""
    if not text:
        return None
    m = VOPROS.search(text)
    if not m:
        return None
    chto = m.group('chto').strip(' ,.;:—-?')
    chto = re.sub(r'(?<![а-я])(лежит|лежат|стоит|стоят|хранится|хранятся|'
                  r'находится|находятся|сейчас|вообще)(?![а-я])', ' ', chto,
                  flags=re.IGNORECASE)
    chto = re.sub(r'[ \t]{2,}', ' ', chto).strip(' ,.;:—-')
    if len(chto) < 3 or _NE_VESHCH.fullmatch(chto):
        return None
    return chto


# «Где ты была», «где все» — это не про имущество
_NE_VESHCH = re.compile(
    r'(?:ты|вы|он|она|они|мы|я|все|всё|тут|там|это|эт|люся|бот)'
    r'(?:\s+\S+)*', re.IGNORECASE)
