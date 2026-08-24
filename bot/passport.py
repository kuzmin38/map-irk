"""Сведения из чата → в паспорт дома.

В рабочем чате постоянно проговаривают то, что должно жить в паспорте:
какой розлив, где перекрывать, что за насос, где ключи. Всё это тонет в
ленте и через неделю не находится.

Теперь так: написали «в паспорт» — Люся определяет дом и раздел и
записывает. Дом назван прямо — пишет молча, не переспрашивая. Не назван —
спрашивает какой, и только тогда пишет.

Раздел выбирает модель: разложить «розлив нижний, сталь ДУ50» по двенадцати
графам регулярками невозможно. Но выбирает она из готового списка, а
текст переписать не может — в паспорт ложится сказанное человеком.
"""
import json
import logging
import re

from . import ai

log = logging.getLogger('passport')

# Прямая просьба записать. Без неё в паспорт ничего не попадает: иначе
# туда стечёт вся болтовня чата
TRIGGER = re.compile(
    r'(?<![а-я])(в\s+паспорт\w*|для\s+паспорт\w*|паспорт\w*\s+дома)(?![а-я])',
    re.IGNORECASE)

ZADANIE = (
    'Есть паспорт дома с такими разделами:\n{fields}\n\n'
    'Сообщение сантехника:\n«{text}»\n\n'
    'В какой раздел это относится? Верни строго JSON без пояснений: '
    '{{"раздел": "ключ", "текст": "сведения одной строкой"}}\n'
    'Правила:\n'
    '— ключ бери из списка выше, ничего не выдумывай;\n'
    '— если подходящего раздела нет, ставь "notes";\n'
    '— в «текст» перенеси сказанное, убрав обращения и просьбу записать. '
    'Ничего не добавляй и не додумывай, единицы и цифры сохрани как есть;\n'
    '— брань замени нейтральными словами.'
)


def wants_passport(text: str) -> bool:
    return bool(text) and bool(TRIGGER.search(text))


def strip_trigger(text: str) -> str:
    """Убирает «запиши в паспорт» — в графу должны попасть только сведения."""
    out = TRIGGER.sub(' ', text or '')
    out = re.sub(r'(?<![а-я])(запиши|запишите|сохрани|сохраните|занеси|занесите|'
                 r'внеси|внесите|добавь|добавьте|запомни|запомните)(?![а-я])',
                 ' ', out, flags=re.IGNORECASE)
    out = re.sub(r'^@?люс[яеию][\s,]*', '', out.strip(), flags=re.IGNORECASE)
    out = re.sub(r'[ \t]{2,}', ' ', out)
    return out.strip(' ,.;:—-')


async def pick_field(text: str) -> tuple[str, str] | None:
    """Раздел паспорта и очищенный текст. None — если разобрать не вышло."""
    from .handlers import PASSPORT_FIELDS

    spisok = '\n'.join(f'— {key}: {label}' for key, label in PASSPORT_FIELDS)
    otvet = await ai.ask(ZADANIE.format(fields=spisok, text=text),
                         max_tokens=300, temperature=0)
    if not otvet:
        return None
    m = re.search(r'\{.*\}', otvet, re.S)
    if not m:
        log.warning('Модель вернула не JSON: %.120s', otvet)
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None

    key = (data.get('раздел') or '').strip()
    znachenie = (data.get('текст') or '').strip()
    izvestnye = {k for k, _ in PASSPORT_FIELDS}
    if key not in izvestnye:
        key = 'notes'
    if not znachenie:
        return None
    return key, znachenie
