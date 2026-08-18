"""Работа со списком домов УК «Жемчужина»: загрузка, поиск по адресу."""
import json
import os
import re

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

ACTIVE_FILE = os.path.join(DATA_DIR, 'active_houses.txt')
COMPLEX_FILE = os.path.join(DATA_DIR, 'house_complex.txt')


def _norm_addr(s: str) -> str:
    """Адрес в сравнимый вид: без регистра, ё, лишних пробелов и слов «ул.», «дом»."""
    s = s.lower().replace('ё', 'е')
    s = re.sub(r'[.,;]', ' ', s)
    # Отдельно стоящие слова, а не буквы внутри номера: «Пограничный 1-Г»
    # и «1-Д» иначе оба превращались в «1-» и становились неразличимы
    s = re.sub(r'(?<![\w-])(ул|улица|мкр|микрорайон|д|дом|г|иркутск)(?![\w-])', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def load_active() -> set:
    """Адреса домов, которые сейчас в работе (`bot/data/active_houses.txt`).

    Пустой набор означает «ограничения нет» — показываем все дома.
    Формат файла: по адресу в строке, пустые строки и строки с # пропускаются.
    """
    if not os.path.exists(ACTIVE_FILE):
        return set()
    with open(ACTIVE_FILE, encoding='utf-8') as f:
        lines = [ln.split('#')[0].strip() for ln in f]
    return {_norm_addr(ln) for ln in lines if ln}


def load_complex_map() -> dict:
    """Привязка домов к ЖК из `bot/data/house_complex.txt`: адрес → id комплекса.

    Руками через бота её проставляют по одному дому — на десятки домов это
    мучение, а список нужен целиком: без него не работает группировка по
    комплексам и задание сразу на весь ЖК.
    """
    if not os.path.exists(COMPLEX_FILE):
        return {}
    mapping = {}
    with open(COMPLEX_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.split('#')[0].strip()
            if not line or '=' not in line:
                continue
            address, complex_id = line.split('=', 1)
            address, complex_id = _norm_addr(address), complex_id.strip()
            if address and complex_id:
                mapping[address] = complex_id
    return mapping


with open(os.path.join(DATA_DIR, 'complexes.json'), encoding='utf-8') as f:
    COMPLEXES = json.load(f)

with open(os.path.join(DATA_DIR, 'houses.json'), encoding='utf-8') as f:
    ALL_HOUSES = json.load(f)
ALL_HOUSES.sort(key=lambda h: h['address'])

ACTIVE = load_active()
# Пока список не задан — работаем со всеми домами, как раньше
HOUSES = ([h for h in ALL_HOUSES if _norm_addr(h['address']) in ACTIVE]
          if ACTIVE else list(ALL_HOUSES))

HOUSES_BY_ID = {h['id']: h for h in HOUSES}


_norm = _norm_addr


def _split_addr(s: str):
    """Разделяет адрес на название улицы и номер дома ('розы люксембург', '118/1')."""
    m = re.match(r'^(.*?)\s*(\d[\w/\-]*)$', s)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return s, ''


def _complex_aliases() -> list:
    """Как жилой комплекс называют вслух: «ЖК Четыре солнца», «четыре солнца», «4 солнца»."""
    from . import numbers

    aliases = set()
    for c in COMPLEXES:
        name = _norm(c['name'])                       # «жк четыре солнца»
        short = re.sub(r'^жк\s+', '', name)            # «четыре солнца»
        for variant in (name, short):
            aliases.add(variant)
            aliases.add(numbers.to_digits(variant, anywhere=True))   # «4 солнца»
    # длинные вперёд, иначе «жк» съест начало названия
    return sorted((a for a in aliases if a), key=len, reverse=True)


COMPLEX_ALIASES = _complex_aliases()


def _strip_complex(text: str) -> str:
    """Убирает из запроса название ЖК: адрес ищется по улице и номеру.

    Привязки домов к комплексам в базе пока нет, но и без неё «четыре солнца
    тридцатый дом» должно находиться — по номеру дома.
    """
    for alias in COMPLEX_ALIASES:
        text = text.replace(alias, ' ')
    return re.sub(r'(?<![\w-])жк(?![\w-])', ' ', text)


def _prepare(query: str) -> str:
    """Разговорный запрос — в вид, сравнимый с адресом из справочника.

    «ЖК четыре солнца, тридцатый дом» → «30-й». Название комплекса убираем
    до перевода числительных, иначе «четыре солнца» само станет числом.
    """
    from . import numbers

    q = _strip_complex(_norm(query))
    q = numbers.to_digits(q, anywhere=True)
    return _norm(_strip_complex(q))


def _num_key(num: str) -> str:
    """Номер дома без наращения: «30-й» и «30» — один и тот же дом.

    Буквы корпуса не трогаем: «1-а», «1-е» и «1-ж» — разные дома, поэтому
    среди окончаний нет «е», которое иначе съело бы корпус Е.
    """
    return re.sub(r'^(\d+)-(?:й|я|м|го|му|ю|х)$', r'\1', num)


def _street_key(street: str) -> list:
    """Слова улицы по корням: «4-я Советская» и «четвёртое советское» — одно.

    В названии улицы наращение всегда порядковое, корпусов там не бывает,
    поэтому окончание отрезаем любое.
    """
    keys = []
    for w in street.split():
        w = re.sub(r'^(\d+)-[а-я]{1,2}$', r'\1', w)
        keys.append(w[:6] if len(w) > 6 else w)
    return keys


def search(query: str, limit: int = 8):
    """Ищет дома по свободному тексту. Возвращает список домов, лучшие первыми."""
    q = _prepare(query)
    if not q:
        return []
    q_street, q_num = _split_addr(q)
    q_num = _num_key(q_num)
    q_keys = _street_key(q_street)
    scored = []
    for h in HOUSES:
        a = _norm(h['address'])
        street, num = _split_addr(a)
        num = _num_key(num)
        score = 0
        if a == q:
            score = 100
        elif not q_street and q_num:
            # Назвали только номер: «тридцатый дом». Если дом с таким номером
            # один — этого достаточно; если нет, спросим, какой именно
            if num == q_num:
                score = 85
            elif num.startswith(q_num):
                score = 55
        elif q_street and q_street in street:
            if q_num:
                if num == q_num:
                    score = 90
                elif num.startswith(q_num):
                    score = 60
                else:
                    continue
            else:
                score = 50
        elif q_keys and all(k in _street_key(street) for k in q_keys):
            # Улица по корням: «четвёртое советское» — та же «4-я Советская»
            if not q_num:
                score = 45
            elif num == q_num:
                score = 80
        elif q in a:
            score = 40
        else:
            # поиск по словам: все слова запроса встречаются в адресе
            words = q.split()
            if words and all(w in a for w in words):
                score = 30
        if score:
            scored.append((score, h))
    scored.sort(key=lambda t: (-t[0], t[1]['address']))
    return [h for _, h in scored[:limit]]


def detect_house(text: str):
    """Ищет упоминание дома в живой речи: «на Байкальской 237 течь в подвале».

    Номер дома должен совпасть точно, название улицы — по корню, чтобы
    пережить падежи («Байкальская» / «Байкальской»). Возвращает дом или None.
    """
    from . import numbers

    # «на четвёртой советской тридцать» — номер в речи звучит словом.
    # Здесь переводим только рядом с названием улицы: в чате хватает
    # обычного счёта, который номером дома не является
    t = _norm(numbers.to_digits(text, anywhere=True))
    if not t:
        return None
    best = None
    for h in HOUSES:
        street, num = _split_addr(_norm(h['address']))
        if not num or not street:
            continue
        # номер дома — отдельным словом, чтобы «237» не поймалось внутри «1237»
        if not re.search(rf'(?<![\w/]){re.escape(num)}(?![\w/])', t):
            continue
        # Улицу сверяем по корням слов: сказать могут «на четвёртой советской»,
        # а в справочнике записано «4-я Советская». Цифру в названии улицы
        # в речи обычно опускают («на Советской тридцать») — не требуем её,
        # различает адрес само название плюс номер дома
        keys = [k for k in _street_key(street) if not k.isdigit()]
        if keys and all(k in t for k in keys):
            # длиннее совпадение улицы — точнее адрес (65а/2 против 65а)
            if best is None or len(street) > len(best[1]):
                best = (h, street)
    return best[0] if best else None


def map_links(h) -> str:
    """Ссылки на дом в картах (2ГИС и Яндекс)."""
    lat, lng = h['lat'], h['lng']
    return (
        f'https://2gis.ru/geo/{lng},{lat}',
        f'https://yandex.ru/maps/?pt={lng},{lat}&z=18&l=map',
    )
