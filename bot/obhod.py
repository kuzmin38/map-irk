"""Поквартирный обход: кто проверен, куда не попали, что нашли за дверью.

Заказчик: «идём ищем подмес по стояку. Сантехник сообщает, что в таких-то
квартирах мимо — значит, всё проверено, туда можно не ходить. Такие-то
квартиры ещё нужно проверить. Если обнаружен подмес, обнаружен гигдуш,
обнаружен водогрей — вот эту всю информацию она должна фиксировать. Потом,
если будут заявки о подмесе, мы уже будем знать, где что находится».

Отчёт пишут на ходу и всяк по-своему: то строками («105 - обнаружен
незакрытый гиг душ»), то через запятую («41 мимо, 1 мимо»). Регуляркой
такое не разберёшь, поэтому работа поделена так же, как в ночном разборе:

— дом определяет код (houses.detect_house) — ошибка здесь дороже всего;
— по квартирам раскладывает модель: где «мимо», а где «не живут», решать
  ей, живой речи это и касается;
— пишет опять код, и только те квартиры, которые в доме есть по шахматке.

Что осталось обойти — не спрашиваем ни у кого: стояк целиком известен из
шахматки, вычитаем то, что уже обошли.
"""
import json
import logging
import re

from . import ai, db, flats, risers

log = logging.getLogger('obhod')

# Строка обхода: номер квартиры, а следом — что там. «123 - нет дома»,
# «41 мимо», «25 завтра в 8 00 предоставят доступ»
STROKA = re.compile(r'^\s*(\d{1,4})\s*[-–—:.)]?\s+(\S.*)$')

# Меньше трёх квартир — это обычное сообщение про находку, его разбирает
# flats. Обход начинается там, где перечисляют
MINIMUM = 3

PROVERENO = 'проверено'
NET_DOSTUPA = 'нет доступа'

# Что стоит за дверью и что важно помнить: заказчик просил про гигиенический
# душ и водонагреватель прямо. Это не дефект, но именно из-за них потом ищут
# подмес — и знать, где они есть, нужно заранее
OBORUDOVANIE = (
    (('гигиенич', 'гиг душ', 'гигдуш', 'гиг. душ'), 'гигиенический душ'),
    (('водонагрев', 'водогре', 'бойлер'), 'водонагреватель'),
    (('полотенцесуш', 'пушка'), 'полотенцесушитель'),
)

ZADANIE = (
    'Ниже — отчёт сантехника управляющей компании о поквартирном обходе '
    'одного дома: где он был и что там. Писал на ходу, короткими '
    'строками.\n\n{text}\n\n'
    'Разложи по квартирам. Верни строго JSON без пояснений:\n'
    '{{"квартиры": [{{"кв": номер, "итог": "проверено|не попал|находка", '
    '"что": "..."}}]}}\n'
    'Правила:\n'
    '— номер квартиры бери только тот, что назван в отчёте. Дату, время, '
    'номер дома, номер стояка и количество чего-либо квартирой не считай;\n'
    '— «мимо», «гул отсутствует», «жалоб нет», «всё чисто», «ничего нет» — '
    'это «проверено»: сантехник там был и ничего не нашёл;\n'
    '— «нет дома», «не живут», «не открыли», «закрыто», «завтра дадут '
    'доступ», «ждёт к такому-то часу» — это «не попал»: квартиру ещё '
    'предстоит проверить;\n'
    '— «обнаружен подмес», «стоит водонагреватель», «есть гигиенический '
    'душ», «течь» — это «находка»: то, что нашли за дверью;\n'
    '— «что» — одна короткая деловая строка о том, что в отчёте написано. '
    'Отрицание не переворачивай: «гигиенического душа нет» так и пиши, это '
    'не находка, а проверено;\n'
    '— время и договорённость о доступе сохрани в «что», если они названы: '
    'по ним человек и пойдёт второй раз;\n'
    '— ничего не придумывай: квартиры, которой в отчёте нет, быть не '
    'должно. Не уверен в номере — пропусти его;\n'
    '— если это не обход по квартирам, верни пустой список.'
)


def pohozh(text: str) -> bool:
    """Похоже ли сообщение на перечисление квартир.

    Дешёвая проверка формы, чтобы не гонять модель на каждую реплику
    чата. Ошибётся в плюс — модель вернёт пустой список, и ничего не
    случится.
    """
    return len(_nomera(text)) >= MINIMUM


def _nomera(text: str) -> list:
    """Числа, с которых начинаются куски отчёта, — в порядке появления."""
    out = []
    for kusok in re.split(r'[\n;,.]+', text or ''):
        m = STROKA.match(kusok)
        if m:
            out.append(int(m.group(1)))
    return out


async def razobrat(text: str) -> list:
    """Квартиры отчёта: [{'кв': 105, 'итог': 'находка', 'что': '...'}]."""
    otvet = await ai.ask(ZADANIE.format(text=text[:4000]),
                         max_tokens=1200, temperature=0)
    if not otvet:
        log.warning('Модель не разобрала обход')
        return []
    m = re.search(r'\{.*\}', otvet, re.S)
    if not m:
        log.warning('Разбор обхода вернулся не JSON: %.150s', otvet)
        return []
    try:
        dannye = json.loads(m.group(0))
    except ValueError:
        log.warning('JSON обхода не читается: %.150s', m.group(0))
        return []
    return [z for z in (dannye.get('квартиры') or []) if isinstance(z, dict)]


def vid(itog: str, chto: str) -> str:
    """Ключ записи: по нему видно повтор и по нему же ищут «где водогрей»."""
    if itog == 'не попал':
        return NET_DOSTUPA
    if itog != 'находка':
        return PROVERENO
    nizhe = (chto or '').lower().replace('ё', 'е')
    for korni, slovo in OBORUDOVANIE:
        if any(k.replace('ё', 'е') in nizhe for k in korni):
            return slovo
    nahodka = flats.nahodka(chto)
    return flats.kind_of(nahodka) if nahodka else 'находка'


def sohranit(house, zapisi: list, uid=None, uname=None) -> dict:
    """Пишет квартиры обхода в карточки. Возвращает разложенное по итогам.

    Квартиру, которой в доме нет, не пишем вовсе: шахматка знает состав
    дома, а выдуманный номер осядет в карточке и через полгода прочтётся
    как факт.
    """
    from . import somneniya

    itogi = {'проверено': [], 'не попал': [], 'находка': [], 'мимо_дома': []}
    for z in zapisi:
        try:
            kv = int(z.get('кв'))
        except (TypeError, ValueError):
            continue
        if not 1 <= kv <= 2000:
            continue
        if somneniya.est_kvartira(house['address'], kv) is False:
            itogi['мимо_дома'].append(kv)
            continue
        itog = (z.get('итог') or '').strip()
        if itog not in ('проверено', 'не попал', 'находка'):
            itog = 'проверено'
        chto = (z.get('что') or '').strip()
        kind = vid(itog, chto)
        itogi[itog].append((kv, chto))
        if db.flat_note_exists(house['id'], kv, kind):
            continue        # тот же обход прислали дважды — это бывает
        db.add_flat_note(house['id'], kv, chto or itog, kind=kind,
                         author_id=uid, author=uname)
    log.info('Обход %s: проверено %s, не попали %s, находок %s',
             house['address'], len(itogi['проверено']),
             len(itogi['не попал']), len(itogi['находка']))
    return itogi


def ostalos(house, oboshli: list) -> list | None:
    """Квартиры того же стояка, куда ещё не заходили. None — стояк не один.

    Ради этого и затевалось: «такие-то квартиры ещё нужно проверить».
    Считаем по шахматке, а не по памяти — и учитываем прошлые дни: обход
    стояка редко укладывается в один заход.
    """
    if not oboshli:
        return None
    mesta = set()
    blok = None
    for kv in oboshli:
        loc = risers.locate(house['address'], kv)
        if not loc:
            return None
        b, _addr, _etazh, stoyak, _na_etazhe = loc
        mesta.add((id(b), stoyak))
        blok = b
    if len(mesta) != 1:
        return None         # обходят не стояк, а что-то своё — не считаем
    _bid, stoyak = mesta.pop()
    vse = [kv for _etazh, kv in risers.riser_flats(blok, stoyak)]
    proverennye = set(oboshli) | _proverennye_ranshe(house, vse)
    return [kv for kv in vse if kv not in proverennye]


def _proverennye_ranshe(house, stoyak_kvartiry: list) -> set:
    """Квартиры стояка, по которым запись уже есть. «Нет доступа» не в счёт —
    туда как раз и надо вернуться."""
    est = set()
    for z in db.flat_notes(house['id'], limit=300):
        if z['flat'] in stoyak_kvartiry and z['kind'] != NET_DOSTUPA:
            est.add(z['flat'])
    return est


def svodka(house, itogi: dict) -> str:
    """Что сказать в чат. Коротко: список номеров, находки — строкой."""
    stroki = [f"🧭 Обход — {house['address']}"]

    provereno = [kv for kv, _chto in itogi['проверено']]
    if provereno:
        stroki.append(f"✅ Проверено ({len(provereno)}): "
                      f"{', '.join(str(k) for k in provereno)}")

    if itogi['находка']:
        stroki.append(f"📌 Нашли ({len(itogi['находка'])}):")
        for kv, chto in itogi['находка']:
            stroki.append(f'   • кв. {kv} — {chto}')

    if itogi['не попал']:
        chasti = []
        for kv, chto in itogi['не попал']:
            chasti.append(f'{kv} ({chto})' if chto else str(kv))
        stroki.append(f"🚪 Не попали ({len(itogi['не попал'])}): "
                      f"{', '.join(chasti)}")

    if itogi.get('мимо_дома'):
        nomera = ', '.join(str(k) for k in itogi['мимо_дома'])
        stroki.append(f"❓ В этом доме нет квартир {nomera} — их не записала. "
                      'Проверьте номера.')

    oboshli = provereno + [kv for kv, _ in itogi['находка']]
    hvost = ostalos(house, oboshli)
    if hvost:
        stroki += ['', f"⏳ По этому стояку осталось ({len(hvost)}): "
                       f"{', '.join(str(k) for k in hvost)}"]
    elif hvost == []:
        stroki += ['', '🎉 Стояк обошли целиком.']
    return '\n'.join(stroki)
