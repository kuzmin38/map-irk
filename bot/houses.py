"""Работа со списком домов УК «Жемчужина»: загрузка, поиск по адресу."""
import json
import os
import re

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

ACTIVE_FILE = os.path.join(DATA_DIR, 'active_houses.txt')


def _norm_addr(s: str) -> str:
    """Адрес в сравнимый вид: без регистра, ё, лишних пробелов и слов «ул.», «дом»."""
    s = s.lower().replace('ё', 'е')
    s = re.sub(r'[.,;]', ' ', s)
    s = re.sub(r'\b(ул|улица|мкр|микрорайон|д|дом|г|иркутск)\b', ' ', s)
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


def search(query: str, limit: int = 8):
    """Ищет дома по свободному тексту. Возвращает список домов, лучшие первыми."""
    q = _norm(query)
    if not q:
        return []
    q_street, q_num = _split_addr(q)
    scored = []
    for h in HOUSES:
        a = _norm(h['address'])
        street, num = _split_addr(a)
        score = 0
        if a == q:
            score = 100
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
    t = _norm(text)
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
        stem = street[:6] if len(street) > 6 else street
        if stem and stem in t:
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
