"""Длительность сутками: «19 дней» вместо «456 ч».

В отчёте по чату всплыл стояк на одном из домов, перекрытый 3 сентября и
к 22-му так и не открытый. Напоминание о перекрытом стояке уходит один
раз, через четыре часа, — дальше про него не скажет никто. На экране
перекрытых стояков такая запись выглядела как «456 ч назад»: мимо этой
строки глаз проходит, а девятнадцать дней без воды по стояку — это уже
разговор с жильцами.
"""
import pytest

from bot import stoyak


@pytest.mark.parametrize('minut,ozhidaem', [
    (5, '5 мин'),
    (59, '59 мин'),
    (60, '1 ч'),
    (90, '1 ч 30 мин'),
    (23 * 60, '23 ч'),
])
def test_korotkie_sroki_kak_i_byli(minut, ozhidaem):
    assert stoyak.dlitelnost(minut) == ozhidaem


@pytest.mark.parametrize('dney,ozhidaem', [
    (1, '1 день'),
    (2, '2 дня'),
    (4, '4 дня'),
    (5, '5 дней'),
    (11, '11 дней'),
    (14, '14 дней'),
    (19, '19 дней'),
    (21, '21 день'),
    (22, '22 дня'),
])
def test_sutki_sklonyayutsya(dney, ozhidaem):
    assert stoyak.dlitelnost(dney * 24 * 60) == ozhidaem


def test_sutki_s_chasami():
    assert stoyak.dlitelnost(3 * 24 * 60 + 5 * 60) == '3 дня 5 ч'


def test_tot_samyy_stoyak():
    """Девятнадцать суток — так это и должно читаться."""
    assert stoyak.dlitelnost(19 * 24 * 60 + 30) == '19 дней'
