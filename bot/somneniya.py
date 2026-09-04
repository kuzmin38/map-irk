"""Что в отчёте не сходится со справочником — и о чём надо переспросить.

Люся выдала карточку: «4-я Советская 26 · В квартире 71 обнаружено
подтопление потолка и стояка». 4-я Советская 26 — парковка, квартир там
нет вообще; 71 — это номер дома Седова 71. Оба факта лежат в справочнике,
проверяются кодом за миллисекунду, и ни один не был проверен.

Заказчик: «Надо как-то сделать, чтобы она не несла чушь. Если непонятно —
пусть переспрашивает». Здесь и решается «непонятно»: сомнение ищет код по
справочнику и шахматкам, а не модель по ощущениям. Модель ошиблась —
значит, спрашивать должен тот, кто ошибиться не может.

Ответ — список готовых вопросов. Пустой список означает «сходится»,
и тогда Люся отвечает как обычно, без лишних уточнений.
"""
import re

from . import houses, risers

# «в квартире 71», «кв. 105», «квартира №8», «105 квартира»
KVARTIRA = re.compile(
    r'(?:кв(?:артир[аеуы]?|\.)\s*№?\s*(\d{1,4})'
    r'|(\d{1,4})\s*(?:-?я\s*)?кв(?:артир[аеуы]?|\.))(?!\s*м)', re.I)

# Откуда взялся адрес. Первые два Люся вправе печатать как факт,
# последние два — догадка, и вслух она должна её назвать догадкой
IZ_RECHI = 'речь'
IZ_PODPISI = 'подпись'
IZ_SERII = 'серия'
IZ_SOSEDNEGO = 'соседнее'
DOGADKA = (IZ_SERII, IZ_SOSEDNEGO)


def kvartiry(text: str) -> list:
    """Номера квартир, названные в тексте прямо — со словом «квартира»."""
    out = []
    for m in KVARTIRA.finditer(text or ''):
        nomer = int(m.group(1) or m.group(2))
        if nomer and nomer not in out:
            out.append(nomer)
    return out


def nezhiloy(house) -> bool:
    return bool(house) and house.get('kind') == 'nonres'


def est_kvartira(address: str, flat: int):
    """True / False / None — если шахматки на этот дом у нас нет."""
    if not risers.find_blocks(address):
        return None
    return risers.locate(address, flat) is not None


def diapazon(address: str):
    """Какие номера квартир вообще есть в доме: (первая, последняя) или None."""
    nomera = []
    for b, _addr in risers.find_blocks(address):
        for flats in b['floors'].values():
            nomera.extend(flats)
    return (min(nomera), max(nomera)) if nomera else None


def doma_s_nomerom(nomer: int) -> list:
    """Адреса, у которых номер дома — это число. «71» → «Седова 71»."""
    out = []
    for h in houses.HOUSES:
        _street, num = houses._split_addr(h['address'])
        if houses._num_key(num) == str(nomer):
            out.append(h['address'])
    return out


def pripisala(svodka: str, rech: str) -> list:
    """Квартиры, которые появились в пересказе, но словом не назывались.

    Сантехник сказал «семьдесят один» — модель написала «в квартире 71».
    Может, и квартира. А может, дом Седова 71: в речи этого не было, и
    решать за человека тут нельзя.
    """
    if not rech:
        return []
    bylo = kvartiry(rech)
    return [n for n in kvartiry(svodka) if n not in bylo]


def proverit(house, svodka: str, rech: str | None = None,
             istochnik: str = IZ_RECHI) -> list:
    """Вопросы, которые надо задать вместо того, чтобы утверждать.

    Порядок важен: сначала то, что делает неверным весь отчёт (адрес),
    потом частности. Больше двух вопросов не задаём — на третий никто
    уже не отвечает.
    """
    voprosy = []
    nomera = kvartiry(svodka)
    sami_pridumali = pripisala(svodka, rech) if rech is not None else []

    if house and nezhiloy(house) and nomera:
        # Из заметки берём только первую фразу: «Парковка. Счётчики:
        # тепло и ХВС» в вопросе не нужно целиком
        chto = (house.get('note') or 'нежилое здание').split('.')[0].strip()
        voprosy.append(
            f"❓ {house['address']} — {chto}, квартир там нет. "
            f"А в записи — квартира {nomera[0]}. Какой это дом?")
    else:
        for nomer in nomera:
            vopros = _pro_kvartiru(house, nomer, nomer in sami_pridumali)
            if vopros:
                voprosy.append(vopros)
                break

    if house and istochnik in DOGADKA and not voprosy:
        otkuda = ('из прошлых роликов' if istochnik == IZ_SERII
                  else 'из соседнего сообщения')
        voprosy.append(
            f"❓ Адрес в записи не назвали — взяла {house['address']} "
            f"{otkuda}. Если дом не тот, напишите какой.")

    return voprosy[:2]


def _pro_kvartiru(house, nomer: int, pridumali: bool) -> str | None:
    """Сомнение по одному номеру квартиры — или None, если всё сходится."""
    est = est_kvartira(house['address'], nomer) if house else None
    dom = doma_s_nomerom(nomer)
    # Голое число, которое модель сама назвала квартирой, а в справочнике
    # это номер дома. Ровно так «Седова 71» стало «квартирой 71»: шахматка
    # такую квартиру подтвердит, и ошибка пройдёт незамеченной
    if pridumali and dom:
        return (f"❓ {nomer} — это квартира или дом {dom[0]}? "
                f"В записи слова «квартира» не было.")
    if est is False:
        if dom:
            return (f"❓ {nomer} — это квартира или дом {dom[0]}? "
                    f"В доме {house['address']} квартиры {nomer} нет.")
        ran = diapazon(house['address'])
        hvost = f' — там квартиры с {ran[0]} по {ran[1]}' if ran else ''
        return (f"❓ В доме {house['address']} квартиры {nomer} "
                f"нет{hvost}. Проверьте номер.")
    if pridumali and est is not True:
        # Слова «квартира» не звучало, и подтвердить номер по шахматке нечем
        if dom:
            return f"❓ {nomer} — это квартира или дом {dom[0]}? В записи не сказано."
        return f"❓ {nomer} — это квартира? В записи слова «квартира» не было."
    return None
