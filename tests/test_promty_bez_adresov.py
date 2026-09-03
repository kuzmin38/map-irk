"""В заданиях модели не должно быть настоящих адресов.

В инструкции на расшифровку стояли примеры «квартира 47», «Байкальская
237» — и модель переписала их в расшифровку как услышанное. В отчёте по
Красных Мадьяр 14 появились две заявки по дому, которого мы вообще не
обслуживаем.

Это худший класс ошибок: выдумка попадает в ленту, в паспорт дома и в
отчёт руководителю, и отличить её от правды человеку уже нечем.
"""
import re

import pytest

from bot import agent, announce, passport, plan, razbor, transcribe
import bot.handlers as H


def zadaniya():
    """Все задания, которые уходят модели."""
    return {
        'расшифровка': transcribe.PROMPT,
        'разбор плана': plan.ZADANIE,
        'раздел паспорта': passport.ZADANIE,
        'объявление жильцам': announce.ZADANIE,
        'правка объявления': announce.ZADANIE_PRAVKI,
        'пересказ отчёта': H.SUMMARY_RULES,
        'разбор дня': razbor.ZADANIE,
    }


# Улица с номером: «Байкальская 237», «Седова 65а/2», «Трилиссера 8/5»
ADRES = re.compile(r'[А-ЯЁ][а-яё]{4,}(?:-[А-ЯЁ][а-яё]+)?\s+\d{1,3}[а-я]?(?:/\d+)?')


@pytest.mark.parametrize('imya', list(zadaniya()))
def test_v_zadanii_net_ulic_s_nomerami(imya):
    text = zadaniya()[imya]

    naydeno = ADRES.findall(text)

    assert naydeno == [], f'в задании «{imya}» настоящий адрес: {naydeno}'


@pytest.mark.parametrize('imya', list(zadaniya()))
def test_v_zadanii_net_nomerov_kvartir(imya):
    text = zadaniya()[imya]

    naydeno = re.findall(r'квартира\s+\d+|кв\.\s*\d+', text, re.IGNORECASE)

    assert naydeno == [], f'в задании «{imya}» номер квартиры: {naydeno}'


def test_rasshifrovka_zapreschaet_dodumyvat():
    """Прямой запрет — второй рубеж после убранных примеров."""
    p = transcribe.PROMPT

    assert 'Ничего не добавляй от себя' in p
    assert 'адресов' in p
    assert 'цифрами' in p, 'числа всё ещё нужны цифрами'


def test_zadaniya_ne_soderzhat_nashih_domov():
    """Ни один настоящий адрес из справочника не должен встречаться."""
    from bot import houses

    for imya, text in zadaniya().items():
        for h in houses.ALL_HOUSES:
            assert h['address'] not in text, \
                f'в задании «{imya}» дом {h["address"]}'
