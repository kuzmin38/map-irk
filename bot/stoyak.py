"""Перекрыл стояк — чат узнаёт об этом сам.

Сантехник перекрывает стояк по одной квартире, а без воды остаётся весь
столб — десяток квартир сверху и снизу. Сегодня об этом узнают по звонкам
жильцов: в чате пишут «перекрыл на 65а/3», и никто не знает, кого именно
это касается.

У Люси есть шахматки: по адресу и квартире она знает этаж, номер стояка и
все квартиры этого стояка. Значит, список отключённых она составит сама —
человеку остаётся сказать, что перекрыл.

Отправку в чат подтверждает человек. Сообщение уходит бригаде и может
попасть жильцам: ошибиться в нём дороже, чем нажать одну кнопку.
"""
import re

# «Перекрыл стояк», «стояк перекрыт», «отключил стояк»
ZAKRYL = re.compile(
    r'(?<![а-я])(перекр\w+|закр\w+|отключ\w+|отсек\w+|отс[её]к)(?![а-я])',
    re.IGNORECASE)
OTKRYL = re.compile(
    r'(?<![а-я])(откр\w+|запуст\w+|пуст\w+|включ\w+|подал\w*|подали|'
    r'восстанов\w+)(?![а-я])', re.IGNORECASE)
STOYAK = re.compile(r'(?<![а-я])стоя[кч]\w*(?![а-я])', re.IGNORECASE)


def parse(text: str):
    """(«zakryl»|«otkryl», дом, квартира) или None.

    Слово «стояк» обязательно: «перекрыл кран в 105» — это не про стояк,
    и поднимать из-за такого весь чат нельзя.
    """
    from . import flats, houses, inventory

    if not text or not STOYAK.search(text):
        return None
    if ZAKRYL.search(text):
        chto = 'zakryl'
    elif OTKRYL.search(text):
        chto = 'otkryl'
    else:
        return None

    dom = houses.detect_house(text)
    if not dom:
        return None
    kvartira = flats.parse_flat(text, dom)
    if kvartira is None:
        # «перекрыл стояк на 65а/3, 105» — номер без слова «квартира»
        ostatok = inventory.ubrat_adres(text, dom)
        ostatok = STOYAK.sub(' ', ostatok)
        chisla = re.findall(r'(?<![\w/])(\d{1,4})(?![\w/])', ostatok)
        if len(chisla) != 1:
            return None
        kvartira = int(chisla[0])
    return chto, dom, kvartira


def naydi_stoyak(address: str, flat: int):
    """(адрес секции, этаж, номер стояка, квартиры стояка) или None."""
    from . import risers

    found = risers.locate(address, flat)
    if not found:
        return None
    block, addr, floor, riser, _ = found
    kvartiry = [f for _, f in risers.riser_flats(block, riser)]
    return addr, floor, riser, kvartiry


def soobschenie(dom_addr: str, flat: int, kvartiry: list, kto: str,
                kogda: str, zakryt: bool = True, skolko: str = '') -> str:
    """Текст для рабочего чата."""
    spisok = ', '.join(str(k) for k in kvartiry) if kvartiry else '—'
    if zakryt:
        return (f'🚫 Перекрыт стояк — {dom_addr}, кв. {flat}\n\n'
                f'Без холодной и горячей воды квартиры: {spisok}\n'
                f'Перекрыл: {kto}, {kogda}\n\n'
                'Как открою — напишу здесь же.')
    hvost = f'\nБыл перекрыт {skolko}.' if skolko else ''
    return (f'✅ Стояк открыт — {dom_addr}, кв. {flat}\n\n'
            f'Вода подана в квартиры: {spisok}\n'
            f'Открыл: {kto}, {kogda}.{hvost}')


def dlitelnost(minut: int) -> str:
    """«1 ч 30 мин», «45 мин»."""
    if minut < 60:
        return f'{minut} мин'
    chasy, ost = divmod(minut, 60)
    return f'{chasy} ч {ost} мин' if ost else f'{chasy} ч'
