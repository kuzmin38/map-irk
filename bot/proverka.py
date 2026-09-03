"""Проверка записей на чужие адреса.

В инструкции на расшифровку стояли примеры с настоящим адресом, и модель
переписала их как услышанное: в отчёте появились заявки по дому, которого
мы не обслуживаем. Инструкцию поправили, но записи с выдумкой остались в
ленте и в выгрузке.

Здесь поиск таких записей: берём из текста всё, что похоже на «улица
номер», и оставляем то, чего нет в справочнике. Решает человек — код
только показывает.
"""
import re

# Номер дома: «237», «65а/2», «8/1». Улицу ищем перед ним словами
NOMER = re.compile(r'(?<![\w/])(\d{1,3}[а-я]?(?:\s*/\s*\d+)?)(?![\w/])')
SLOVO = re.compile(r'([А-ЯЁ][а-яё]{3,}(?:-[А-ЯЁ][а-яё]+)?)\s*$')

# Слова, которые пишут с заглавной, но улицами они не являются
NE_ULITSA = {'дом', 'квартира', 'подъезд', 'этаж', 'стояк', 'офис', 'подвал',
             'кабинет', 'помещение', 'поступили', 'напоминаю', 'просьба',
             'участок', 'секция', 'корпус', 'строение', 'литер', 'заявки',
             'работы', 'показания', 'счётчик', 'счетчик', 'манометр'}


VYDUMKA = 'нет в справочнике'
NE_V_RABOTE = 'не в работе'


def v_spravochnike(kusok: str):
    """Дом из полного справочника (87 домов), даже если он не в работе.

    «Байкальская 237» в справочнике есть, просто участок не наш. Это не
    выдумка модели, а чужой дом — и сказать об этом надо иначе.
    """
    from . import houses

    n = houses._norm(kusok)
    for h in houses.ALL_HOUSES:
        if houses._norm(h['address']) == n:
            return h
    return None


def chuzhie_adresa(text: str) -> list:
    """[(адрес, вид)] — что похоже на адрес и не наше.

    Вид: «нет в справочнике» — почти наверняка модель дописала сама;
    «не в работе» — дом чужого участка, такое бывает и по делу.

    Улица бывает из двух слов («Красных Мадьяр»), поэтому перед номером
    смотрим два слова, а не одно: иначе «Мадьяр 14» не опознается и попадёт
    в список чужих, хотя дом наш.
    """
    from . import houses

    if not text:
        return []
    out = []
    for m in NOMER.finditer(text):
        nomer = m.group(1).strip()
        do = text[:m.start()].rstrip()
        slova = []
        for _ in range(2):
            w = SLOVO.search(do)
            if not w:
                break
            slova.insert(0, w.group(1))
            do = do[:w.start()].rstrip()
        if not slova or slova[-1].lower() in NE_ULITSA:
            continue
        varianty = [f"{' '.join(slova[i:])} {nomer}" for i in range(len(slova))]
        if any(houses.detect_house(v) for v in varianty):
            continue
        dom = next((v_spravochnike(v) for v in varianty if v_spravochnike(v)), None)
        kusok = dom['address'] if dom else varianty[0]
        vid = NE_V_RABOTE if dom else VYDUMKA
        if (kusok, vid) not in out:
            out.append((kusok, vid))
    return out


def podozritelnye(records) -> list:
    """[(запись, [чужие адреса])] — только те, где что-то нашлось."""
    out = []
    for r in records:
        text = ' '.join(x for x in ((r['text'] or ''), (r['transcript'] or '')) if x)
        naydeno = chuzhie_adresa(text)
        if naydeno:
            out.append((r, naydeno))
    return out
