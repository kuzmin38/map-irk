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
    '— адрес пиши так, как он назван в тексте: «65/2», «126/3», «22»;\n'
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
