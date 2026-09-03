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

# Какой стояк перекрыли. Не сказали — не выдумываем: жильцам нельзя
# объявлять, что нет горячей, если перекрыли холодную
GVS = re.compile(r'(?<![а-я])(гвс|горяч\w+)(?![а-я])', re.IGNORECASE)
HVS = re.compile(r'(?<![а-я])(хвс|холодн\w+)(?![а-я])', re.IGNORECASE)
OTOPLENIE = re.compile(r'(?<![а-я])(отоплен\w+|цо)(?![а-я])', re.IGNORECASE)


def resurs(text: str) -> str:
    """Что именно перекрыто: «вода», «горячая вода», «холодная вода», «отопление»."""
    if OTOPLENIE.search(text or ''):
        return 'отопление'
    gvs, hvs = bool(GVS.search(text or '')), bool(HVS.search(text or ''))
    if gvs and hvs:
        return 'холодная и горячая вода'
    if gvs:
        return 'горячая вода'
    if hvs:
        return 'холодная вода'
    return 'вода'


def _bez(res: str) -> str:
    """«Без воды», «Без горячей воды», «Без отопления»."""
    return {
        'вода': 'Без воды',
        'горячая вода': 'Без горячей воды',
        'холодная вода': 'Без холодной воды',
        'холодная и горячая вода': 'Без холодной и горячей воды',
        'отопление': 'Без отопления',
    }.get(res, 'Без воды')


def _podacha(res: str) -> str:
    return 'Отопление подано' if res == 'отопление' else 'Вода подана'


# Как ресурс называется в объявлении жильцам — там нужен деловой язык
_STOYAK_CHEGO = {
    'вода': 'водоснабжения',
    'горячая вода': 'горячего водоснабжения',
    'холодная вода': 'холодного водоснабжения',
    'холодная и горячая вода': 'холодного и горячего водоснабжения',
    'отопление': 'отопления',
}

_PODACHA_CHEGO = {
    'вода': 'воды',
    'горячая вода': 'горячей воды',
    'холодная вода': 'холодной воды',
    'холодная и горячая вода': 'холодной и горячей воды',
    'отопление': 'отопления',
}


def parse(text: str):
    """(«zakryl»|«otkryl», дом, квартира, ресурс) или None.

    Слово «стояк» обязательно: «перекрыл кран в 105» — это не про стояк,
    и поднимать из-за такого весь чат нельзя.

    Дом и квартира могут прийти пустыми: «открыл стояк ещё вчера, забыл
    сказать» — законная фраза, и Люся сама только что напоминала, о каком
    стояке речь. Что с этим делать, решает вызывающий.
    """
    if not text or not STOYAK.search(text):
        return None
    if ZAKRYL.search(text):
        chto = 'zakryl'
    elif OTKRYL.search(text):
        chto = 'otkryl'
    else:
        return None

    dom, kvartira = dom_i_kvartira(text)
    return chto, dom, kvartira, resurs(text)


def dom_i_kvartira(text: str):
    """(дом, квартира) из фразы. Любая часть может быть None.

    Понимает и короткий ответ на вопрос — «71 - 1», «65а/3 105»: так
    отвечают, когда Люся сама спросила адрес.
    """
    from . import flats, houses, inventory, risers

    dom = houses.detect_house(text)
    kvartira = flats.parse_flat(text, dom) if dom else None
    if dom and kvartira is None:
        # «перекрыл стояк на 65а/3, 105» — номер без слова «квартира»
        ostatok = inventory.ubrat_adres(text, dom)
        ostatok = STOYAK.sub(' ', ostatok)
        chisla = re.findall(r'(?<![\w/])(\d{1,4})(?![\w/])', ostatok)
        if len(chisla) == 1:
            kvartira = int(chisla[0])
    if kvartira is None:
        # «71 - 1» — так отвечают на вопрос, а не рассказывают
        _, kv = risers.parse_query(text)
        if kv:
            kvartira = kv
    return dom, kvartira


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
                kogda: str, zakryt: bool = True, skolko: str = '',
                res: str = 'вода') -> str:
    """Текст для рабочего чата: коротко, для своих."""
    spisok = ', '.join(str(k) for k in kvartiry) if kvartiry else '—'
    if zakryt:
        return (f'🚫 Перекрыт стояк — {dom_addr}, кв. {flat}\n\n'
                f'{_bez(res)} квартиры: {spisok}\n'
                f'Перекрыл: {kto}, {kogda}\n\n'
                'Как открою — напишу здесь же.')
    hvost = f'\nБыл перекрыт {skolko}.' if skolko else ''
    return (f'✅ Стояк открыт — {dom_addr}, кв. {flat}\n\n'
            f'{_podacha(res)} в квартиры: {spisok}\n'
            f'Открыл: {kto}, {kogda}.{hvost}')


def zhiltsam(dom_addr: str, kvartiry: list, kogda: str, zakryt: bool = True,
             res: str = 'вода', uk: str = 'Управляющая компания «Жемчужина»') -> str:
    """Текст для домового чата: жильцам, а не бригаде.

    Здесь другой читатель и другая цена ошибки. Ни имён сантехников, ни
    номера квартиры, из-за которой перекрывали: соседям знать, у кого
    авария, незачем. Зато нужно то, чего в рабочем сообщении нет, —
    что людям делать и когда ждать воду.
    """
    spisok = ', '.join(str(k) for k in kvartiry) if kvartiry else '—'
    chto = _STOYAK_CHEGO.get(res, 'водоснабжения')
    podano = _PODACHA_CHEGO.get(res, 'воды')
    if zakryt:
        prosim = ('  • закрыть краны на смесителях и бытовой технике;\n'
                  '  • не оставлять открытыми краны до возобновления подачи.')
        if res == 'отопление':
            prosim = ('  • не открывать краны на радиаторах;\n'
                      '  • сообщить нам, если в квартире станет заметно холоднее.')
        return (
            f'Уважаемые жильцы!\n\n'
            f'Сегодня в {kogda} по адресу {dom_addr} перекрыт стояк {chto} — '
            'для устранения неисправности.\n\n'
            f'Временно отключены квартиры:\n{spisok}.\n\n'
            f'Просим вас:\n{prosim}\n\n'
            'О возобновлении подачи сообщим в этом чате. '
            'Приносим извинения за доставленные неудобства.\n\n'
            f'{uk}')
    proverit = ('Просим проверить радиаторы: если батареи остаются холодными '
                'или слышен шум воздуха — напишите нам, подойдём и стравим.'
                if res == 'отопление' else
                'Просим проверить смесители и бытовую технику. Если заметите '
                'протечку — напишите нам, подойдём и устраним.')
    return (
        f'Уважаемые жильцы!\n\n'
        f'Подача {podano} по адресу {dom_addr} возобновлена в {kogda}.\n\n'
        f'{_podacha(res)} в квартиры:\n{spisok}.\n\n'
        f'{proverit}\n\n'
        f'Благодарим за терпение.\n\n{uk}')


def dlitelnost(minut: int) -> str:
    """«1 ч 30 мин», «45 мин»."""
    if minut < 60:
        return f'{minut} мин'
    chasy, ost = divmod(minut, 60)
    return f'{chasy} ч {ost} мин' if ost else f'{chasy} ч'
