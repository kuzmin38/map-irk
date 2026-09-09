"""Улица, которую модель не узнала, превращается в цифру.

Костя сказал в видео «Трилиссера, восемь дробь два». В расшифровке вышло
«38/2 офис, который в подъезде, течёт в этом коробе» — улицу модель не
услышала вовсе, а её хвост склеила с восьмёркой. В чат ушло «В офисе 38/2
обнаружена течь». Дома 38/2 не существует.

Лечится с двух сторон: модели дают названия улиц, чтобы ей было что
узнавать, а несуществующий номер дома ловится по справочнику.
"""
import re

import pytest

from bot import houses, somneniya, transcribe
import bot.handlers as H


# ── подсказка по улицам ─────────────────────────────────────────────────

def test_ulicy_beryotsya_iz_spravochnika():
    imena = transcribe.ulicy()
    assert 'Трилиссера' in imena
    assert 'Красных Мадьяр' in imena
    assert '4-я Советская' in imena


def test_v_podskazke_net_nomerov_domov():
    """Услышав знакомый номер, модель однажды впишет его как услышанный."""
    zadanie = transcribe.zadanie()
    adresa = re.findall(r'[А-ЯЁ][а-яё]{4,}(?:-[А-ЯЁ][а-яё]+)?\s+\d{1,3}', zadanie)
    assert not adresa, f'в задании появился адрес с номером: {adresa}'


def test_zapret_podstavlyat_nesyshannoe():
    assert 'Не слышишь — не подставляй' in transcribe.zadanie()


def test_bez_domov_zadanie_ne_lomaetsya(monkeypatch):
    monkeypatch.setattr(houses, 'HOUSES', [])
    assert transcribe.zadanie() == transcribe.PROMPT


# ── несуществующий номер дома ───────────────────────────────────────────

def test_tot_samyy_nomer():
    assert somneniya.neizvestnye_nomera(
        '38/2 офис, который в подъезде, течёт в этом коробе') == ['38/2']


@pytest.mark.parametrize('text', [
    '8/2 офис течёт',
    'перекрыл стояк на 65а/3, кв. 105',
    '71/1, 105 квартира, нашёл подмес',
    'Байкальская 126/3, подвал',
])
def test_nastoyashchie_nomera_ne_trevozhat(text):
    assert somneniya.neizvestnye_nomera(text) == []


@pytest.mark.parametrize('text', [
    'труба 50 мм',
    'давление 4 атмосферы',
    'счётчик 12345678',
])
def test_ne_adresa_ne_trevozhat(text):
    assert somneniya.neizvestnye_nomera(text) == []


def test_vopros_vmesto_utverzhdeniya():
    voprosy = somneniya.proverit(
        None, 'В офисе 38/2 обнаружена течь в коробе канализационной трубы.')
    assert voprosy and '38/2' in voprosy[0]
    assert 'нет' in voprosy[0]


async def test_kartochka_otchyota_sprashivaet(monkeypatch):
    """Тот самый видеоотчёт целиком."""
    async def fake_ask(prompt, **kw):
        return ('В офисе 38/2 обнаружена течь в коробе канализационной трубы. '
                'Требуется демонтировать часть короба.')

    monkeypatch.setattr(H.ai, 'ask', fake_ask)
    text = await H.short_summary(
        ['38/2 офис, который в подъезде, течёт в этом коробе'], None)

    assert '38/2' in text and 'Какой это адрес?' in text


async def test_pravilnyy_nomer_voprosov_ne_vyzyvaet(monkeypatch):
    async def fake_ask(prompt, **kw):
        return 'В офисе 8/2 течь в коробе канализационной трубы.'

    monkeypatch.setattr(H.ai, 'ask', fake_ask)
    text = await H.short_summary(['8/2 офис течёт'], 'Трилиссера 8/2')
    assert 'Какой это адрес' not in text
