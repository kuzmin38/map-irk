"""Заготовки Люси в чате не должны утверждать того, чего не было.

Реплика «Ну вот, а говорили — до вечера» выглядела как ссылка на чей-то срок.
Срока никто не называл — это была просто шутка из списка, но со стороны
неотличимо от выдумки. Человек идёт искать, кто это сказал, не находит и
перестаёт доверять всему остальному, что Люся пишет.

Про летопись — отдельная история. Фраза заказчику нравилась, поэтому она
осталась, но теперь звучит только там, где запись правда легла в ленту дома.
"""
import re

from bot import banter, handlers

# Что заготовка не имеет права утверждать: чужие слова и чужие сроки
UTVERZHDENIYA = re.compile(
    r'\b(говорил[иа]?|обещал[иа]?|договаривал\w*|как и условились|'
    r'быстро управил\w*)\b', re.I)

# Про запись — только по делу, не в свободных заготовках
ZAPIS = re.compile(r'\b(записал[аи]|сохранил[аи]|занесл[аи]|летопис\w*)\b', re.I)


def vse_repliki():
    for name, _pattern, lines in banter.TRIGGERS:
        for line in lines:
            yield name, line


def test_zagotovki_nichego_ne_utverzhdayut():
    plohie = [(n, s) for n, s in vse_repliki() if UTVERZHDENIYA.search(s)]
    assert not plohie, (
        'Заготовка утверждает то, чего не было: '
        + '; '.join(f'{n}: {s}' for n, s in plohie))


def test_pro_zapis_tolko_v_letopisi():
    """В свободных заготовках обещаний записать быть не должно."""
    plohie = [(n, s) for n, s in vse_repliki() if ZAPIS.search(s)]
    assert not plohie, (
        'Заготовка обещает запись, которой не делает: '
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
    assert not ZAPIS.search(otvet)


def test_letopis_zvuchit_kogda_zapis_est():
    banter.forget()
    otvet = banter.reply(-1, 'Давно уже запустили', v_letopisi=True)
    assert otvet in banter.LETOPIS


def test_letopis_ne_zvuchit_bez_zapisi():
    """Сто раз подряд — и ни разу не пообещала записать."""
    for _ in range(100):
        banter.forget()
        otvet = banter.reply(-1, 'Давно уже запустили')
        assert otvet and otvet not in banter.LETOPIS


def test_letopis_ne_lezet_v_drugie_povody():
    """«Спасибо» в летопись не пишут, даже если запись по делу была."""
    banter.forget()
    otvet = banter.reply(-1, 'Спасибо, мужики!', v_letopisi=True)
    assert otvet and otvet not in banter.LETOPIS


def test_uslovie_letopisi_dom_i_rabota():
    """v_letopisi повторяет условие znachimo: без дома в летопись не попадёт."""
    assert handlers.v_letopisi('Заменили кран на Седова 71/1')
    assert not handlers.v_letopisi('Заменили кран')          # дома нет
    assert not handlers.v_letopisi('Были на Седова 71/1')    # работы нет
    assert not handlers.v_letopisi('')
