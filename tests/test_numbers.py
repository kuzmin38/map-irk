"""Номера домов и квартир в расшифровке — цифрами.

Просьба проектного менеджера: по такому тексту можно искать номер и его
видно с первого взгляда. При этом обычный счёт в речи трогать нельзя.
"""
import pytest

from bot.numbers import to_digits


@pytest.mark.parametrize('skazano, ozhidaem', [
    # квартиры — самое частое в отчётах
    ('квартира сорок семь', 'квартира 47'),
    ('в квартире сорок семь', 'в квартире 47'),
    ('сорок седьмая квартира', '47-я квартира'),
    ('в сорок седьмой квартире', 'в 47-й квартире'),
    ('кв сорок семь', 'кв 47'),
    ('кв. сорок семь', 'кв. 47'),
    # дома, в том числе трёхзначные
    ('дом двести тридцать семь', 'дом 237'),
    ('Байкальская дом двести тридцать семь', 'Байкальская дом 237'),
    ('в доме сто восемнадцать', 'в доме 118'),
    ('шестьдесят пятый дом', '65-й дом'),
    # прочая техника — тот же смысл, что и у дома с квартирой
    ('второй подъезд', '2-й подъезд'),
    ('на девятом этаже', 'на 9-м этаже'),
    ('третий стояк', '3-й стояк'),
    ('корпус два', 'корпус 2'),
    ('номер триста двенадцать', 'номер 312'),
])
def test_nomera_stanovyatsya_ciframi(skazano, ozhidaem):
    assert to_digits(skazano) == ozhidaem


@pytest.mark.parametrize('skazano', [
    'приезжал один раз',
    'в первую очередь надо перекрыть',
    'два дня никто не приходил',
    'сделали за сорок минут',
    'течёт уже третий день',
    'семь жалоб за вечер',
])
def test_obychnyy_schyot_ne_trogaem(skazano):
    assert to_digits(skazano) == skazano


@pytest.mark.parametrize('skazano, ozhidaem', [
    ('Байкальская двести тридцать семь', 'Байкальская 237'),
    ('на Байкальскую двести тридцать семь', 'на Байкальскую 237'),
    ('по Волгоградской семьдесят пять', 'по Волгоградской 75'),
    ('Седова шестьдесят пять', 'Седова 65'),
    ('Розы Люксембург сто восемнадцать', 'Розы Люксембург 118'),
])
def test_adres_bez_slova_dom(skazano, ozhidaem):
    """В речи «дом» почти всегда опускают — улица сама указывает на номер."""
    assert to_digits(skazano) == ozhidaem


def test_ulica_posle_chisla_ne_schitaetsya_adresom():
    """Номер дома идёт после названия, а не перед ним."""
    assert to_digits('три дня на Седова') == 'три дня на Седова'


def test_celaya_fraza_iz_otchyota():
    skazano = ('Приехал на Байкальскую двести тридцать семь, квартира сорок семь, '
               'второй подъезд. Течь в первой комнате, перекрыл второй стояк, '
               'сверху в пятьдесят четвёртой тоже мокро.')
    assert to_digits(skazano) == (
        'Приехал на Байкальскую 237, квартира 47, '
        '2-й подъезд. Течь в первой комнате, перекрыл 2-й стояк, '
        'сверху в пятьдесят четвёртой тоже мокро.')


def test_pustaya_stroka_i_none():
    assert to_digits('') == ''
    assert to_digits(None) is None


def test_uzhe_cifry_ostayutsya_kak_est():
    assert to_digits('квартира 47, дом 237') == 'квартира 47, дом 237'


async def test_rasshifrovka_prohodit_cherez_preobrazovanie(monkeypatch, tmp_path):
    """Связка: то, что вернула модель, доезжает до чата уже с цифрами."""
    from bot import transcribe

    class FakeResp:
        status = 200

        async def json(self):
            return {'choices': [{'message': {
                'content': 'Байкальская двести тридцать семь, квартира сорок семь'}}]}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeSession:
        def post(self, *a, **k):
            return FakeResp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    audio = tmp_path / 'report.mp3'
    audio.write_bytes(b'fake audio')
    monkeypatch.setattr(transcribe.ai, 'enabled', lambda: True)
    monkeypatch.setattr(transcribe.aiohttp, 'ClientSession', lambda *a, **k: FakeSession())

    assert await transcribe.transcribe_file(str(audio)) == 'Байкальская 237, квартира 47'


def test_zapros_k_modeli_tozhe_prosit_cifry():
    """Две защиты вместо одной: и просьба модели, и своё преобразование."""
    from bot.transcribe import PROMPT

    assert 'цифрами' in PROMPT
