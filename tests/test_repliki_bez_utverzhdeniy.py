"""Заготовки Люси в чате не должны ничего утверждать.

Реплика «Ну вот, а говорили — до вечера» выглядела как ссылка на чей-то срок.
Срока никто не называл — это была просто шутка из списка, но со стороны
неотличимо от выдумки. Такие фразы в рабочем чате дороже, чем кажутся:
человек идёт искать, кто это сказал, и перестаёт доверять всему остальному.
"""
import re

from bot import banter

# Что заготовка не имеет права утверждать: чужие слова и собственные записи
UTVERZHDENIYA = re.compile(
    r'\b(говорил[иа]?|обещал[иа]?|договаривал\w*|как и условились|'
    r'записал[аи]|сохранил[аи]|занесл[аи]|отметил[аи])\b', re.I)


def vse_repliki():
    for name, _pattern, lines in banter.TRIGGERS:
        for line in lines:
            yield name, line


def test_zagotovki_nichego_ne_utverzhdayut():
    plohie = [(n, s) for n, s in vse_repliki() if UTVERZHDENIYA.search(s)]
    assert not plohie, (
        'Заготовка утверждает то, чего не было: '
        + '; '.join(f'{n}: {s}' for n, s in plohie))


def test_pro_sroki_do_vechera_ubrano():
    """Та самая реплика, из-за которой всё началось."""
    assert all('до вечера' not in s for _n, s in vse_repliki())


def test_zavershenie_raboty_vsyo_eshchyo_hvalyat():
    """Убирая утверждения, не убрали сам повод порадоваться за бригаду."""
    banter.forget()
    otvet = banter.reply(-1, 'Давно уже запустили')
    assert otvet
    assert not UTVERZHDENIYA.search(otvet)
