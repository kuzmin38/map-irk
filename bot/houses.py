"""Работа со списком домов УК: загрузка, поиск по адресу, списки по звеньям."""
import json
import os
import re

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

ZVENO_NAMES = {
    1: 'Звено 1 — Север',
    2: 'Звено 2 — Центр/ЦП',
    3: 'Звено 3 — Восток/Байкальская',
    4: 'Звено 4 — Аварийное (весь город, нерабочее время)',
}

with open(os.path.join(DATA_DIR, 'houses.json'), encoding='utf-8') as f:
    HOUSES = json.load(f)

HOUSES_BY_ID = {h['id']: h for h in HOUSES}


def _norm(s: str) -> str:
    s = s.lower().replace('ё', 'е')
    s = re.sub(r'[.,;]', ' ', s)
    s = re.sub(r'\b(ул|улица|мкр|микрорайон|д|дом|г|иркутск)\b', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


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


def by_zveno(zveno: int):
    return [h for h in HOUSES if h['zveno'] == zveno]


def map_links(h) -> str:
    """Ссылки на дом в картах (2ГИС и Яндекс)."""
    lat, lng = h['lat'], h['lng']
    return (
        f'https://2gis.ru/geo/{lng},{lat}',
        f'https://yandex.ru/maps/?pt={lng},{lat}&z=18&l=map',
    )
