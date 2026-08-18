"""Поверка манометра вводится так, как написано на приборе.

Заказчик: «там стоит поверка июль двадцать шестого года, они на два года,
значит в июле двадцать восьмого нужна поверка». Бот раньше требовал полную
дату срока годности — считать в уме приходилось человеку.
"""
import pytest

from bot.handlers import VERIFY_YEARS, parse_verify


@pytest.mark.parametrize('kleymo', ['июль 2026', 'июля 2026', 'Июль 2026',
                                    '07.2026', '7.26', '07 2026'])
def test_kleymo_pribavlyaet_interval(kleymo):
    """Июль 2026 плюс два года — годен до конца июля 2028."""
    data, kak = parse_verify(kleymo)

    assert data == '2028-07-31'
    assert '2 года' in kak, 'человек должен видеть, как посчитано'


@pytest.mark.parametrize('srok, ozhidaem', [
    ('до 07.2028', '2028-07-31'),
    ('до июля 2028', '2028-07-31'),
    ('до 25.09.2028', '2028-09-25'),
    ('25.09.2027', '2027-09-25'),
])
def test_srok_godnosti_beryotsya_kak_est(srok, ozhidaem):
    """«До ...» и полная дата — это уже срок, прибавлять ничего не нужно."""
    data, kak = parse_verify(srok)

    assert data == ozhidaem
    assert kak == '', 'ничего не досчитывали — и пояснять нечего'


def test_neizvestnaya_poverka():
    assert parse_verify('-') == (None, '')
    assert parse_verify('—') == (None, '')


@pytest.mark.parametrize('chush', ['завтра', '13.2026', 'абырвалг 2026', '2026'])
def test_neponyatnoe_otvergaetsya(chush):
    with pytest.raises(ValueError):
        parse_verify(chush)


def test_interval_dva_goda():
    assert VERIFY_YEARS == 2


def test_kleymo_i_srok_dayut_odno_i_to_zhe():
    """«Июль 2026» и «до июля 2028» — про один и тот же прибор."""
    assert parse_verify('июль 2026')[0] == parse_verify('до июля 2028')[0]
