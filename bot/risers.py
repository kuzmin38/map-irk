"""Стояки и раскладка квартир по этажам (из таблицы «СТОЯКИ квартир»).

Главная задача: по адресу и номеру квартиры сказать сантехнику этаж, номер
стояка и — самое нужное — соседей по стояку сверху и снизу.
"""
import json
import os
import re

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

with open(os.path.join(DATA_DIR, 'risers.json'), encoding='utf-8') as f:
    BLOCKS = json.load(f)

BLOCKS_BY_ID = {b['id']: b for b in BLOCKS}


def _norm(s: str) -> str:
    s = s.lower().replace('ё', 'е')
    s = re.sub(r'[.,;"]', ' ', s)
    s = re.sub(r'\b(ул|улица|мкр|микрорайон|дом|г|иркутск)\b', ' ', s)
    s = re.sub(r'\b(подъезд|под|п)\s*(\d)', r'подъезд \2', s)
    return re.sub(r'\s+', ' ', s).strip()


def find_blocks(address: str):
    """Все таблицы, подходящие под адрес: [(блок, адрес), ...].

    Дом с несколькими подъездами даёт несколько совпадений — нужную
    секцию потом определяем по номеру квартиры.
    """
    q = _norm(address)
    if not q:
        return []
    exact, partial = [], []
    for b in BLOCKS:
        for addr in b['addresses']:
            a = _norm(addr)
            if a == q:
                exact.append((b, addr))
            elif q in a or a in q:
                partial.append((b, addr))
    return exact or partial


def find_block(address: str):
    """Первая подходящая таблица: (блок, адрес) или (None, None)."""
    found = find_blocks(address)
    return found[0] if found else (None, None)


def locate(address: str, flat: int):
    """Ищет квартиру по адресу среди всех подходящих секций.
    Возвращает (блок, адрес, этаж, стояк, квартир_на_этаже) или None."""
    for b, addr in find_blocks(address):
        loc = locate_flat(b, flat)
        if loc:
            return (b, addr, *loc)
    return None


def all_addresses():
    return sorted({a for b in BLOCKS for a in b['addresses']})


def locate_flat(block, flat: int):
    """Где квартира: (этаж, номер стояка, всего стояков на этаже) или None."""
    for floor, flats in block['floors'].items():
        if flat in flats:
            return int(floor), flats.index(flat) + 1, len(flats)
    return None


def riser_flats(block, riser: int):
    """Все квартиры одного стояка снизу вверх: [(этаж, квартира), ...].

    Ряды с неполным числом квартир (обычно первый жилой этаж, где часть
    помещений нежилая) в стояк не включаем — их принадлежность по таблице
    не определить однозначно.
    """
    full = block['risers']
    out = []
    for floor in sorted(block['floors'], key=int):
        flats = block['floors'][floor]
        if len(flats) == full and riser <= len(flats):
            out.append((int(floor), flats[riser - 1]))
    return out


def partial_floors(block):
    """Этажи с неполным рядом квартир — по ним предупреждаем."""
    full = block['risers']
    return [int(f) for f, v in block['floors'].items() if len(v) != full]


def parse_query(text: str):
    """Из «Седова 65а/2 кв 47» или «65а/2 47» достаёт (адрес, номер квартиры)."""
    t = text.strip()
    m = re.search(r'(?:кв\w*\.?\s*|квартира\s*)(\d+)\s*$', t, re.I)
    if m:
        return t[:m.start()].strip(' ,-'), int(m.group(1))
    m = re.search(r'\s(\d{1,3})\s*$', t)
    if m:
        return t[:m.start()].strip(' ,-'), int(m.group(1))
    return None, None
